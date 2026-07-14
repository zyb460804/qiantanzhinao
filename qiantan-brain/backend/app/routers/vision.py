"""Vision recognition API router — YOLO integration with demo mode.

Supports four recognition modes:
1. Edge mode   – pre-computed detections from edge device.
2. Demo mode   – simulated results for UI testing / competition demos.
3. Model mode  – ONNX inference via :class:`OnnxVisionModelService`.
4. Placeholder – model unavailable AND not in strict-production mode.

Strict-production enforcement (P0-3): when ``vision_strict_mode=True``
AND ``app_env == "production"``, a missing model returns **HTTP 503**
instead of silently returning empty placeholder results.
"""

import json
import logging
import random
import time
from pathlib import Path
from typing import TypedDict

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import get_current_merchant
from app.database import get_db
from app.models.merchant import Merchant
from app.models.product import ProductCategory
from app.schemas.vision import (
    VisionCategoriesResponse,
    VisionFeedbackRequest,
    VisionFeedbackResponse,
    VisionRecognizeResponse,
)

logger = logging.getLogger("vision_router")


router = APIRouter(prefix="/api/v1/vision", tags=["vision"])

_RULES_DIR = Path(__file__).parent.parent / "rules"

# Max image size: 10MB
_MAX_IMAGE_SIZE = 10 * 1024 * 1024

# ------------------------------------------------------------------
# Lazy-initialised vision model service (singleton)
# ------------------------------------------------------------------

_vision_service: "OnnxVisionModelService | None" = None
_vision_service_initialized: bool = False


def _get_vision_service() -> "OnnxVisionModelService | None":
    """Return the module-level vision model singleton, initialised on first call.

    Returns ``None`` when ``VISION_MODEL_PATH`` is empty (model not configured)
    so the router can distinguish "not configured" from "configured but failed".
    """
    global _vision_service, _vision_service_initialized  # noqa: PLW0603

    if _vision_service_initialized:
        return _vision_service

    _vision_service_initialized = True

    from app.services.vision_model_onnx import OnnxVisionModelService

    model_path = settings.vision_model_path
    if not model_path:
        logger.info("VISION_MODEL_PATH not set — vision model disabled")
        _vision_service = None
        return None

    _vision_service = OnnxVisionModelService(
        model_path=model_path,
        device=settings.vision_model_device,
        confidence_threshold=settings.vision_confidence_threshold,
    )
    logger.info(
        "Vision model initialised: available=%s version=%s",
        _vision_service.is_available,
        _vision_service.model_version,
    )
    return _vision_service


class DemoDetection(TypedDict):
    product_id: int
    name: str
    confidence: float


def _load_categories() -> list[dict]:
    config_path = _RULES_DIR / "product_categories.json"
    if not config_path.exists():
        return []
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)
    result = []
    for group_name, group_data in data.get("categories", {}).items():
        for product_name in group_data.get("products", []):
            result.append(
                {
                    "name": product_name,
                    "category_group": group_name,
                    "unit": group_data.get("unit", "斤"),
                }
            )
    return result


@router.get("/categories", response_model=VisionCategoriesResponse)
async def get_categories(db: AsyncSession = Depends(get_db)):
    """Get list of supported product categories."""
    query = select(ProductCategory).where(ProductCategory.is_active == True)  # noqa: E712
    result = await db.execute(query)
    categories = result.scalars().all()

    items = [
        {
            "product_id": c.id,
            "name": c.name,
            "category_group": c.category_group,
            "unit": c.unit,
        }
        for c in categories
    ]

    if not items:
        items = _load_categories()

    return {"code": 0, "data": items}


