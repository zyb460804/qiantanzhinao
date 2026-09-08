"""Unit tests for the ONNX vision pipeline.

Covers the three fixes landed with this file:

* H7 — ``decode_yolo_output`` parses the real Ultralytics export layout
  ``[1, 4+nc, num_boxes]`` (per-box class scores, cxcywh, inverse
  letterbox) instead of the old (never produced) ``[N, 6]`` format.
* H5 — router lazy-init uses double-checked ``asyncio.Lock`` +
  ``asyncio.to_thread``: exactly one construction under concurrency.
* L1 — ``GET /api/v1/vision/categories`` requires authentication.
"""

import asyncio
import io
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image


sys.path.insert(0, str(Path(__file__).parent.parent))

from app.routers import vision as vision_router
from app.services.vision_model_onnx import (
    OnnxVisionModelService,
    decode_yolo_output,
    letterbox_params,
)


# ---------------------------------------------------------------------------
# Synthetic Ultralytics-layout tensors
# ---------------------------------------------------------------------------

INPUT_SIZE = (640, 640)  # (w, h) — YOLOv8/v11 export convention
NUM_CLASSES = 3
NUM_BOXES = 8400  # real anchor count for YOLOv8n @ 640px
CONF_THRESHOLD = 0.5
ORIG_SIZE = (1280, 720)  # (w, h) of the "original photo"
# letterbox for this pair: scale = 0.5, pad = (0.0, 140.0)


def _build_output(
    entries: list[tuple[float, float, float, float, int, float]],
) -> np.ndarray:
    """Build a ``[1, 4+nc, N]`` tensor in Ultralytics export layout.

    ``entries`` are ``(cx, cy, w, h, class_id, score)`` in letterbox pixels;
    every unused anchor gets uniform 1e-4 background noise (below threshold).
    """
    preds = np.full((NUM_BOXES, 4 + NUM_CLASSES), 1e-4, dtype=np.float32)
    for i, (cx, cy, w, h, cid, score) in enumerate(entries):
        preds[i, :4] = (cx, cy, w, h)
        preds[i, 4:] = 1e-4
        preds[i, 4 + cid] = score
    return preds.T[np.newaxis, :]


# Target A: orig box (200, 100, 160, 120), class 1, conf 0.92
#   orig centre (280, 160) → letterbox centre (140, 220), wh (80, 60)
_T_A = (140.0, 220.0, 80.0, 60.0, 1, 0.92)
# Exact duplicate of A with a lower score → NMS must suppress it
_T_A_DUP = (140.0, 220.0, 80.0, 60.0, 1, 0.85)
# Target B: orig box (600, 300, 200, 200), class 2, conf 0.77
_T_B = (350.0, 340.0, 100.0, 100.0, 2, 0.77)
# Target C: orig box (1200, 300, 200, 200) overflows the right edge → clipped
_T_C = (650.0, 340.0, 100.0, 100.0, 0, 0.88)
# Below the 0.5 threshold → dropped before NMS
_T_LOW = (100.0, 100.0, 50.0, 50.0, 2, 0.3)


def _decode(output: np.ndarray) -> list[dict]:
    return decode_yolo_output(
        output,
        original_size=ORIG_SIZE,
        input_size=INPUT_SIZE,
        confidence_threshold=CONF_THRESHOLD,
    )


# ---------------------------------------------------------------------------
# H7 — postprocessing
# ---------------------------------------------------------------------------


def test_decode_recovers_classes_confidences_and_coordinates():
    dets = _decode(_build_output([_T_A, _T_A_DUP, _T_B, _T_C, _T_LOW]))

    # Dup suppressed by NMS, low-confidence dropped, survivors sorted desc.
    assert len(dets) == 3
    assert [d["product_id"] for d in dets] == [1, 0, 2]
    assert [d["confidence"] for d in dets] == [0.92, 0.88, 0.77]
    assert [d["name"] for d in dets] == ["product_1", "product_0", "product_2"]

    # Inverse letterbox recovers the original-image boxes.
    a, c, b = dets
    assert a["bbox"] == pytest.approx([200.0, 100.0, 160.0, 120.0], abs=1e-3)
    assert b["bbox"] == pytest.approx([600.0, 300.0, 200.0, 200.0], abs=1e-3)
    # Right-edge overflow clipped to image bounds (same as edge engine).
    assert c["bbox"] == pytest.approx([1200.0, 300.0, 80.0, 200.0], abs=1e-3)


def test_decode_accepts_squeezed_2d_layout():
    output = _build_output([_T_A])
    dets = _decode(output[0])  # [4+nc, N] without the batch dim
    assert len(dets) == 1
    assert dets[0]["product_id"] == 1
    assert dets[0]["bbox"] == pytest.approx([200.0, 100.0, 160.0, 120.0], abs=1e-3)


def test_decode_all_below_threshold_returns_empty():
    assert _decode(_build_output([_T_LOW])) == []


@pytest.mark.parametrize(
    "bad",
    [
        np.zeros((4, 10), dtype=np.float32),  # 4 channels → no class scores
        np.zeros((1, 2, 10), dtype=np.float32),  # fewer than 4+1 channels
    ],
)
def test_decode_rejects_malformed_shapes(bad):
    assert _decode(bad) == []


