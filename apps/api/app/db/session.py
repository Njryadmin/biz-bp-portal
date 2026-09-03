"""
apps/api/app/db/session.py

SQLAlchemy 2.0 async engine + session factory. The MVP just exposes the
plumbing; business lines can introduce their own ORM models in
`business_lines/<line>/api/models.py` and import them in their routers.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ..core.config import get_settings
from ..core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_connect_args() -> dict:
    """Build the asyncpg ``connect_args`` that prevent startup hang.

    asyncpg defaults to an unbounded TCP connect timeout — if PostgreSQL
    is not running on the configured host, ``connect()`` blocks for
    several minutes (OS-level TCP retransmit) and uvicorn's ``lifespan``
    never returns, making the API look "stuck" at boot.

    We pass a 2-second ``timeout`` (asyncpg connect timeout, in seconds)
    so the connection attempt fails fast. The engine also has
    ``pool_pre_ping`` so dead connections in the pool are recycled.
    """
    return {"timeout": 2}


def engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            future=True,
            echo=False,
            pool_pre_ping=True,
            connect_args=_build_connect_args(),
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(engine(), expire_on_commit=False)
    return _session_factory


def reset_engine() -> None:
    """Drop the cached engine + session factory.

    Useful for tests that spawn multiple event loops (e.g. an outer
    ``TestClient`` loop plus an inner ``asyncio.run`` helper). Calling
    this between loops forces the next ``engine()`` to construct a
    fresh pool bound to the new loop, avoiding "got Future attached
    to a different loop" errors.
    """
    global _engine, _session_factory
    if _engine is not None:
        try:
            # Best-effort: dispose the pool. If the loop is already
            # closed, swallow the warning — the engine is about to be
            # garbage-collected anyway.
            _engine.sync_engine.pool.dispose()
        except Exception:  # noqa: BLE001
            pass
    _engine = None
    _session_factory = None


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_db() -> None:
    """Bootstrap the data-integration schema on startup.

    Creates the ``raw`` schema and the ``raw.uploads`` table if they don't
    already exist. Failures are logged but do not prevent the app from
    starting — the upload router will return 500 on DB errors until the
    database becomes reachable.

    Belt-and-suspenders: the engine's ``connect_args={"timeout": 2}``
    already bounds the asyncpg connect, but we also wrap
    ``ensure_raw_schema()`` in ``asyncio.wait_for`` so even an exotic
    hang (DNS, TLS handshake, etc.) cannot block startup longer than
    ``DB_BOOTSTRAP_TIMEOUT_S`` seconds.
    """
    from .bootstrap import DB_BOOTSTRAP_TIMEOUT_S, ensure_raw_schema  # local import to avoid cycle
    try:
        await asyncio.wait_for(ensure_raw_schema(), timeout=DB_BOOTSTRAP_TIMEOUT_S)
    except asyncio.TimeoutError:
        logger.warning(
            "init_db: ensure_raw_schema timed out after %.1fs (DB unreachable, continuing without DB)",
            DB_BOOTSTRAP_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("init_db: ensure_raw_schema failed (DB may be down): %s", exc)
