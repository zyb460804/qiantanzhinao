"""FastAPI application entry point — 千摊智脑."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.admin_security import get_current_admin
from app.core.idempotency_middleware import IdempotencyMiddleware
from app.core.middleware import RequestIDMiddleware, get_request_id
from app.core.tenant_context import TenantContextMiddleware
from app.database import get_db, init_db
from app.routers import (
    accounts,
    advice,
    ai_actions,
    anomalies,
    auth,
    behavior,
    catalog,
    device,
    edge,
    environment,
    expense,
    food_safety,
    insights,
    inventory,
    market_admin,
    operations,
    pos,
    purchase,
    reconciliation,
    reports,
    staff,
    twin,
    vision,
    voice,
)
from app.routers.admin import admins as admin_admins
from app.routers.admin import audit as admin_audit
from app.routers.admin import auth as admin_auth
from app.routers.admin import dashboard as admin_dashboard
from app.routers.admin import export as admin_export
from app.routers.admin import invoices as admin_invoices
from app.routers.admin import operations as admin_operations
from app.routers.admin import plans as admin_plans
from app.routers.admin import subscriptions as admin_subscriptions
from app.routers.admin import tenants as admin_tenants
from app.routers.admin import usage as admin_usage
from app.routers.tenant import portal as tenant_portal


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    import os

    # 生产安全自检：fail-closed，致命误配直接拒绝启动（dev 环境跳过）
    settings.validate_security()

    # Sentry error tracking（未配置 DSN 时静默跳过）
    if settings.sentry_dsn:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.sentry_environment or settings.app_env,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            release=settings.app_version,
        )

    os.makedirs(settings.upload_dir, exist_ok=True)
    os.makedirs(settings.audio_dir, exist_ok=True)

    # Schema bootstrap：以 Alembic 为唯一建表权威（全量基线 5242218be814 +
    # 未来增量迁移），从空库即可建出与 ORM 模型一致的完整 schema。
    # dev/test 若 Alembic 失败（debug 模式）会回退到 create_all 保持便利；
    # 生产（debug=False）失败则 fail-fast，绝不掩盖迁移错误。
    await init_db()

    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 全局异常处理：500 可观测性 + 422 中文摘要
# ---------------------------------------------------------------------------


def _validation_error_summary(exc: RequestValidationError) -> str:
    """把 pydantic 422 校验错误翻译成面向用户的中文摘要（取第一条错误）。

    常见类型映射：missing → 缺少字段X；value_error → 原 msg；
    其余类型回退为「字段: 原 msg」，避免向前端暴露 pydantic 内部结构。
    """
    errors = exc.errors()
    if not errors:
        return "请求参数校验失败"
    first = errors[0]
    loc = [
        str(part)
        for part in first.get("loc", ())
        if part not in ("body", "query", "path", "header")
    ]
    field = ".".join(loc) or "请求参数"
    err_type = str(first.get("type", ""))
    msg = str(first.get("msg", ""))
    if err_type == "missing":
        return f"缺少字段{field}"
    if err_type.startswith("value_error"):
        return msg or f"{field}取值不合法"
    if err_type.endswith("_type"):
        return f"{field}类型不正确（{msg}）"
    # 日期/时间解析类错误（date_parsing / date_from_datetime 等）：pydantic 的
    # msg 是英文技术文案，统一翻成用户可懂的格式提示。
    # 注意只匹配 date/datetime/time 根类型的解析错误，uuid_parsing 等不放行。
    if err_type.split("_", 1)[0] in ("date", "datetime", "time") or _looks_like_date_error(msg):
        return f"{field}格式不正确，日期应为 YYYY-MM-DD"
    return f"{field}: {msg}"


def _looks_like_date_error(msg: str) -> bool:
    """识别 pydantic 日期解析错误文案（英文），如 'Input should be a valid date ...'。"""
    return "valid date" in msg or "valid datetime" in msg or "expected range" in msg


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 校验错误 → 中文摘要；状态码保持 422，完整错误列表记 debug 日志。"""
    logger.debug(
        "422 validation failed on %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={"detail": _validation_error_summary(exc)},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底 500：logger.exception 记录完整堆栈（带 request_id），响应统一文案。

    此前未注册 Exception handler，500 时 uvicorn 日志抓不到 traceback，
    线上问题无法定位；现在保证日志与响应同时落地。
    """
    # 异常穿过 BaseHTTPMiddleware 子任务冒泡后 ContextVar 可能已失联，
    # 回退读请求头（RequestIDMiddleware 会接受客户端提供的 X-Request-ID）。
    request_id = get_request_id() or request.headers.get("X-Request-ID", "")
    logger.exception(
        "Unhandled exception on %s %s (request_id=%s)",
        request.method,
        request.url.path,
        request_id or "-",
        extra={"request_id": request_id},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器开小差了，请稍后再试"},
    )


# Prometheus metrics — 自动暴露 /metrics 端点并采集 HTTP 耗时/状态码
instrumentator = Instrumentator(
    should_group_status_codes=False,
    should_ignore_untemplated=True,
    should_respect_env_var=True,
)
instrumentator.instrument(app).expose(app)

# CORS：从 settings.cors_origins 读取白名单（逗号分隔）。
# "*" 仅允许本地 dev，且此时关闭 credentials 以避免规范矛盾；
# 生产环境必须配置具体域名（如 https://mp.weixin.qq.com,https://your-domain.com）。
_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _origins == ["*"]:
    _allow_origins, _allow_credentials = ["*"], False
else:
    _allow_origins, _allow_credentials = _origins, True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Request ID middleware — must be first so all downstream code has access
app.add_middleware(RequestIDMiddleware)
# Tenant context middleware — clears tenant_id ContextVar per request
app.add_middleware(TenantContextMiddleware)
# Retry-safe writes with Idempotency-Key are cached across client retries.
app.add_middleware(IdempotencyMiddleware)

# Register routers
app.include_router(voice.router)
app.include_router(vision.router)
app.include_router(inventory.router)
app.include_router(advice.router)
app.include_router(environment.router)
app.include_router(twin.router)
app.include_router(behavior.router)
# 产品意见反馈（原独立 feedback.py 已合并进 behavior，路径保持 /api/v1/feedback）
app.include_router(behavior.feedback_router)
app.include_router(reports.router)
app.include_router(purchase.router)
app.include_router(edge.router)
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(pos.router)
app.include_router(ai_actions.router)
app.include_router(catalog.router)
app.include_router(operations.router)
app.include_router(food_safety.router)
app.include_router(staff.router)
app.include_router(reconciliation.router)
app.include_router(device.router)
app.include_router(expense.router)
app.include_router(market_admin.router)
app.include_router(anomalies.router)
app.include_router(insights.router)

# Admin panel routers (SaaS management)
app.include_router(admin_auth.router)
app.include_router(admin_dashboard.router)
app.include_router(admin_tenants.router)
app.include_router(admin_plans.router)
app.include_router(admin_subscriptions.router)
app.include_router(admin_invoices.router)
app.include_router(admin_usage.router)
app.include_router(admin_export.router)
app.include_router(admin_admins.router)
app.include_router(admin_audit.router)
app.include_router(admin_operations.router)

# Tenant-side API (merchant-facing)
app.include_router(tenant_portal.router)


@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    return {"code": 0, "message": "ok", "data": {"version": settings.app_version}}


@app.get("/api/v1/health/detailed")
async def detailed_health(
    db: AsyncSession = Depends(get_db),
    admin=Depends(get_current_admin),
):
    """Detailed health report — 仅限管理员访问。

    包含数据库连接状态、设备心跳超时数量、当前健康状态等信息。
    不对公网公开，避免泄露内部运行状态。
    """
    from app.core.health_monitor import build_health_report

    report = await build_health_report(db)
    return {"code": 0, "data": report}
