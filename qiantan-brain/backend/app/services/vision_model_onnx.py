"""ONNX Runtime vision model service.

Loads an ONNX detection model, keeps CPU-bound inference off the async
event loop via ``run_in_executor``, and handles missing-model files
gracefully (``is_available = False``) rather than crashing on import.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from app.services.vision_model import Detection


# numpy / PIL / onnxruntime are heavy and optional (no model in CI / dev):
# imported lazily inside methods at runtime, and only under TYPE_CHECKING
# here so annotations like ``np.ndarray`` / ``Image.Image`` resolve for static
# analysis without forcing a hard install-time dependency.
if TYPE_CHECKING:
    import numpy as np
    import onnxruntime as ort
    from PIL import Image

logger = logging.getLogger("vision_model_onnx")

# Letterbox canvas fill — Ultralytics YOLO training pipeline and the edge
# engine (edge/vision/inference.py) both pad with 114 gray; using black (0)
# here would shift border-pixel statistics vs. what the model was trained on.
_LETTERBOX_FILL = (114, 114, 114)

# Greedy NMS IoU threshold (same value as edge/vision/inference.py).
_NMS_IOU_THRESHOLD = 0.45


class DecodedDetection(TypedDict):
    """Fully decoded detection with pixel bbox (pre API-level trimming)."""

    product_id: int
    name: str
    confidence: float
    bbox: list[float]  # x, y, w, h (top-left + size, original-image pixels)


def letterbox_params(
    original_size: tuple[int, int], input_size: tuple[int, int]
) -> tuple[float, float, float]:
    """Aspect-preserving letterbox parameters — ``(scale, pad_x, pad_y)``.

    Uses exactly the same math as the edge engine
    (``edge/vision/inference.py::_preprocess``), so backend and edge map
    coordinates identically:

    * ``scale = min(tw / ow, th / oh)``
    * ``new_w, new_h = int(ow * scale), int(oh * scale)``
    * float pads centring the resized image on the canvas

    Forward (original → letterbox): ``x_l = x_o * scale + pad_x``.
    Inverse (letterbox → original): ``x_o = (x_l - pad_x) / scale``.
    """
    orig_w, orig_h = original_size
    target_w, target_h = input_size
    scale = min(target_w / orig_w, target_h / orig_h)
    new_w, new_h = int(orig_w * scale), int(orig_h * scale)
    pad_x = (target_w - new_w) / 2
    pad_y = (target_h - new_h) / 2
    return scale, pad_x, pad_y


def decode_yolo_output(
    output: np.ndarray,
    *,
    original_size: tuple[int, int],
    input_size: tuple[int, int],
    confidence_threshold: float,
    iou_threshold: float = _NMS_IOU_THRESHOLD,
) -> list[DecodedDetection]:
    """Decode Ultralytics YOLOv8/v11 ONNX export output into detections.

    ``ml/train_yolo.py`` exports via ``model.export(format="onnx")``, whose
    prediction layout is ``[1, 4 + nc, num_boxes]`` (e.g. ``[1, 19, 8400]``
    for 15 classes at 640px).  Rows are ``(cx, cy, w, h, cls_0 ... cls_{nc-1})``
    in letterbox-pixel coordinates; class scores are already activated.

    Mirrors ``edge/vision/inference.py::_postprocess``:

    1. drop batch dim → ``[4 + nc, num_boxes]``; transpose → per-box rows
    2. best class per box via ``argmax`` over the class-score columns
    3. drop boxes whose best score is below ``confidence_threshold``
    4. greedy NMS (xyxy IoU) in letterbox space
    5. inverse letterbox map to original-image ``xywh``, clipped to bounds
    6. sort by descending confidence
    """
    import numpy as np

    if output.ndim == 3:
        output = output[0]  # [1, 4+nc, num_boxes] → [4+nc, num_boxes]
    if output.ndim != 2:
        logger.warning("Unexpected YOLO output ndim=%s — no detections", output.ndim)
        return []

    preds = output.T  # [num_boxes, 4+nc]
    num_classes = preds.shape[1] - 4
    if num_classes <= 0:
        logger.warning(
            "YOLO output has %s channels — need >= 5 (4 box + >= 1 class)",
            preds.shape[1],
        )
        return []

    boxes = preds[:, :4]  # cx, cy, w, h (letterbox pixels)
    class_scores = preds[:, 4:]

    class_ids = np.argmax(class_scores, axis=1)
    max_scores = np.max(class_scores, axis=1)

    # --- confidence filter (per-box best class score) ----------------
    mask = max_scores >= confidence_threshold
    boxes = boxes[mask]
    class_ids = class_ids[mask]
    max_scores = max_scores[mask]
    if boxes.shape[0] == 0:
        return []

    # --- greedy NMS in letterbox space (cxcywh → xyxy) ----------------
    xyxy = np.empty_like(boxes)
    xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    keep = _greedy_nms(xyxy, max_scores, iou_threshold)
    boxes = boxes[keep]
    class_ids = class_ids[keep]
    max_scores = max_scores[keep]

    # --- inverse letterbox → original-image xywh ----------------------
    orig_w, orig_h = original_size
    scale, pad_x, pad_y = letterbox_params(original_size, input_size)

    detections: list[DecodedDetection] = []
    for (cx, cy, w, h), class_id, score in zip(boxes, class_ids, max_scores, strict=True):
        cx_o = (float(cx) - pad_x) / scale
        cy_o = (float(cy) - pad_y) / scale
        w_o = float(w) / scale
        h_o = float(h) / scale
        x = max(0.0, min(float(orig_w), cx_o - w_o / 2))
        y = max(0.0, min(float(orig_h), cy_o - h_o / 2))
        w_o = max(0.0, min(float(orig_w) - x, w_o))
        h_o = max(0.0, min(float(orig_h) - y, h_o))
        detections.append(
            DecodedDetection(
                product_id=int(class_id),
                name=f"product_{int(class_id)}",
                confidence=round(float(score), 4),
                bbox=[x, y, w_o, h_o],
            )
        )
    detections.sort(key=lambda d: d["confidence"], reverse=True)
    return detections


def _greedy_nms(boxes_xyxy: np.ndarray, scores: np.ndarray, iou_threshold: float) -> list[int]:
    """Greedy Non-Maximum Suppression on xyxy boxes + per-box scores.

    Returns indices of boxes to keep (into ``boxes_xyxy`` / ``scores``),
    ordered by descending confidence.
    """
    import numpy as np

    if boxes_xyxy.shape[0] == 0:
        return []

    order = scores.argsort()[::-1]
    areas = (boxes_xyxy[:, 2] - boxes_xyxy[:, 0]) * (boxes_xyxy[:, 3] - boxes_xyxy[:, 1])
    kept: list[int] = []

    while order.size > 0:
        current = order[0]
        kept.append(int(current))

        if order.size == 1:
            break

        # IoU of current vs rest
        x1 = np.maximum(boxes_xyxy[current, 0], boxes_xyxy[order[1:], 0])
        y1 = np.maximum(boxes_xyxy[current, 1], boxes_xyxy[order[1:], 1])
        x2 = np.minimum(boxes_xyxy[current, 2], boxes_xyxy[order[1:], 2])
        y2 = np.minimum(boxes_xyxy[current, 3], boxes_xyxy[order[1:], 3])

        inter = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        union = areas[current] + areas[order[1:]] - inter
        iou = inter / np.maximum(union, 1e-7)

        order = order[1:][iou <= iou_threshold]

    return kept


class OnnxVisionModelService:
    """ONNX-Runtime-based product recognition service.

    Parameters
    ----------
    model_path:
        Filesystem path to the ``.onnx`` model file.
    device:
        ``"cpu"`` (default) or ``"cuda"``.
    confidence_threshold:
        Minimum confidence score (0.0-1.0); detections below this are
        dropped.
    input_size:
        (width, height) the model expects.  Default ``(640, 640)`` matches
        YOLOv8/YOLOv11 export convention.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def __init__(
        self,
        model_path: str,
        device: str = "cpu",
        confidence_threshold: float = 0.5,
        input_size: tuple[int, int] = (640, 640),
    ) -> None:
        self._model_path = model_path
        self._device = device
        self._confidence_threshold = confidence_threshold
        self._input_size: tuple[int, int] = input_size
        self._available: bool = False
        self._model_version: str = "unavailable"
        self._input_name: str = ""
        self._session: ort.InferenceSession | None = None

        if not model_path or not Path(model_path).exists():
            logger.warning(
                "Vision model file not found at %r — is_available=False",
                model_path,
            )
            return

        try:
            import onnxruntime as ort  # noqa: F811 – re-import for clarity
        except ImportError:
            logger.warning("onnxruntime not installed — is_available=False")
            return

        try:
            providers = (
                ["CUDAExecutionProvider", "CPUExecutionProvider"]
                if device == "cuda"
                else ["CPUExecutionProvider"]
            )
            self._session = ort.InferenceSession(model_path, providers=providers)
            self._input_name = self._session.get_inputs()[0].name
            self._available = True

            # Deterministic model version from file hash (first 12 hex chars)
            file_bytes = Path(model_path).read_bytes()
            self._model_version = hashlib.sha256(file_bytes).hexdigest()[:12]

            logger.info(
                "ONNX model loaded: path=%r device=%r version=%s",
                model_path,
                device,
                self._model_version,
            )
        except Exception as exc:
            logger.exception("Failed to load ONNX model from %r: %s", model_path, exc)
            self._available = False
            self._session = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return self._available

    @property
    def model_version(self) -> str:
        return self._model_version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def recognize(self, image_bytes: bytes) -> list[Detection]:
        """Run inference, keeping work off the async event loop.

        Returns an empty list when the model is not available — callers
        should check ``is_available`` first and decide their own fallback
        behaviour (e.g. return HTTP 503 in strict production mode).
        """
        if not self._available:
            return []
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run_inference, image_bytes)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_inference(self, image_bytes: bytes) -> list[Detection]:
        """Synchronous inference pipeline (runs in thread-pool).

        Steps
        -----
        1. Decode JPEG/PNG via Pillow.
        2. Letterbox to model input dimensions, normalise to [0, 1].
        3. Run ONNX session → ``[1, 4+nc, num_boxes]``.
        4. ``decode_yolo_output``: class scores + NMS + inverse letterbox.
        5. Map to ``Detection`` dicts.
        """
        # ``recognize()`` only dispatches here when ``is_available`` is True,
        # which is set iff the ONNX session loaded successfully.
        assert self._session is not None

        # --- decode ---------------------------------------------------
        from PIL import Image as PILImage

        image = PILImage.open(io.BytesIO(image_bytes)).convert("RGB")

        # --- preprocess ------------------------------------------------
        input_tensor = self._preprocess(image)

        # --- infer -----------------------------------------------------
        outputs = self._session.run(None, {self._input_name: input_tensor})

        # --- postprocess -----------------------------------------------
        decoded = decode_yolo_output(
            outputs[0],
            original_size=image.size,
            input_size=self._input_size,
            confidence_threshold=self._confidence_threshold,
        )
        return [
            Detection(
                product_id=d["product_id"],
                name=d["name"],
                confidence=d["confidence"],
            )
            for d in decoded
        ]

    def _preprocess(self, image: Image.Image) -> np.ndarray:
        """Letterbox, normalise → (1, 3, H, W) float32 CHW.

        Shares :func:`letterbox_params` with :func:`decode_yolo_output` so
        the forward transform and the inverse map cannot drift apart.
        """
        import numpy as np
        from PIL import Image as PILImage

        target_w, target_h = self._input_size
        img_w, img_h = image.size
        scale, pad_x, pad_y = letterbox_params(image.size, self._input_size)
        # Same expression as inside letterbox_params → identical new_w/new_h.
        new_w, new_h = int(img_w * scale), int(img_h * scale)
        resized = image.resize((new_w, new_h), PILImage.Resampling.BILINEAR)

        # Centre on the standard YOLO 114-gray canvas
        canvas = PILImage.new("RGB", (target_w, target_h), _LETTERBOX_FILL)
        canvas.paste(resized, (int(pad_x), int(pad_y)))

        # HWC → CHW, uint8 → float32, normalise to [0, 1]
        arr = np.array(canvas, dtype=np.float32)
        arr = arr.transpose((2, 0, 1))  # HWC → CHW
        arr /= 255.0
        arr = np.expand_dims(arr, axis=0)  # 1,3,H,W
        return arr
