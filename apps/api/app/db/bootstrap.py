"""
apps/api/app/db/bootstrap.py

Schema bootstrap for the data-integration layer.

Creates the ``raw`` schema and the ``raw.uploads`` table if they don't
already exist. Called from ``apps.api.app.db.session.init_db`` at app
startup, which is itself invoked from the FastAPI lifespan handler.

All DDL is idempotent (``CREATE ... IF NOT EXISTS``), so this is safe
to call on every boot.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from .session import engine


# Hard upper bound on the schema-bootstrap call. Even with
# ``connect_args={"timeout": 2}`` on the engine, a hung DNS lookup or a
# half-open TCP connection can still block longer than the driver-
# level timeout. The caller (``init_db``) wraps us in
# ``asyncio.wait_for`` to guarantee startup cannot stall more than
# this many seconds when PostgreSQL is down. Picked at 2.0s so uvicorn
# reaches "Application startup complete" well within 5 seconds end-to-end.
DB_BOOTSTRAP_TIMEOUT_S: float = 2.0


SCHEMA_DDL: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    """
    CREATE TABLE IF NOT EXISTS raw.uploads (
        id          BIGSERIAL PRIMARY KEY,
        upload_id   TEXT NOT NULL UNIQUE,
        filename    TEXT NOT NULL,
        upload_type TEXT NOT NULL
                    CHECK (upload_type IN ('excel', 'csv', 'bank_statement', 'scraper')),
        row_count   INTEGER NOT NULL,
        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        fetched_at  TIMESTAMPTZ,
        source      TEXT,
        payload     JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_uploads_uploaded_at "
    "ON raw.uploads (uploaded_at DESC)",
    # Additive migration: add fetched_at / source columns to existing
    # installations that predate the scraper layer. Both are nullable so
    # legacy rows remain valid.
    "ALTER TABLE raw.uploads ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ",
    "ALTER TABLE raw.uploads ADD COLUMN IF NOT EXISTS source TEXT",
    # The original CHECK constraint excludes 'scraper'. Postgres does
    # not support modifying a CHECK in place, so drop and re-create it
    # in a DO block that swallows "constraint does not exist" errors.
    """
    DO $$
    BEGIN
        ALTER TABLE raw.uploads DROP CONSTRAINT IF EXISTS raw_uploads_upload_type_check;
        ALTER TABLE raw.uploads
            ADD CONSTRAINT raw_uploads_upload_type_check
            CHECK (upload_type IN ('excel', 'csv', 'bank_statement', 'scraper'));
    EXCEPTION WHEN OTHERS THEN
        -- Swallow; the new constraint is already correct.
        NULL;
    END$$;
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_uploads_source "
    "ON raw.uploads (source) WHERE source IS NOT NULL",
]


async def ensure_raw_schema() -> None:
    """Create the ``raw`` schema + ``raw.uploads`` table if missing.

    The engine has ``connect_args={"timeout": 2}`` so a missing
    PostgreSQL server fails fast. ``init_db`` adds an outer
    ``asyncio.wait_for`` for extra safety; this function itself does
    not impose a per-statement timeout because the DDL list is small
    and each statement is idempotent.
    """
    eng = engine()
    async with eng.begin() as conn:
        for stmt in SCHEMA_DDL:
            await conn.execute(text(stmt))
