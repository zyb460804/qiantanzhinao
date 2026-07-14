"""Write-request idempotency middleware.

Provides idempotency-key based deduplication for POST/PUT/PATCH/DELETE requests.
Clients that explicitly set retrySafe:true should also send an Idempotency-Key header.

Architecture:
  - GET/HEAD/OPTIONS are always safe to retry (stateless).
  - POST/PUT/PATCH/DELETE are NOT retried by default; the client must explicitly
    opt in with retrySafe:true AND an Idempotency-Key header.
  - Idempotency records are stored with (tenant_id, operation, idempotency_key)
    unique constraint + request body hash + response cache + processing lock.
"""

from __future__ import annotations

import hashlib
import json
import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyRecord


logger = logging.getLogger(__name__)


# ── Core functions ──────────────────────────────────────────


def _hash_body(body: dict | None) -> str:
    if not body:
        return "empty"
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def check_idempotency(
    db: AsyncSession,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    request_body: dict | None = None,
) -> tuple[bool, dict | None]:
    """Check if this idempotency key has already been processed.

    Returns (is_duplicate, cached_response).
    - (False, None): first request, proceed normally
    - (True, cached_response): duplicate, return cached result
    - Raises ValueError if same key but different body (conflict)
    """
    # Look up existing record
    result = await db.execute(
        sa.select(IdempotencyRecord).where(
            IdempotencyRecord.idempotency_key == idempotency_key,
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.operation == operation,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        return False, None

    # Verify request body matches (same key, different body = conflict)
    current_hash = _hash_body(request_body)
    if record.request_hash != current_hash:
        raise ConflictError(
            idempotency_key,
            f"Idempotency key {idempotency_key} reused with different request body",
        )

    # Return cached response
    cached = None
    if record.response_body:
        try:
            cached = json.loads(record.response_body)
        except json.JSONDecodeError:
            cached = {"code": 0, "data": record.response_body}
    return True, cached


async def record_idempotency(
    db: AsyncSession,
    tenant_id: str,
    operation: str,
    idempotency_key: str,
    request_body: dict | None,
    response_body: dict | None,
    status_code: int,
) -> None:
    """Record the result of an idempotent write operation."""
    record = IdempotencyRecord(
        idempotency_key=idempotency_key,
        tenant_id=tenant_id,
        operation=operation,
        request_hash=_hash_body(request_body),
        response_body=json.dumps(response_body, ensure_ascii=False, default=str)
        if response_body
        else None,
        status_code=status_code,
    )
    db.add(record)
    await db.commit()


class ConflictError(Exception):
    """Idempotency key conflict — same key, different request body."""

    def __init__(self, key: str, message: str):
        self.key = key
        self.message = message
        super().__init__(message)
