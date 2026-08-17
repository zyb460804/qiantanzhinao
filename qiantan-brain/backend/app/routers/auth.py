"""鉴权路由（P0-1）：微信登录、获取当前商户、刷新令牌。

小程序流程：
  wx.login() → 拿到 code → POST /api/v1/auth/wechat-login {code}
  → 后端 code2session 换 openid → 绑定/创建 Merchant → 签发 JWT
  → 此后所有请求带 Authorization: Bearer <jwt>
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.models.saas import Tenant
from app.schemas.auth import (
    LoginData,
    LogoutResponse,
    MerchantInfo,
    MerchantUpdateRequest,
    MeResponse,
    RefreshResponse,
    TokenData,
    WechatLoginRequest,
    WechatLoginResponse,
)
from app.schemas.common import AnyResponse


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_oauth2_scheme = HTTPBearer(auto_error=False)

# 新商户/存量未绑定商户统一落入的默认租户。
# slug 唯一，天然幂等：已存在时直接复用，避免重复建租户。
DEFAULT_TENANT_SLUG = "default"


async def _get_or_create_default_tenant(db: AsyncSession) -> Tenant:
    """获取默认租户；不存在则创建（幂等）。

    并发首次登录时可能同时插入相同 slug，捕获 IntegrityError 后回滚重查，
    返回已由其它请求创建的默认租户。
    """
    result = await db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant

    tenant = Tenant(
        name="默认租户",
        slug=DEFAULT_TENANT_SLUG,
        status="active",
    )
    db.add(tenant)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            raise
    return tenant


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
        # 新商户必须绑定默认租户；先确保默认租户存在，再创建商户。
        tenant = await _get_or_create_default_tenant(db)
        merchant = Merchant(
            name=f"摊主{openid[-6:]}",
            wechat_openid=openid,
            role="owner",
            tenant_id=tenant.id,
        )
        db.add(merchant)
        try:
            await db.flush()
            is_new = True
        except IntegrityError:
            # 修复（F4）：并发首次登录同一 openid 时，另一个 session 已 INSERT
            # → IntegrityError（wechat_openid UNIQUE 约束）。rollback 后重新 SELECT
            # 取已建 Merchant，避免 500。
            await db.rollback()
            merchant = (
                await db.execute(select(Merchant).where(Merchant.wechat_openid == openid))
            ).scalar_one_or_none()
            if merchant is None:
                raise  # 不该发生 — IntegrityError 说明 UNIQUE 约束被另一并发请求触发
            # 并发商户可能由旧版本代码创建（tenant_id 为空），补绑默认租户。
            if merchant.tenant_id is None:
                tenant = await _get_or_create_default_tenant(db)
                merchant.tenant_id = tenant.id
    elif merchant.tenant_id is None:
        # 存量老商户登录时自动补绑默认租户（幂等，避免重复建）。
        tenant = await _get_or_create_default_tenant(db)
        merchant.tenant_id = tenant.id

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


@router.put("/me", response_model=MeResponse)
async def update_me(
    body: MerchantUpdateRequest,
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """更新当前商户信息。"""
    if body.name is not None:
        merchant.name = body.name
    if body.business_type is not None:
        merchant.business_type = body.business_type
    if body.location is not None:
        merchant.location = body.location
    await db.commit()
    await db.refresh(merchant)
    return MeResponse(code=0, data=_merchant_to_info(merchant))


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    merchant: Merchant = Depends(get_current_merchant),
    creds: HTTPAuthorizationCredentials | None = Depends(_oauth2_scheme),
    db: AsyncSession = Depends(get_db),
):
    """用有效 token 换发新 token，并吊销旧 token。

    修复（审计 H1）：原实现签发新 token 后旧 token 仍有效，构成 token 泄露窗口。
    现在签发新 token 前先吊销旧 token 的 jti，实现"刷新即轮转"语义。

    修复（F1 权限提升）：原实现用 `merchant.role`（DB 列）签新 token，而
    wechat_login 硬编码 role="owner"，所有微信登录商户的 DB 列恒为 "owner"。
    员工（cashier）通过 /staff/login 拿到带 staff_id claim 的 cashier token 后，
    调 /refresh 会被签成 owner + 无 staff_id 的新 token —— 等同权限提升为摊主全权，
    且旧 token 已被吊销，员工被迫用新 owner token。现从 token claim 读 role/staff_id
    原样轮转，保持员工身份。
    """
    # 提取旧 token 的 jti 并吊销（旧 token 无效不阻断 refresh，幂等）
    if creds and creds.credentials:
        try:
            payload = decode_access_token(creds.credentials)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti:
                await revoke_token(db, str(jti), exp)
        except HTTPException:
            pass  # 旧 token 已过期/无效，不阻断 refresh 流程
    # 从当前请求的 token claim 读 role/staff_id，避免员工 refresh 后被升级为 owner
    new_role = getattr(merchant, "_token_role", None) or merchant.role
    new_staff_id = getattr(merchant, "_token_staff_id", None)
    token = create_access_token(merchant.id, role=new_role, staff_id=new_staff_id)
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
            jti = payload.get("jti")
            if jti:
                await revoke_token(db, str(jti), payload.get("exp"))
        except HTTPException:
            pass  # 令牌已无效，注销幂等成功
    return LogoutResponse(code=0, message="已退出登录")


@router.get("/me/preferences", response_model=AnyResponse)
async def get_preferences(
    merchant: Merchant = Depends(get_current_merchant),
):
    """获取当前商户的经营偏好设置（方言、营业时段、城市、风险偏好等）。"""
    prefs = merchant.preferences or {}
    return {
        "code": 0,
        "data": {
            "voice_dialect": prefs.get("voice_dialect", "mandarin"),
            "business_hours": prefs.get("business_hours", "morning"),
            "notification_enabled": prefs.get("notification_enabled", True),
            "risk_profile": prefs.get("risk_profile", "neutral"),
            "merchant_city": prefs.get("merchant_city", "上海"),
        },
    }


@router.put("/me/preferences", response_model=AnyResponse)
async def update_preferences(
    body: dict = Body(...),
    merchant: Merchant = Depends(get_current_merchant),
    db: AsyncSession = Depends(get_db),
):
    """更新当前商户的经营偏好设置。存入 merchant.preferences JSON 字段。"""
    allowed_keys = {
        "voice_dialect",
        "business_hours",
        "notification_enabled",
        "risk_profile",
        "merchant_city",
    }
    current = merchant.preferences or {}
    for key in allowed_keys:
        if key in body:
            current[key] = body[key]
    merchant.preferences = current
    await db.commit()
    return {"code": 0, "data": current}
