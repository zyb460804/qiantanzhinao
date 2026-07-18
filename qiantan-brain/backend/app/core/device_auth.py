"""设备 API Key 鉴权 — 供边缘端（树莓派）非交互式调用后端 API。

鉴权模型：Device ID + API Key + Timestamp + Nonce

与商户 Bearer Token 的区别：
  - 不需要微信登录流程（设备没有微信）
  - 无过期时间或极长过期（API Key 本身作为长期凭证）
  - Scope 限定（edge:ingest / edge:heartbeat），不能访问经营接口
  - 支持密钥轮换（旧密钥可配置宽限期并行有效）
  - 请求级防重放（timestamp + nonce，窗口 5 分钟）

使用方式（边缘端发送）：
    POST /api/v1/edge/ingest/device
    Headers:
      X-Api-Key: qt_live_a1b2c3d4...
      X-Device-Id: raspberry-pi-5-001
      X-Timestamp: 1750000000
      X-Nonce: random-uuid-v4

后端验证流程：
    1. 用 SHA-256 对请求中的明文 key 做哈希，查询 ApiKey 表
    2. 校验设备序列号已注册且属于 API Key 对应租户
    3. 校验 ApiKey.is_active 且未过期
    4. 校验 X-Timestamp 在 5 分钟窗口内（防重放）
    5. 校验 X-Nonce 未在窗口内重复（Redis 或内存 LRU）
    6. 校验 Scope 包含请求路径对应的权限
    7. 注入 tenant_id / merchant_id / device_id 到 ContextVar
    8. 更新 last_used_at
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_context import set_tenant_id
from app.database import get_db
from app.models.device import Device
from app.models.merchant import Merchant
from app.models.saas import ApiKey


# ── 防重放配置 ──
REPLAY_WINDOW_SECONDS = 300  # 时间戳允许偏差 5 分钟
# 内存 LRU 去重（生产应迁移到 Redis）
_seen_nonces: dict[str, float] = {}
MAX_NONCE_CACHE = 10_000
NONCE_BACKEND = os.getenv(
    "DEVICE_NONCE_BACKEND",
    os.getenv("RATE_LIMIT_BACKEND", "memory"),
)
NONCE_REDIS_URL = os.getenv("RATE_LIMIT_REDIS_URL", "redis://localhost:6379/0")
_redis_client = None


def _clean_expired_nonces():
    """清理过期的 nonce 缓存。"""
    now = time.time()
    expired = [k for k, v in _seen_nonces.items() if now - v > REPLAY_WINDOW_SECONDS]
    for k in expired:
        del _seen_nonces[k]

    while len(_seen_nonces) >= MAX_NONCE_CACHE:
        oldest = min(_seen_nonces, key=lambda key: _seen_nonces[key])
        del _seen_nonces[oldest]


async def _claim_nonce(namespace: str, nonce: str) -> bool:
    """Atomically claim a nonce in Redis or the bounded local cache."""
    cache_key = f"device-nonce:{namespace}:{nonce}"
    if NONCE_BACKEND == "redis":
        global _redis_client
        if _redis_client is None:
            import redis.asyncio as redis

            _redis_client = redis.from_url(NONCE_REDIS_URL, decode_responses=True)
        claimed = await _redis_client.set(
            cache_key,
            "1",
            ex=REPLAY_WINDOW_SECONDS,
            nx=True,
        )
        return bool(claimed)

    _clean_expired_nonces()
    if cache_key in _seen_nonces:
        return False
    _seen_nonces[cache_key] = time.time()
    return True


def _hash_key(plain_key: str) -> str:
    """对 API Key 明文做 SHA-256 哈希。"""
    return hashlib.sha256(plain_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """生成新的 API Key，返回 (明文, 哈希)。

    明文格式：qt_live_<32 hex chars>
    只返回一次明文，调用方负责安全存储。
    """
    raw = f"qt_live_{uuid.uuid4().hex}"
    return raw, _hash_key(raw)


# ── FastAPI 依赖：从请求头解析并验证设备 API Key ──


async def authenticate_device(
    request: Request,
    x_api_key: str | None = Header(None, alias="X-Api-Key"),
    x_device_id: str | None = Header(None, alias="X-Device-Id"),
    x_timestamp: str | None = Header(None, alias="X-Timestamp"),
    x_nonce: str | None = Header(None, alias="X-Nonce"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """验证设备 API Key 并返回上下文。

    返回：{tenant_id, merchant_id, device_id, api_key_id}
    失败抛 401/403。

    merchant_id 始终由已注册设备反查得到，不信任请求体提交的归属信息。
    """
    # ── 基本参数校验 ──
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-Api-Key 请求头",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    if not x_device_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-Device-Id 请求头",
        )
    if not x_timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-Timestamp 请求头",
        )
    if not x_nonce:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 X-Nonce 请求头",
        )

    # ── 时间戳防重放 ──
    try:
        req_ts = int(x_timestamp)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Timestamp 格式无效",
        ) from exc

    now_ts = int(time.time())
    if abs(now_ts - req_ts) > REPLAY_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"请求时间戳偏差超过 {REPLAY_WINDOW_SECONDS} 秒，请校准设备时间",
        )

    # ── 数据库查询 ApiKey ──
    key_hash = _hash_key(x_api_key)
    api_key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == key_hash))

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
        )

    if not api_key.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key 已被停用",
        )

    now = datetime.now(UTC)
    expires_at = api_key.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at and expires_at <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key 已过期",
        )

    # ── Scope 检查 ──
    required_scope = _resolve_scope(request.url.path, request.method)
    scopes = api_key.scopes or []
    if required_scope and required_scope not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"API Key 无 {required_scope} 权限",
        )

    # ── 设备归属检查：由注册表解析 merchant，禁止请求体自行声明 ──
    registered = (
        await db.execute(
            select(Device, Merchant)
            .join(Merchant, Merchant.id == Device.merchant_id)
            .where(
                Device.serial_number == x_device_id,
                Device.is_active.is_(True),
                Merchant.tenant_id == api_key.tenant_id,
            )
        )
    ).all()
    if len(registered) != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="设备未注册、已停用或在当前租户中不唯一",
        )
    registered_device, merchant = registered[0]

    # ── Nonce 防重放：凭证验证成功后才占用缓存 ──
    if not await _claim_nonce(f"{api_key.id}:{x_device_id}", x_nonce):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请求重放检测：nonce 已使用",
        )

    api_key.last_used_at = now.replace(tzinfo=None)
    await db.commit()

    set_tenant_id(api_key.tenant_id)
    return {
        "tenant_id": api_key.tenant_id,
        "merchant_id": merchant.id,
        "api_key_id": api_key.id,
        "device_id": x_device_id,
        "registered_device_id": registered_device.id,
        "scopes": scopes,
    }


# ── Scope 解析 ──

_SCOPE_PATH_MAP = {
    "/api/v1/edge/ingest": "edge:ingest",
    "/api/v1/edge/ingest/device": "edge:ingest",
    "/api/v1/edge/heartbeat": "edge:heartbeat",
    "/api/v1/edge/status": "edge:status",
    "/api/v1/edge/ota/check": "edge:ingest",
    "/api/v1/edge/ota/report": "edge:ingest",
    "/api/v1/edge/model-version": "edge:ingest",
    "/api/v1/edge/logs": "edge:ingest",
}


def _resolve_scope(path: str, method: str) -> str | None:
    """根据请求路径和方法解析所需的 scope。"""
    # 精确匹配优先
    return _SCOPE_PATH_MAP.get(path)


# ── 全局依赖工厂：创建带 scope 的设备鉴权依赖 ──


class DeviceAuth:
    """设备鉴权依赖工厂。

    用法：
        @router.post("/api/v1/edge/ingest")
        async def ingest(
            device: dict = Depends(DeviceAuth.require("edge:ingest")),
        ):
            tenant_id = device["tenant_id"]
            device_id = device["device_id"]
    """

    @staticmethod
    def require(scope: str):
        """创建要求特定 scope 的设备鉴权依赖。"""

        async def _auth(
            request: Request,
            x_api_key: str | None = Header(None, alias="X-Api-Key"),
            x_device_id: str | None = Header(None, alias="X-Device-Id"),
            x_timestamp: str | None = Header(None, alias="X-Timestamp"),
            x_nonce: str | None = Header(None, alias="X-Nonce"),
            db: AsyncSession = Depends(get_db),
        ) -> dict:
            ctx = await authenticate_device(
                request, x_api_key, x_device_id, x_timestamp, x_nonce, db
            )
            if scope not in (ctx.get("scopes") or []):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"设备无 {scope} 权限",
                )
            return ctx

        return _auth
