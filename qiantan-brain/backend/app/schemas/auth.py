"""Auth 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import ApiResponse


# ── 请求模型 ──────────────────────────────────────────────


class WechatLoginRequest(BaseModel):
    code: str


class RefreshRequest(BaseModel):
    """refresh token 换新 access token（可选，当前用 access token 换发）。"""

    pass


# ── 响应数据模型 ──────────────────────────────────────────


class MerchantInfo(BaseModel):
    id: str
    name: str
    role: str
    business_type: str | None = None
    location: str | None = None

    model_config = {"from_attributes": True}


class LoginData(BaseModel):
    token: str
    expires_in: int  # 秒
    is_new: bool
    merchant: MerchantInfo


class TokenData(BaseModel):
    token: str
    expires_in: int


# ── 响应信封类型别名 ──────────────────────────────────────

WechatLoginResponse = ApiResponse[LoginData]
MeResponse = ApiResponse[MerchantInfo]
RefreshResponse = ApiResponse[TokenData]
LogoutResponse = ApiResponse[None]
