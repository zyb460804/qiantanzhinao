"""Integration tests for /api/v1/vision — product recognition API.

Covers: categories list, demo mode recognition, empty image validation,
edge device detections passthrough, and placeholder (no-model) mode.
"""

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

import io
import json

import pytest
import pytest_asyncio
from tests.conftest import TEST_MERCHANT_ID, TEST_PRODUCT_ID


pytestmark = pytest.mark.asyncio

# Minimal JPEG bytes (SOI + EOI markers) — enough to pass non-empty check.
_IMG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


# ------------------------------------------------------------------
# GET /api/v1/vision/categories
# ------------------------------------------------------------------


async def test_get_categories(client, db_session):
    """Categories endpoint returns the 4 seeded products."""
    resp = await client.get("/api/v1/vision/categories")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert isinstance(data["data"], list)
    assert len(data["data"]) == 4
    # Verify structure
    for item in data["data"]:
        assert "product_id" in item
        assert "name" in item
        assert "category_group" in item
        assert "unit" in item


# ------------------------------------------------------------------
# POST /api/v1/vision/recognize — demo mode
# ------------------------------------------------------------------


async def test_recognize_demo_mode(client, db_session):
    """Demo mode returns simulated detections from seeded products."""
    files = {"image": ("test.jpg", io.BytesIO(_IMG_BYTES), "image/jpeg")}
    data = {"merchant_id": TEST_MERCHANT_ID, "demo_mode": "true"}

    resp = await client.post("/api/v1/vision/recognize", files=files, data=data)
    assert resp.status_code == 200
    result = resp.json()
    assert result["code"] == 0
    assert result["data"]["source"] == "demo"
    assert isinstance(result["data"]["detections"], list)
    assert len(result["data"]["detections"]) >= 1
    assert result["data"]["suggested_product"] is not None
    # Each detection has the expected fields
    for det in result["data"]["detections"]:
        assert "product_id" in det
        assert "name" in det
        assert "confidence" in det


# ------------------------------------------------------------------
# POST /api/v1/vision/recognize — empty image
# ------------------------------------------------------------------


async def test_recognize_empty_image(client, db_session):
    """Empty image file returns 400."""
    files = {"image": ("test.jpg", io.BytesIO(b""), "image/jpeg")}
    data = {"merchant_id": TEST_MERCHANT_ID}

    resp = await client.post("/api/v1/vision/recognize", files=files, data=data)
    assert resp.status_code == 400


# ------------------------------------------------------------------
# POST /api/v1/vision/recognize — edge device detections
# ------------------------------------------------------------------


async def test_recognize_edge_detections(client, db_session):
    """Edge device sends pre-computed detections — passthrough."""
    edge_detections = json.dumps(
        [
            {"product_id": 1, "name": "白菜", "confidence": 0.95},
            {"product_id": 2, "name": "土豆", "confidence": 0.82},
        ]
    )
    files = {"image": ("test.jpg", io.BytesIO(_IMG_BYTES), "image/jpeg")}
    data = {
        "merchant_id": TEST_MERCHANT_ID,
        "detections": edge_detections,
    }

    resp = await client.post("/api/v1/vision/recognize", files=files, data=data)
    assert resp.status_code == 200
    result = resp.json()
    assert result["code"] == 0
    assert result["data"]["source"] == "edge_yolo"
    assert len(result["data"]["detections"]) == 2
    assert result["data"]["detections"][0]["name"] == "白菜"
    assert result["data"]["suggested_product"]["name"] == "白菜"


# ------------------------------------------------------------------
# POST /api/v1/vision/recognize — placeholder (no model)
# ------------------------------------------------------------------


async def test_recognize_placeholder_mode(client, db_session):
    """Non-demo, non-edge mode returns empty detections (placeholder)."""
    files = {"image": ("test.jpg", io.BytesIO(_IMG_BYTES), "image/jpeg")}
    data = {"merchant_id": TEST_MERCHANT_ID}  # no demo_mode, no detections

    resp = await client.post("/api/v1/vision/recognize", files=files, data=data)
    assert resp.status_code == 200
    result = resp.json()
    assert result["code"] == 0
    assert result["data"]["source"] == "placeholder"
    assert result["data"]["detections"] == []
    assert result["data"]["suggested_product"] is None
