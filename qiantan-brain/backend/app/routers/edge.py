"""Edge device sync API router — receives offline-cached records."""

import logging
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.core.security import get_merchant_id
from app.schemas.edge import EdgeIngestResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/edge", tags=["edge"])


@router.post("/ingest", response_model=EdgeIngestResponse)
async def ingest_edge_record(
    merchant_id: uuid.UUID = Depends(get_merchant_id),
    body: dict = Body(...),
):
    """
    Accept a record pushed from an edge device.

    The edge device queues records locally (offline SQLite) and POSTs
    them here once connectivity is restored. Merchant identity comes only
    from the Authorization Bearer token (or dev fallback header), never
    from the request body.
    """
    # Defense-in-depth: if the body also carries merchant_id, it must match
    # the authenticated merchant. This prevents a malicious client from using
    # a valid token for merchant A while pushing data for merchant B.
    body_merchant_id = body.get("merchant_id")
    if body_merchant_id:
        try:
            if uuid.UUID(body_merchant_id) != merchant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="merchant_id in body does not match authenticated merchant",
                )
        except (ValueError, TypeError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid merchant_id in body",
            ) from exc

    detections = body.get("detections", [])
    weight = body.get("weight_g")
    logger.info(
        "edge ingest: merchant=%s detections=%d weight_g=%s",
        merchant_id,
        len(detections),
        weight,
    )
    return {
        "code": 0,
        "data": {
            "accepted": True,
            "merchant_id": str(merchant_id),
            "detection_count": len(detections),
            "weight_g": weight,
        },
    }
