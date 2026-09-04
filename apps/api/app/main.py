"""
apps/api/app/main.py

FastAPI 入口。启动事件读取 registry.yaml，并将每个业务线的
APIRouter / FastAPI 子应用挂载到其 api_prefix 之下。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .core.config import get_settings
from .core.logging import get_logger
from .core.registry import get_project_root
from .db import init_db
from .db.seed_users import seed_initial_users
from .middleware import AuditMiddleware
from .routers import build_registry_router
from .routers.admin_business_lines import router as admin_business_lines_router
from .routers.ai_models import router as ai_models_router
from .routers.alerts import router as alerts_router
from .routers.auth import router as auth_router
from .routers.copilot import router as copilot_router
from .routers.forecast import router as forecast_router
from .routers.registry import mount_business_line_routers
from .routers.scrapers import router as scrapers_router
from .routers.sensitivity import router as sensitivity_router
from .routers.upload import router as upload_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    root = get_project_root()
    logger.info("Project root: %s", root)
    logger.info("Loading business line routers...")
    mount_business_line_routers(app)
    logger.info("Business line routers mounted.")
    # 主动发现 web 爬虫，便于启动日志中输出已注册列表
    # （路由本身也会在首次访问时延迟发现，启动时打印更便于运维）。
    try:
        from .services.scrapers import discover_scrapers
        scrapers = discover_scrapers()
        logger.info("Discovered %d scraper(s): %s",
                    len(scrapers), ", ".join(s.source_id for s in scrapers))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scraper discovery failed: %s", exc)
    # init_db() 内部已有自身的 try/except + asyncio.wait_for
    # （参见 app.db.session.init_db），此处再增加一层保护：
    # 任何意外异常都绝不能阻止 uvicorn 完成启动阶段，
    # 否则 PostgreSQL 不可用时 API 会看似卡死在启动。
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "init_db failed at lifespan level (continuing without DB): %s", exc
        )
    # 首次启动的 RBAC 种子（admin + 每个业务线一个 BP）。尽力而为：
    # 不能因此阻塞启动。
    try:
        await seed_initial_users()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "seed_initial_users failed (continuing without seeded users): %s", exc
        )
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Fin BP Portal API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 审计中间件置于 CORS 之内，以便读取浏览器发送的 cookie /
    # Authorization 头。每个请求向 raw.audit_log 写一行（尽力而为）。
    app.add_middleware(AuditMiddleware)

    # 身份认证 / 用户管理
    app.include_router(auth_router)

    # 通用注册表端点
    app.include_router(build_registry_router())

    # 数据集成上传端点（Excel / CSV / 银行流水）
    app.include_router(upload_router)

    # 跨业务线敏感性 Lab（通用，不挂载在任何业务线下）
    app.include_router(sensitivity_router)

    # AI Copilot（通用，不挂载在任何业务线下）
    app.include_router(copilot_router)

    # 滚动预测引擎（通用）
    app.include_router(forecast_router)

    # 告警中心（通用）
    app.include_router(alerts_router)

    # Web 爬虫（市场数据接入）
    app.include_router(scrapers_router)

    # AI 模型注册表（运行时可切换的 LLM 厂商开关）
    app.include_router(ai_models_router)

    # Admin: 业务线 manifest / indicators 增删改 (D1, 2026-09-04)
    app.include_router(admin_business_lines_router)

    @app.get("/")
    async def root() -> dict:
        return {
            "service": "fin-bp-portal-api",
            "version": app.version,
            "registry": "/api/registry/lines",
            "auth": "/api/auth/me",
        }

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
