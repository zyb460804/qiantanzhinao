"""ASGI middleware for persistent idempotency-key handling."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import jwt
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings
from app.database import get_db
from app.models.idempotency import IdempotencyRecord


_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_MAX_BODY_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def _request_session(scope: Scope) -> AsyncIterator[AsyncSession]:
    """Resolve the same DB provider FastAPI uses, including test overrides."""
    app = scope.get("app")
    provider = get_db
    if app is not None:
        provider = app.dependency_overrides.get(get_db, get_db)

    generator = provider()
    session = await anext(generator)
    try:
        yield session
    finally:
        await generator.aclose()


def _principal_scope(headers: Headers, scope: Scope) -> str:
    authorization = headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret,
                algorithms=[settings.jwt_algorithm],
                options={"verify_aud": False},
            )
            issuer = str(payload.get("iss") or "token")
            subject = str(payload.get("sub") or "unknown")
            return f"{issuer}:{subject}"
        except jwt.PyJWTError:
            digest = hashlib.sha256(token.encode()).hexdigest()[:32]
            return f"invalid-token:{digest}"

    api_key = headers.get("x-api-key")
    if api_key:
        digest = hashlib.sha256(api_key.encode()).hexdigest()[:32]
        device_id = headers.get("x-device-id", "unknown")
        return f"device:{digest}:{device_id}"

    client = scope.get("client") or ("unknown", 0)
    return f"anonymous:{client[0]}"


def _request_hash(scope: Scope, headers: Headers, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(scope["method"].encode())
    digest.update(scope["path"].encode())
    digest.update(scope.get("query_string", b""))
    digest.update(headers.get("content-type", "").encode())
    digest.update(body)
    return digest.hexdigest()


async def _send_json(send: Send, status_code: int, payload: dict[str, Any]) -> None:
    await JSONResponse(payload, status_code=status_code)(
        {"type": "http"},
        _empty_receive,
        send,
    )


async def _empty_receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


class IdempotencyMiddleware:
    """Cache completed unsafe requests when the client supplies a key."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        key = headers.get("idempotency-key") or headers.get("x-idempotency-key")
        if not key:
            await self.app(scope, receive, send)
            return
        if not _KEY_PATTERN.fullmatch(key):
            await _send_json(
                send,
                400,
                {"code": 400, "message": "Idempotency-Key 格式无效"},
            )
            return

        body_parts: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body_parts.append(message.get("body", b""))
            if sum(map(len, body_parts)) > _MAX_BODY_BYTES:
                await _send_json(
                    send,
                    413,
                    {"code": 413, "message": "幂等请求体超过 10 MB 限制"},
                )
                return
            if not message.get("more_body", False):
                break
        body = b"".join(body_parts)

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        principal = _principal_scope(headers, scope)
        operation = f"{scope['method'].upper()}:{scope['path']}"
        request_hash = _request_hash(scope, headers, body)

        async with _request_session(scope) as db:
            record = IdempotencyRecord(
                idempotency_key=key,
                tenant_id=principal,
                operation=operation,
                request_hash=request_hash,
                status_code=102,
            )
            db.add(record)
            try:
                await db.commit()
                await db.refresh(record)
            except IntegrityError:
                await db.rollback()
                existing = await db.scalar(
                    sa.select(IdempotencyRecord).where(
                        IdempotencyRecord.tenant_id == principal,
                        IdempotencyRecord.operation == operation,
                        IdempotencyRecord.idempotency_key == key,
                    )
                )
                if existing is None:
                    raise
                if existing.request_hash != request_hash:
                    await _send_json(
                        send,
                        409,
                        {"code": 409, "message": "幂等键已用于不同请求"},
                    )
                    return
                if existing.status_code == 102:
                    await _send_json(
                        send,
                        409,
                        {"code": 409, "message": "相同请求正在处理中"},
                    )
                    return
                cached = existing.response_body or "null"
                response_headers = []
                if existing.content_type:
                    response_headers.append(
                        (b"content-type", existing.content_type.encode("latin-1"))
                    )
                await send(
                    {
                        "type": "http.response.start",
                        "status": existing.status_code,
                        "headers": response_headers,
                    }
                )
                await send({"type": "http.response.body", "body": cached.encode("utf-8")})
                return

        messages: list[Message] = []

        async def capture_send(message: Message) -> None:
            messages.append(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        except Exception:
            async with _request_session(scope) as db:
                stored = await db.get(IdempotencyRecord, record.id)
                if stored is not None:
                    await db.delete(stored)
                    await db.commit()
            raise

        start = next(
            (message for message in messages if message["type"] == "http.response.start"),
            None,
        )
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        if start is not None:
            content_type = None
            for name, value in start.get("headers", []):
                if name.lower() == b"content-type":
                    content_type = value.decode("latin-1")
                    break
            async with _request_session(scope) as db:
                stored = await db.get(IdempotencyRecord, record.id)
                if stored is not None:
                    stored.status_code = int(start["status"])
                    stored.content_type = content_type
                    stored.response_body = response_body.decode("utf-8", errors="replace")
                    await db.commit()

        for message in messages:
            await send(message)
