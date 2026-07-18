"""ONNX Runtime vision model service.

Loads an ONNX detection model, keeps CPU-bound inference off the async
event loop via ``run_in_executor``, and handles missing-model files
gracefully (``is_available = False``) rather than crashing on import.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

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
        2. Resize + letterbox to model input dimensions.
        3. Normalise to [0, 1].
        4. Run ONNX session.
        5. Non-Maximum Suppression (simple IoU threshold).
        6. Map surviving boxes to ``Detection`` dicts.
        """
        # ``recognize()`` only dispatches here when ``is_available`` is True,
        # which is set iff the ONNX session loaded successfully.
        assert self._session is not None

        # --- decode ---------------------------------------------------
        from PIL import Image as PILImage

        image = PILImage.open(__import__("io").BytesIO(image_bytes)).convert("RGB")

        # --- preprocess ------------------------------------------------
        input_tensor = self._preprocess(image)

        # --- infer -----------------------------------------------------
        outputs = self._session.run(None, {self._input_name: input_tensor})
        raw_boxes: np.ndarray = outputs[0]  # shape [N, 6] or [1, N, 6]

        # Handle batched output — take first batch item
        if raw_boxes.ndim == 3:
            raw_boxes = raw_boxes[0]  # [1, N, 6] → [N, 6]

        if raw_boxes.size == 0:
            return []

        # --- postprocess -----------------------------------------------
        detections = self._postprocess(raw_boxes, image.size)
        return detections

    def _preprocess(self, image: Image.Image) -> np.ndarray:
        """Resize, letterbox, normalise → (1, 3, H, W) float32 CHW."""
        import numpy as np
        from PIL import Image as PILImage

        target_w, target_h = self._input_size

        # Resize with aspect-ratio preserving letterbox
        img_w, img_h = image.size
        scale = min(target_w / img_w, target_h / img_h)
        new_w, new_h = int(img_w * scale), int(img_h * scale)
        resized = image.resize((new_w, new_h), PILImage.Resampling.BILINEAR)

        # Centre on a black canvas
        canvas = PILImage.new("RGB", (target_w, target_h), (0, 0, 0))
        offset_x = (target_w - new_w) // 2
        offset_y = (target_h - new_h) // 2
        canvas.paste(resized, (offset_x, offset_y))

        # HWC → CHW, uint8 → float32, normalise to [0, 1]
        arr = np.array(canvas, dtype=np.float32)
        arr = arr.transpose((2, 0, 1))  # HWC → CHW
        arr /= 255.0
        arr = np.expand_dims(arr, axis=0)  # 1,3,H,W
        return arr

    def _postprocess(
        self, raw_boxes: np.ndarray, original_size: tuple[int, int]
    ) -> list[Detection]:
        """Convert raw model output to ``Detection`` dicts.

        Expected *raw_boxes* format: [x1, y1, x2, y2, confidence, class_id]
        or [cx, cy, w, h, confidence, class_id] depending on model export.

        This implementation assumes YOLO-style xyxy format.
        """
        target_w, target_h = self._input_size
        orig_w, orig_h = original_size
        scale = min(target_w / orig_w, target_h / orig_h)
        pad_x = (target_w - orig_w * scale) / 2
        pad_y = (target_h - orig_h * scale) / 2

        # --- confidence filter ----------------------------------------
        mask = raw_boxes[:, 4] >= self._confidence_threshold
        boxes = raw_boxes[mask]
        if boxes.size == 0:
            return []

        # --- rescale from letterbox coords back to original image -----
        boxes[:, 0] = (boxes[:, 0] - pad_x) / scale
        boxes[:, 1] = (boxes[:, 1] - pad_y) / scale
        boxes[:, 2] = (boxes[:, 2] - pad_x) / scale
        boxes[:, 3] = (boxes[:, 3] - pad_y) / scale

        # --- simple NMS (IoU threshold 0.45) ---------------------------
        kept = self._nms(boxes, iou_threshold=0.45)

        detections: list[Detection] = []
        for idx in kept:
            conf = float(boxes[idx, 4])
            class_id = int(boxes[idx, 5])
            detections.append(
                Detection(
                    product_id=class_id,
                    name=f"product_{class_id}",
                    confidence=round(conf, 4),
                )
            )
        detections.sort(key=lambda d: d["confidence"], reverse=True)
        return detections

    @staticmethod
    def _nms(boxes: np.ndarray, iou_threshold: float = 0.45) -> list[int]:
        """Greedy Non-Maximum Suppression on xyxy boxes.

        Returns indices of boxes to keep, ordered by descending confidence.
        """
        import numpy as np

        if boxes.shape[0] == 0:
            return []

        # Sort by confidence descending
        order = boxes[:, 4].argsort()[::-1]
        kept: list[int] = []

        while order.size > 0:
            current = order[0]
            kept.append(int(current))

            if order.size == 1:
                break

            # IoU of current vs rest
            x1 = np.maximum(boxes[current, 0], boxes[order[1:], 0])
            y1 = np.maximum(boxes[current, 1], boxes[order[1:], 1])
            x2 = np.minimum(boxes[current, 2], boxes[order[1:], 2])
            y2 = np.minimum(boxes[current, 3], boxes[order[1:], 3])

            inter_w = np.maximum(0.0, x2 - x1)
            inter_h = np.maximum(0.0, y2 - y1)
            inter_area = inter_w * inter_h

            area_current = (boxes[current, 2] - boxes[current, 0]) * (
                boxes[current, 3] - boxes[current, 1]
            )
            area_rest = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (
                boxes[order[1:], 3] - boxes[order[1:], 1]
            )
            union = area_current + area_rest - inter_area
            iou = inter_area / np.maximum(union, 1e-7)

            keep_mask = iou <= iou_threshold
            order = order[1:][keep_mask]

        return kept
