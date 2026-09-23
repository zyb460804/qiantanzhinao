"""Auth 路由的 Pydantic 请求/响应模型。"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

from app.schemas.common import ApiResponse


# ── 请求模型 ──────────────────────────────────────────────

# 微信 wx.login() 返回的 code 实际字符集：字母/数字/-/_/~/.。
# 白名单外的字符（空格、SQL/XSS 特殊符号等）一律 422 拒绝，
# 防止历史版本的"零校验登录"被灌入任意串并批量创建商户（QA P2 修复）。
_WECHAT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_\-~.]+$")

# 微信 code 实际长度约 32 字符，128 为宽松上限，拦截超长串滥用。
WECHAT_CODE_MAX_LENGTH = 128


class WechatLoginRequest(BaseModel):
    code: str

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        """校验微信登录 code：trim 后非空、长度上限、字符白名单。

        校验失败抛 ValueError → FastAPI 转 422，中文 msg 经全局
        RequestValidationError handler 直达响应 detail。
        注意：返回 trim 后的 code，下游（dev mock / 真实 code2session）
        统一拿到无首尾空白的形式。
        """
        code = v.strip()
        if not code:
            raise ValueError("登录 code 不能为空")
        if len(code) > WECHAT_CODE_MAX_LENGTH:
            raise ValueError(f"登录 code 长度不能超过 {WECHAT_CODE_MAX_LENGTH} 个字符")
        if not _WECHAT_CODE_PATTERN.fullmatch(code):
            raise ValueError("登录 code 包含非法字符，仅允许字母、数字及 - _ ~ .")
        return code


class RefreshRequest(BaseModel):
    """refresh token 换新 access token（可选，当前用 access token 换发）。"""

    pass


class MerchantUpdateRequest(BaseModel):
    """更新商户信息的请求体。"""

    name: str | None = None
    business_type: str | None = None
    location: str | None = None


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
