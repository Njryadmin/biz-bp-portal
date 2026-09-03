"""
apps/api/app/db/bootstrap.py

Schema bootstrap for the data-integration + auth layer.

Creates (idempotently) the schemas and tables that the rest of the app
expects to find on first boot. The DDL is split into three groups:

1. ``SCHEMA_DDL`` — the legacy ``raw`` schema + ``raw.uploads`` table
   (data-integration), preserved verbatim from the original file.

2. ``AUTH_DDL`` — the new RBAC tables (``users``, ``user_roles``,
   ``user_business_lines``, ``raw.audit_log``) introduced for the
   2026-09-03 RBAC deliverable.

3. ``AI_MODELS_DDL`` — the ``ai_models`` registry table for the
   runtime-toggleable LLM provider switcher, added on 2026-09-03.

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
    "ALTER TABLE raw.uploads ADD COLUMN IF NOT EXISTS run_status TEXT",
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


# ---------------------------------------------------------------------------
# AI-models registry DDL — created 2026-09-03
# ---------------------------------------------------------------------------
#
# One row per registered LLM provider config. The ``is_default`` +
# ``enabled`` + ``is_active`` triple is what the factory
# (``services.llm.factory.get_active_model``) checks to pick the
# runtime provider. We always seed one row — the MockBackend — so the
# system is never without a working LLM.
#
# The provider CHECK is enforced in DDL so a typo at the API layer
# surfaces as a 500, not as a silent fallback to mock. Additive
# migrations are listed at the bottom of the list so existing
# installations upgrade cleanly.

AI_MODELS_DDL: list[str] = [
    # -----------------------------------------------------------------------
    # ai_models — runtime-toggleable LLM provider registry.
    # -----------------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS ai_models (
        id                    SERIAL PRIMARY KEY,
        name                  VARCHAR(64) UNIQUE NOT NULL,
        provider              VARCHAR(32) NOT NULL
                              CHECK (provider IN ('openai', 'deepseek', 'ollama', 'mock', 'anthropic', 'custom')),
        model_name            VARCHAR(128) NOT NULL,
        base_url              VARCHAR(512),
        api_key               VARCHAR(512),
        enabled               BOOLEAN NOT NULL DEFAULT TRUE,
        is_default            BOOLEAN NOT NULL DEFAULT FALSE,
        is_active             BOOLEAN NOT NULL DEFAULT TRUE,
        last_tested_at        TIMESTAMPTZ,
        last_test_status      VARCHAR(32),
        last_test_latency_ms  INTEGER,
        last_test_response    TEXT,
        created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ai_models_active "
    "ON ai_models (is_active) WHERE is_active = TRUE",
    "CREATE INDEX IF NOT EXISTS idx_ai_models_default "
    "ON ai_models (is_default) WHERE is_default = TRUE",
    # The CHECK constraint on ``provider`` cannot be re-issued to add a
    # new enum value without a DROP+ADD. We do the swap in a DO block
    # so legacy databases that pre-date the new provider values are
    # auto-upgraded. The new constraint allows all six values, so
    # existing rows that already satisfy it pass unchanged.
    """
    DO $$
    BEGIN
        ALTER TABLE ai_models DROP CONSTRAINT IF EXISTS ai_models_provider_check;
        ALTER TABLE ai_models
            ADD CONSTRAINT ai_models_provider_check
            CHECK (provider IN ('openai', 'deepseek', 'ollama', 'mock', 'anthropic', 'custom'));
    EXCEPTION WHEN OTHERS THEN
        NULL;
    END$$;
    """,
]


async def ensure_raw_schema() -> None:
    """Create the ``raw`` schema + ``raw.uploads`` table + RBAC tables
    + ``ai_models`` registry if missing.

    The engine has ``connect_args={"timeout": 2}`` so a missing
    PostgreSQL server fails fast. ``init_db`` adds an outer
    ``asyncio.wait_for`` for extra safety; this function itself does
    not impose a per-statement timeout because the DDL list is small
    and each statement is idempotent.

    Also seeds ONE row into ``ai_models`` on the very first boot: the
    built-in MockBackend. This guarantees the LLM factory always has a
    working provider to fall back to — even if the operator never opens
    the admin UI to add a real provider. The seed is ON CONFLICT
    DO NOTHING, so a partial seed (e.g. the table was created but the
    row was lost) is repaired on the next restart.
    """
    eng = engine()
    async with eng.begin() as conn:
        for stmt in SCHEMA_DDL:
            await conn.execute(text(stmt))
        for stmt in AUTH_DDL:
            await conn.execute(text(stmt))
        for stmt in AI_MODELS_DDL:
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
        # Seed the MockBackend row. ON CONFLICT DO NOTHING so a partial
        # seed (the row was deleted but the table was kept) is repaired
        # on the next restart. We always force this row to
        # is_default=TRUE so the factory has a known-good fallback even
        # if the operator later deletes every other row.
        await conn.execute(
            text(
                """
                INSERT INTO ai_models
                    (name, provider, model_name, base_url, api_key,
                     enabled, is_default, is_active,
                     last_test_status)
                VALUES
                    ('Mock (built-in)', 'mock', 'mock-1', NULL, NULL,
                     TRUE, TRUE, TRUE, 'untested')
                ON CONFLICT (name) DO NOTHING
                """
            )
        )
        # If for any reason no row is the default (e.g. the operator
        # deleted the seeded row and only the mock row remains under a
        # different name), promote the mock row to default. This is
        # belt-and-suspenders; the seed above already covers the
        # standard case.
        await conn.execute(
            text(
                """
                UPDATE ai_models
                SET is_default = TRUE
                WHERE provider = 'mock'
                  AND is_active = TRUE
                  AND NOT EXISTS (
                      SELECT 1 FROM ai_models
                      WHERE is_default = TRUE AND is_active = TRUE
                  )
                """
            )
        )
