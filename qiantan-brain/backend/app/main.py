"""FastAPI application entry point — 千摊智脑."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.middleware import RequestIDMiddleware
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    import os

    # 生产安全自检：fail-closed，致命误配直接拒绝启动（dev 环境跳过）
    settings.validate_security()

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


@app.get("/api/v1/health")
async def health_check():
    """Health check endpoint."""
    return {"code": 0, "message": "ok", "data": {"version": settings.app_version}}


@app.get("/api/v1/health/detailed")
async def detailed_health(db: AsyncSession = Depends(get_db)):
    """Detailed health report including DB connectivity and device status."""
    from app.core.health_monitor import build_health_report

    report = await build_health_report(db)
    return {"code": 0, "data": report}