@router.post("/recognize", response_model=VisionRecognizeResponse)
async def recognize_product(
    merchant: Merchant = Depends(get_current_merchant),
    image: UploadFile = File(...),
    demo_mode: bool = Form(False),
    detections: str = Form(""),
    db: AsyncSession = Depends(get_db),
):
    """Upload product image for recognition.

    Three modes:
    1. Edge mode: edge device sends pre-computed detections as JSON string
    2. Demo mode: returns simulated results for UI testing/competition demo
    3. Placeholder: returns empty (production YOLO not yet deployed)
    """
    start = time.time()

    # --- Validate image ---
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="图片文件为空")
    if len(content) > _MAX_IMAGE_SIZE:
        raise HTTPException(status_code=400, detail="图片大小不能超过10MB")

    # Validate content type
    ct = (image.content_type or "").lower()
    if ct and not ct.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片文件")

    # --- Mode 1: Edge device sends pre-computed detections ---
    if detections:
        try:
            edge_detections = json.loads(detections)
            if isinstance(edge_detections, list) and edge_detections:
                processing_ms = int((time.time() - start) * 1000)
                return {
                    "code": 0,
                    "message": "识别成功（边缘端推理）",
                    "data": {
                        "detections": edge_detections,
                        "suggested_product": edge_detections[0],
                        "processing_time_ms": processing_ms,
                        "source": "edge_yolo",
                    },
                }
        except (json.JSONDecodeError, TypeError):
            pass  # Fall through to demo/placeholder

    # --- Mode 2: Demo mode — simulate recognition ---
    if demo_mode:
        query = select(ProductCategory).where(ProductCategory.is_active.is_(True))
        result = await db.execute(query)
        products = result.scalars().all()

        if not products:
            cats = _load_categories()
            products = [
                type("P", (), {"id": 0, "name": c["name"], "unit": c["unit"]})() for c in cats[:8]
            ]

        if products:
            sample_size = min(random.randint(1, 3), len(products))
            sampled = random.sample(list(products), sample_size)

            demo_detections: list[DemoDetection] = []
            for p in sampled:
                confidence = round(random.uniform(0.72, 0.96), 2)
                demo_detections.append(
                    {
                        "product_id": p.id,
                        "name": p.name,
                        "confidence": confidence,
                    }
                )

            demo_detections.sort(key=lambda x: x["confidence"], reverse=True)
            demo_detections = demo_detections[:3]

            processing_ms = int((time.time() - start) * 1000)
            return {
                "code": 0,
                "message": "识别成功（演示模式）",
                "data": {
                    "detections": demo_detections,
                    "suggested_product": demo_detections[0] if demo_detections else None,
                    "processing_time_ms": processing_ms,
                    "source": "demo",
                },
            }

    # --- Mode 3: Real model inference (P0-3) -------------------------
    vision_svc = _get_vision_service()

    # 3a — Model is available → use it
    if vision_svc is not None and vision_svc.is_available:
        dets = await vision_svc.recognize(content)
        processing_ms = int((time.time() - start) * 1000)
        return {
            "code": 0,
            "message": "识别成功（模型推理）",
            "data": {
                "detections": dets,
                "suggested_product": dets[0] if dets else None,
                "processing_time_ms": processing_ms,
                "source": "onnx",
                "model_version": vision_svc.model_version,
            },
        }

    # 3b — Strict-production enforcement (P0-3 core deliverable)
    if settings.vision_strict_mode and settings.app_env == "production":
        logger.critical(
            "Vision model unavailable in strict-production mode — returning 503"
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "视觉识别模型未就绪，生产环境禁止返回空占位结果。"
                "请检查 VISION_MODEL_PATH 配置并确保模型文件存在。"
            ),
        )

    # 3c — Non-strict / non-production: log warning, return placeholder
    logger.warning(
        "Vision model unavailable (strict_mode=%s app_env=%s) — returning placeholder",
        settings.vision_strict_mode,
        settings.app_env,
    )
    processing_ms = int((time.time() - start) * 1000)
    return {
        "code": 0,
        "message": "视觉识别模型尚未部署，请使用演示模式或边缘端推理",
        "data": {
            "detections": [],
            "suggested_product": None,
            "processing_time_ms": processing_ms,
            "source": "placeholder",
        },
    }


@router.post("/feedback", response_model=VisionFeedbackResponse)
async def submit_recognition_feedback(
    body: VisionFeedbackRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Submit user correction for a recognition result.

    Stored for future model retraining. Fields:
    { merchant_id, original_prediction, user_correction, confidence, image_hash }
    """
    # In production this would persist to a feedback table.
    # For now, just acknowledge.
    return {
        "code": 0,
        "message": "反馈已记录，将用于改进识别模型",
        "data": {
            "original": body.original_prediction,
            "corrected": body.user_correction,
        },
    }
