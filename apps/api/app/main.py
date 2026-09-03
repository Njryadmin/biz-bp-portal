"""
apps/api/app/main.py

FastAPI entrypoint. The startup event reads registry.yaml and mounts
each business line's APIRouter / FastAPI sub-app under its api_prefix.
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
from .routers import build_registry_router
from .routers.alerts import router as alerts_router
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
    # Discover web scrapers eagerly so the log shows the registered
    # list at startup (the routers would discover lazily on first hit
    # anyway, but a boot-time line is nicer for ops).
    try:
        from .services.scrapers import discover_scrapers
        scrapers = discover_scrapers()
        logger.info("Discovered %d scraper(s): %s",
                    len(scrapers), ", ".join(s.source_id for s in scrapers))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Scraper discovery failed: %s", exc)
    # init_db() already has its own try/except + asyncio.wait_for inside
    # (see app.db.session.init_db), but we add an outer guard here too:
    # any unforeseen failure must NEVER prevent uvicorn from finishing
    # its startup phase, otherwise the API appears to hang on boot when
    # PostgreSQL is down.
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "init_db failed at lifespan level (continuing without DB): %s", exc
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

    # Generic registry endpoints
    app.include_router(build_registry_router())

    # Data-integration upload endpoints (Excel / CSV / bank-statement)
    app.include_router(upload_router)

    # Cross-business-line sensitivity lab (universal, not under any line)
    app.include_router(sensitivity_router)

    # AI Copilot (universal, not under any line)
    app.include_router(copilot_router)

    # Rolling Forecast engine (universal)
    app.include_router(forecast_router)

    # Alert Center (universal)
    app.include_router(alerts_router)

    # Web scrapers (market data ingest)
    app.include_router(scrapers_router)

    @app.get("/")
    async def root() -> dict:
        return {
            "service": "fin-bp-portal-api",
            "version": app.version,
            "registry": "/api/registry/lines",
        }

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
