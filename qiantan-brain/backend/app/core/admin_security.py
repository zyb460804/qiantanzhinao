"""平台管理员鉴权 — 独立 JWT token 体系。

与 Merchant JWT 分离：
  - iss = "qiantan-admin"（区别于 "qiantan-brain"）
  - sub = PlatformAdmin.id
  - role = super_admin / ops_admin
  - 复用同一 jwt_secret + 算法，但 decode 时校验 iss 防止跨体系 token 混用
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.saas import PlatformAdmin


ALGO = settings.jwt_algorithm
ADMIN_ISSUER = "qiantan-admin"
_oauth2_scheme = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    """bcrypt 哈希密码。"""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """验证明文密码与哈希是否匹配。支持 bcrypt 和 sha256 兜底。"""
    if hashed.startswith("$2"):
        # bcrypt hash
        try:
            return bcrypt.checkpw(plain.encode(), hashed.encode())
        except (ValueError, TypeError):
            return False
    else:
        # sha256 fallback (dev only) — 无法验证（salt 未存储），仅做开发环境匹配
        # 如果种子脚本用了 sha256 兜底，需重新运行种子脚本以使用 bcrypt
        return False


def create_admin_token(admin_id: uuid.UUID, role: str = "super_admin") -> str:
    """签发平台管理员 JWT。"""
    now = datetime.now(UTC)
    payload = {
        "sub": str(admin_id),
        "role": role,
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.admin_jwt_expire_minutes),
        "iss": ADMIN_ISSUER,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGO)


def decode_admin_token(token: str) -> dict:
    """解码管理员 token，校验签名 + 过期 + issuer。"""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[ALGO],
            issuer=ADMIN_ISSUER,
            options={"require": ["exp", "sub", "iss"]},
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


def extract_admin_token(request: Request) -> str | None:
    """Prefer a Bearer token for API clients, otherwise use the admin cookie."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip() or None
    return request.cookies.get(settings.admin_cookie_name)


async def get_current_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> PlatformAdmin:
    """从 Bearer token 或 HttpOnly Cookie 解析当前平台管理员。

    与 get_current_merchant 不同：
      - 不允许 fallback（管理员必须持 token）
      - 校验 iss == "qiantan-admin"，拒绝 Merchant token
    """
    token = creds.credentials if creds and creds.credentials else extract_admin_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_admin_token(token)
    admin_id = uuid.UUID(payload["sub"])
    jti = payload.get("jti")

    # 令牌吊销检查
    if jti and await _is_admin_token_revoked(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌已失效，请重新登录",
        )

    admin = await db.get(PlatformAdmin, admin_id)
    if admin is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员账号不存在",
        )
    if not admin.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已停用",
        )
    return admin


async def _is_admin_token_revoked(db: AsyncSession, jti: str) -> bool:
    """查询管理员令牌是否已被吊销。复用 AuthRevokedToken 表。"""
    from app.models.auth import AuthRevokedToken

    result = await db.execute(select(AuthRevokedToken).where(AuthRevokedToken.jti == jti))
    return result.scalar_one_or_none() is not None


async def revoke_admin_token(
    db: AsyncSession,
    jti: str,
    expires_at: datetime | int | float | None = None,
) -> None:
    """幂等吊销管理员令牌（仅 add，由调用方统一 commit）。"""
    from app.models.auth import AuthRevokedToken

    if await _is_admin_token_revoked(db, jti):
        return
    if isinstance(expires_at, (int, float)):
        expires_at = datetime.fromtimestamp(expires_at, tz=UTC)
    if isinstance(expires_at, datetime) and expires_at.tzinfo is not None:
        expires_at = expires_at.astimezone(UTC).replace(tzinfo=None)
    db.add(AuthRevokedToken(jti=jti, expires_at=expires_at))
