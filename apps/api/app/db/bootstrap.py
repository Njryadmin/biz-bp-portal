"""
apps/api/app/db/bootstrap.py

Schema bootstrap for the data-integration + auth layer.

Creates (idempotently) the schemas and tables that the rest of the app
expects to find on first boot. The DDL is split into two groups:

1. ``SCHEMA_DDL`` — the legacy ``raw`` schema + ``raw.uploads`` table
   (data-integration), preserved verbatim from the original file.

2. ``AUTH_DDL`` — the new RBAC tables (``users``, ``user_roles``,
   ``user_business_lines``, ``raw.audit_log``) introduced for the
   2026-09-03 RBAC deliverable.

All DDL is idempotent (``CREATE ... IF NOT EXISTS`` / ``ADD ... IF NOT
EXISTS``), so this is safe to call on every boot.
"""
from __future__ import annotations

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


# ---------------------------------------------------------------------------
# RBAC DDL — created 2026-09-03
# ---------------------------------------------------------------------------

AUTH_DDL: list[str] = [
    # -----------------------------------------------------------------------
    # users — every login principal. ``password_hash`` is bcrypt.
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS users (
        id            SERIAL PRIMARY KEY,
        username      TEXT UNIQUE NOT NULL,
        email         TEXT UNIQUE,
        password_hash TEXT NOT NULL,
        display_name  TEXT,
        is_active     BOOLEAN NOT NULL DEFAULT TRUE,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    # -----------------------------------------------------------------------
    # user_roles — many-to-many (user, role). Role strings are open
    # (admin / viewer / auditor / bp:<line_id>).
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role       TEXT NOT NULL,
        granted_by INT REFERENCES users(id),
        granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, role)
    )
    """,
    # -----------------------------------------------------------------------
    # user_business_lines — explicit list of line ids a user can see.
    # Always kept in sync with bp:<line_id> roles (a role row and a
    # business-line row are inserted together).
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS user_business_lines (
        user_id    INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        line_id    TEXT NOT NULL,
        granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        PRIMARY KEY (user_id, line_id)
    )
    """,
    # -----------------------------------------------------------------------
    # raw.audit_log — one row per HTTP request, written by AuditMiddleware.
    # We keep it in the ``raw`` schema so it doesn't pollute the public
    # warehouse and so the same retention policy applies.
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS raw.audit_log (
        id           SERIAL PRIMARY KEY,
        user_id      INT,
        username     TEXT,
        method       TEXT NOT NULL,
        path         TEXT NOT NULL,
        query        TEXT,
        status_code  INT NOT NULL,
        duration_ms  INT NOT NULL,
        ip           TEXT,
        user_agent   TEXT,
        "timestamp"  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_audit_log_user ON raw.audit_log(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON raw.audit_log(\"timestamp\" DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_log_path ON raw.audit_log(path)",
]


async def ensure_raw_schema() -> None:
    """Create the ``raw`` schema + ``raw.uploads`` table + RBAC tables if missing.

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
        for stmt in AUTH_DDL:
            await conn.execute(text(stmt))
        # One-off cleanup: the ``bp-my-line`` user was created when the
        # ``my-line`` test line was still in the registry. The line has
        # since been removed, so the user is orphaned (no business line
        # matches ``bp:my-line``) and would never be auto-reaped. Drop
        # it on every boot so dev environments converge to a clean
        # 1 admin + 9 BP-users set.
        await conn.execute(
            text(
                "DELETE FROM user_business_lines "
                "WHERE user_id IN (SELECT id FROM users WHERE username = 'bp-my-line')"
            )
        )
        await conn.execute(
            text("DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE username = 'bp-my-line')")
        )
        await conn.execute(
            text("DELETE FROM users WHERE username = 'bp-my-line'")
        )
