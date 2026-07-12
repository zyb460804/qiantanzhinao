"""鉴权路由（P0-1）：微信登录、获取当前商户、刷新令牌。

小程序流程：
  wx.login() → 拿到 code → POST /api/v1/auth/wechat-login {code}
  → 后端 code2session 换 openid → 绑定/创建 Merchant → 签发 JWT
  → 此后所有请求带 Authorization: Bearer <jwt>
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    get_current_merchant,
    revoke_token,
    wechat_code2session,
)
from app.database import get_db
from app.models.merchant import Merchant
from app.schemas.auth import (
    LoginData,
    LogoutResponse,
    MerchantInfo,
    MeResponse,
    RefreshResponse,
    TokenData,
    WechatLoginRequest,
    WechatLoginResponse,
)


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_oauth2_scheme = HTTPBearer(auto_error=False)


def _merchant_to_info(m: Merchant) -> MerchantInfo:
    return MerchantInfo(
        id=str(m.id),
        name=m.name,
        role=m.role,
        business_type=m.business_type,
        location=m.location,
    )


@router.post("/wechat-login", response_model=WechatLoginResponse)
async def wechat_login(
    body: WechatLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """微信小程序登录：code → openid → 绑定/创建商户 → 签发 JWT。"""
    openid = await wechat_code2session(body.code)

    # 按 openid 查找已绑定商户；没有则创建（默认摊主角色）
    result = await db.execute(select(Merchant).where(Merchant.wechat_openid == openid))
    merchant = result.scalar_one_or_none()
    is_new = False
    if merchant is None:
        merchant = Merchant(
            name=f"摊主{openid[-6:]}",
            wechat_openid=openid,
            role="owner",
        )
        db.add(merchant)
        await db.flush()
        is_new = True

    await db.commit()
    await db.refresh(merchant)

    token = create_access_token(merchant.id, merchant.role)
    return WechatLoginResponse(
        code=0,
        data=LoginData(
            token=token,
            expires_in=settings.jwt_expire_minutes * 60,
            is_new=is_new,
            merchant=_merchant_to_info(merchant),
        ),
    )


@router.get("/me", response_model=MeResponse)
async def me(merchant: Merchant = Depends(get_current_merchant)):
    """返回当前登录商户信息（身份来自 token）。"""
    return MeResponse(code=0, data=_merchant_to_info(merchant))


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(merchant: Merchant = Depends(get_current_merchant)):
    """用有效 token 换发新 token。"""
    token = create_access_token(merchant.id, merchant.role)
    return RefreshResponse(
        code=0,
        data=TokenData(token=token, expires_in=settings.jwt_expire_minutes * 60),
    )


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    creds: HTTPAuthorizationCredentials | None = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """注销：吊销当前令牌，使其后续请求立即失效。

    无 token 也可调用（幂等），便于客户端清理本地状态。
    """
    if creds and creds.credentials:
        try:
            payload = decode_access_token(creds.credentials)
            await revoke_token(db, payload.get("jti"), payload.get("exp"))
        except HTTPException:
            pass  # 令牌已无效，注销幂等成功
    return LogoutResponse(code=0, message="已退出登录")
