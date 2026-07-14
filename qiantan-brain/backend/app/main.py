"""FastAPI application entry point — 千摊智脑."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.admin_security import get_current_admin
from app.core.idempotency_middleware import IdempotencyMiddleware
from app.core.middleware import RequestIDMiddleware
from app.core.tenant_context import TenantContextMiddleware
from app.database import get_db, init_db
from app.routers import (
    accounts,
    advice,
    ai_actions,
    auth,
    behavior,
    catalog,
    cloud,
    device,
    edge,
    environment,
    expense,
    feedback,
    food_safety,
    inventory,
    market_admin,
    media,
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
app.include_router(cloud.router)
app.include_router(behavior.router)
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
app.include_router(feedback.router)
app.include_router(media.router)

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