@pytest.mark.parametrize(
    "ow,oh",
    [(1280, 720), (720, 1280), (1001, 333), (333, 1001), (640, 640), (1920, 1080)],
)
def test_letterbox_params_match_edge_engine_math(ow, oh):
    """Backend letterbox math must equal edge/vision/inference.py::_preprocess."""
    # Replicates the edge formulas verbatim (ratio / int-truncated size / float pad)
    ratio = min(640 / ow, 640 / oh)
    new_w, new_h = int(ow * ratio), int(oh * ratio)
    pad_w = (640 - new_w) / 2
    pad_h = (640 - new_h) / 2

    scale, pad_x, pad_y = letterbox_params((ow, oh), (640, 640))
    assert scale == ratio
    assert pad_x == pad_w
    assert pad_y == pad_h


def test_preprocess_shape_dtype_and_letterbox_fill():
    svc = OnnxVisionModelService(model_path="")
    tensor = svc._preprocess(Image.new("RGB", ORIG_SIZE, (200, 30, 90)))

    assert tensor.shape == (1, 3, 640, 640)
    assert tensor.dtype == np.float32
    assert 0.0 <= tensor.min() and tensor.max() <= 1.0
    # scale 0.5, pad (0, 140): row 0 is canvas fill (YOLO-standard 114 gray)
    np.testing.assert_allclose(tensor[0, :, 0, 5], 114 / 255)
    # row 140 is the first image row (all one solid colour)
    np.testing.assert_allclose(tensor[0, :, 140, 5], np.array([200, 30, 90]) / 255)


# ---------------------------------------------------------------------------
# Service-level no-model behaviour (regression guard)
# ---------------------------------------------------------------------------


async def test_unavailable_service_recognize_returns_empty():
    svc = OnnxVisionModelService(model_path="")
    assert svc.is_available is False
    assert await svc.recognize(b"\xff\xd8\xff\xd9") == []


class _FakeSession:
    """Minimal stand-in returning a canned prediction tensor."""

    def __init__(self, output: np.ndarray) -> None:
        self._output = output

    def run(self, _output_names, _feed):
        return [self._output]


async def test_recognize_end_to_end_with_fake_session(monkeypatch):
    """Full pipeline (decode → preprocess → session → decode → Detection)."""
    svc = OnnxVisionModelService(model_path="")
    assert svc.is_available is False

    output = _build_output([_T_A, _T_A_DUP, _T_B])
    monkeypatch.setattr(svc, "_session", _FakeSession(output))
    monkeypatch.setattr(svc, "_available", True)
    monkeypatch.setattr(svc, "_input_name", "images")

    buf = io.BytesIO()
    Image.new("RGB", ORIG_SIZE, (10, 200, 30)).save(buf, format="JPEG")

    dets = await svc.recognize(buf.getvalue())
    assert dets == [
        {"product_id": 1, "name": "product_1", "confidence": 0.92},
        {"product_id": 2, "name": "product_2", "confidence": 0.77},
    ]


# ---------------------------------------------------------------------------
# H5 — router lazy-init concurrency
# ---------------------------------------------------------------------------


def _reset_lazy_state(monkeypatch) -> None:
    monkeypatch.setattr(vision_router, "_vision_service", None)
    monkeypatch.setattr(vision_router, "_vision_service_initialized", False)
    monkeypatch.setattr(vision_router, "_vision_service_lock", asyncio.Lock())


async def test_vision_service_constructed_once_under_concurrency(monkeypatch):
    """Racing first callers must produce exactly one service construction."""
    calls: list[str] = []

    def fake_init(
        self,
        model_path,
        device="cpu",
        confidence_threshold=0.5,
        input_size=(640, 640),
    ):
        calls.append(model_path)
        time.sleep(0.02)  # simulate the slow ONNX session load
        self._available = False
        self._model_version = "unavailable"

    monkeypatch.setattr(OnnxVisionModelService, "__init__", fake_init)
    monkeypatch.setattr(vision_router.settings, "vision_model_path", "X:/nonexistent/model.onnx")
    _reset_lazy_state(monkeypatch)

    results = await asyncio.gather(*(vision_router._get_vision_service() for _ in range(8)))

    assert len(calls) == 1
    assert all(svc is results[0] for svc in results)


async def test_vision_service_none_when_model_path_empty(monkeypatch):
    """Empty VISION_MODEL_PATH → None (placeholder mode), never constructs."""
    calls: list[str] = []
    monkeypatch.setattr(
        OnnxVisionModelService,
        "__init__",
        lambda self, *args, **kwargs: calls.append("init"),
    )
    monkeypatch.setattr(vision_router.settings, "vision_model_path", "")
    _reset_lazy_state(monkeypatch)

    assert await vision_router._get_vision_service() is None
    assert await vision_router._get_vision_service() is None  # fast path
    assert calls == []


# ---------------------------------------------------------------------------
# L1 — /categories authentication
# ---------------------------------------------------------------------------


async def test_categories_requires_authentication(auth_client):
    """No token + no fallback header → 401, not an open category dump."""
    resp = await auth_client.get("/api/v1/vision/categories")
    assert resp.status_code == 401
