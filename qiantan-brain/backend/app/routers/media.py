"""Media upload API (§5.9, §5.10) — idempotent file upload with JWT auth.

Supports the offline media queue: images (purchase certs, waste photos,
stocktake photos), audio (voice notes), and documents.

§5.10 文件和对象存储规范:
  - 校验 MIME、扩展名、大小
  - 文件名使用不可预测 UUID
  - 禁止把对象存储密钥返回给小程序
  - 凭证类文件记录上传人、商户、关联业务和保留期限
"""

from __future__ import annotations

import json as _json
import os
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import get_current_merchant
from app.database import get_db

# Ensure model table is registered with Base.metadata before create_all
from app.models.media import MediaFile  # noqa: E402, F811
from app.models.merchant import Merchant
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/media", tags=["media"])

# media_type 枚举（审计 H-2 fail-closed）：Form 参数用 Literal 校验，未知值直接 422；
# 下方两个白名单字典的键必须与 Literal 的取值保持一致（miss 时按拒绝处理）。

# Allowed MIME types per media type
MIME_WHITELIST: dict[str, set[str]] = {
    "image": {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
    },
    "audio": {
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/aac",
        "audio/ogg",
        "audio/amr",  # WeChat voice recording
    },
    "document": {
        "application/pdf",
        "image/jpeg",
        "image/png",  # photos of documents
    },
}

MAX_FILE_SIZE_MB = 20  # Max per-file size

# Allowed file extensions per media type (defense-in-depth with MIME check).
# Extension comes from the client filename, so this is a first-line filter;
# the MIME whitelist below is a second check (although Content-Type is also
# client-controlled, both layers together raise the bar).
_EXT_WHITELIST: dict[str, set[str]] = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"},
    "audio": {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".amr"},
    "document": {".pdf", ".jpg", ".jpeg", ".png"},
}


@router.post("/upload", response_model=AnyResponse)
async def upload_media(
    file: UploadFile = File(...),
    media_type: Literal["image", "audio", "document"] = Form(default="image"),
    business_type: str = Form(default="other"),
    business_payload: str = Form(default="{}"),
    idempotency_key: str = Form(default=""),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """Upload a media file with idempotency protection (§5.9, §5.10).

    Returns 200 for new upload, 409 for duplicate (same idempotency_key).
    The client uses this to safely retry failed uploads.
    """
    import mimetypes

    # 1. Check idempotency
    if idempotency_key:
        existing = (
            await db.execute(
                select(MediaFile).where(
                    MediaFile.merchant_id == merchant.id,
                    MediaFile.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing:
            return {
                "code": 409,
                "message": "文件已上传（幂等）",
                "data": {
                    "file_id": str(existing.id),
                    "stored_name": existing.stored_name,
                    "duplicate": True,
                },
            }

    # 2. Validate extension (first-line filter; ext comes from client filename)
    #    修复（审计 H-2 fail-open）：字典 miss（空集）按「拒绝」处理，
    #    任何文件必须命中白名单才接受，杜绝未知 media_type 绕过校验落盘。
    ext = (os.path.splitext(file.filename or "file")[1] or "").lower()
    allowed_exts = _EXT_WHITELIST.get(media_type)
    if not allowed_exts or ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"禁止的文件扩展名: {ext or '(无)'}",
        )

    # 3. Validate MIME (defense-in-depth; Content-Type is also client-controlled)
    mime = (
        file.content_type
        or mimetypes.guess_type(file.filename or "")[0]
        or "application/octet-stream"
    )
    allowed_mimes = MIME_WHITELIST.get(media_type)
    if not allowed_mimes or mime not in allowed_mimes:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {mime}。允许的类型: {', '.join(sorted(allowed_mimes))}",
        )

    # 4. Validate size with chunked read (prevents OOM from oversized payloads)
    max_size = MAX_FILE_SIZE_MB * 1024 * 1024
    contents = bytearray()
    while chunk := await file.read(64 * 1024):
        if len(contents) + len(chunk) > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"文件大小不能超过 {MAX_FILE_SIZE_MB}MB",
            )
        contents.extend(chunk)
    contents = bytes(contents)

    # 5. Generate UUID-based filename (ext already validated above; default to .bin)
    stored_name = f"{uuid.uuid4()}{ext or '.bin'}"

    # 6. Write to upload dir (dev-local; production should use object storage)
    upload_dir = os.path.join(settings.upload_dir, merchant.id.hex)
    os.makedirs(upload_dir, exist_ok=True)
    file_path = os.path.join(upload_dir, stored_name)
    with open(file_path, "wb") as f:
        f.write(contents)

    # 7. Parse business payload
    payload: dict[str, Any] = {}
    try:
        payload = _json.loads(business_payload) if business_payload else {}
    except _json.JSONDecodeError:
        pass

    # 8. Determine retention (certificates keep longer)
    retention_days = None
    if business_type in ("purchase_cert", "quality_cert", "inspection_cert"):
        retention_days = 365 * 2  # 2 years for compliance docs
    elif business_type in ("waste_photo", "stocktake_photo"):
        retention_days = 90

    # 9. Save record
    record = MediaFile(
        merchant_id=merchant.id,
        original_name=file.filename or "unknown",
        stored_name=stored_name,
        media_type=media_type,
        business_type=business_type,
        business_payload=payload,
        mime_type=mime,
        file_size=len(contents),
        file_path=file_path,
        idempotency_key=idempotency_key or None,
        retention_days=retention_days,
    )
    db.add(record)
    await db.commit()

    return {
        "code": 0,
        "message": "上传成功",
        "data": {
            "file_id": str(record.id),
            "stored_name": stored_name,
            "original_name": file.filename,
            "media_type": media_type,
            "file_size": len(contents),
        },
    }


@router.get("/files", response_model=AnyResponse)
async def list_media_files(
    business_type: str | None = None,
    page: int = 1,
    limit: int = 20,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """List uploaded media files for current merchant."""

    filters = [MediaFile.merchant_id == merchant.id]
    if business_type:
        filters.append(MediaFile.business_type == business_type)
    offset = (page - 1) * limit
    rows = (
        (
            await db.execute(
                select(MediaFile)
                .where(*filters)
                .order_by(MediaFile.uploaded_at.desc())
                .offset(offset)
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "data": [
            {
                "file_id": str(r.id),
                "original_name": r.original_name,
                "media_type": r.media_type,
                "business_type": r.business_type,
                "file_size": r.file_size,
                "mime_type": r.mime_type,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
            }
            for r in rows
        ],
        "meta": {"page": page, "limit": limit},
    }
