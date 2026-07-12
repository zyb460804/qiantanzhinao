"""鉴权核心（P0-1）：JWT 签发/校验 + 微信 code2session + 获取当前商户。

设计原则（详见 team-code-quality-guidance.md §三.5）：
- 身份只来自 Bearer token，绝不信任请求体/查询参数里的 merchant_id。
- 过渡期：无 token 时允许从 query/header 取 merchant_id（仅 dev/测试），
  上线前必须删除该回退分支，强制走 token。

测试：通过 monkeypatch `app.core.security.wechat_code2session` 替换微信网络调用。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.merchant import Merchant


ALGO = settings.jwt_algorithm
_oauth2_scheme = HTTPBearer(auto_error=False)


def create_access_token(merchant_id: uuid.UUID, role: str | None = None) -> str:
    """签发 JWT：sub=商户ID，role=角色，iat/exp 用 UTC。"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(merchant_id),
        "role": role or "owner",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "iss": "qiantan-brain",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGO)


def decode_access_token(token: str) -> dict:
    """校验签名与过期，返回 payload；失败抛 401。"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGO],
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已过期，请重新登录",
        ) from err
    except jwt.InvalidTokenError as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效令牌",
        ) from err
    return payload


async def wechat_code2session(code: str) -> str:
    """调用微信 jscode2session 换 openid。

    生产需配置 settings.wechat_appid / wechat_secret。
    测试通过 monkeypatch 本函数替换网络调用，避免真实请求。
    """
    if not settings.wechat_appid or not settings.wechat_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务端未配置微信 AppID/Secret",
        )
    url = "https://api.weixin.qq.com/sns/jscode2session"
    params = {
        "appid": settings.wechat_appid,
        "secret": settings.wechat_secret,
        "js_code": code,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
    if data.get("errcode"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"微信登录失败: {data.get('errmsg', 'unknown')}",
        )
    openid = data.get("openid")
    if not openid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="微信未返回 openid")
    return openid


async def get_current_merchant(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> Merchant:
    """从 Bearer token 解析当前商户。

    安全原则：身份只来自可信凭证（token）。`auth_allow_fallback` 为 True 时
    才允许过渡期从 query/header 取 merchant_id（仅 dev/测试）。生产必须为 False。
    """
    merchant_id: uuid.UUID | None = None
    jti: str | None = None

    if creds and creds.credentials:
        payload = decode_access_token(creds.credentials)
        merchant_id = uuid.UUID(payload["sub"])
        jti = payload.get("jti")
    elif settings.auth_allow_fallback:
        # ⚠️ 仅 dev/测试过渡：无 token 时从 query/header 取 merchant_id。
        raw = request.query_params.get("merchant_id") or request.headers.get("X-Merchant-Id")
        if raw:
            try:
                merchant_id = uuid.UUID(raw)
            except ValueError:
                merchant_id = None
    # 注意：生产环境（auth_allow_fallback=False）且缺 token → 直接 401，不回退。

    if merchant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未认证",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 令牌吊销检查（注销后失效）
    if jti and await _is_token_revoked(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已失效，请重新登录",
        )

    merchant = await db.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="商户不存在")
    return merchant


async def get_merchant_id(merchant: Merchant = Depends(get_current_merchant)) -> uuid.UUID:
    """依赖注入：返回已验证的商户 ID。

    用于路由处理函数签名，FastAPI 会用此依赖提供 merchant_id，
    **忽略** 任何客户端传入的 merchant_id（query/body/header），从根源杜绝越权。
    """
    return merchant.id


async def _is_token_revoked(db: AsyncSession, jti: str) -> bool:
    """查询令牌是否已被吊销（注销）。"""
    from app.models.auth import AuthRevokedToken

    result = await db.execute(select(AuthRevokedToken).where(AuthRevokedToken.jti == jti))
    return result.scalar_one_or_none() is not None


async def revoke_token(db: AsyncSession, jti: str, expires_at=None) -> None:
    """吊销（注销）一个令牌，使其后续请求失效。"""
    from app.models.auth import AuthRevokedToken

    if isinstance(expires_at, (int, float)):
        expires_at = datetime.fromtimestamp(expires_at, tz=UTC)
    db.add(AuthRevokedToken(jti=jti, expires_at=expires_at))
    await db.commit()


def merchant_id_from_token(token: str) -> uuid.UUID:
    """供需要 merchant_id 字符串的调用方使用（如日志/审计）。"""
    return uuid.UUID(decode_access_token(token)["sub"])
