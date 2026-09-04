"""
apps/api/tests/test_multi_tenant_m1.py
======================================

Tests for InsightBP v2 P2 multi-tenant M1 (2026-09-04).

What we cover
-------------
* ``003_multi_tenant_setup.sql`` applies cleanly on the dev pgserver
  (where the v0.1.0 + PR#1 schemas already exist) and is idempotent
  (running it twice doesn't error and the second run is a no-op).
* The ``tenants`` table is created with a default-tenant row.
* The 6 business tables (``users``, ``user_roles``,
  ``user_business_lines``, ``raw.audit_log``, ``ai_models``,
  ``raw.uploads``) gain a NOT NULL ``tenant_id`` column, indexed and
  constrained to the default tenant.
* All existing rows are backfilled to the default tenant.
* Row-Level Security is **enabled AND forced** on all 6 tables.
* The RLS policy ``tenant_lock`` is present and behaves correctly:
  - Without ``app.tenant_id`` set: the policy denies all rows.
  - With ``app.tenant_id`` set to the default tenant: the policy
    allows all backfilled rows.
  - With ``app.tenant_id`` set to an unknown tenant: zero rows.
* The ``tenant_session()`` async context manager from
  ``app.db.tenant`` sets the right GUCs and releases them on
  transaction exit (``SET LOCAL`` semantics).

RLS testing note
----------------
The dev pgserver user (``finbp``) is a ``SUPERUSER`` (see
``pgserver_runner.py:CREATE USER finbp ... SUPERUSER``). Superusers
bypass RLS by default — even ``FORCE ROW LEVEL SECURITY`` is no-op
for them. To test the policy's blocking behaviour we create a
non-superuser role in the test fixture, ``GRANT SELECT`` on the
relevant tables to it, and then ``SET LOCAL ROLE`` to it within a
transaction. After the transaction the role is automatically reset
to ``finbp``. This mirrors how production RLS will work: the app
connects as a least-privilege role and RLS fires normally.

Why we hit the real pgserver
----------------------------
Same as ``test_migration_runner.py`` and ``test_ai_models.py``: the
RLS policy is a SQL feature, not a Python one. Mocking Postgres out
would test nothing.

Run with::

    BIZ_BP_DATABASE_URL=postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp \\
        python -m pytest apps/api/tests/test_multi_tenant_m1.py -v
"""
from __future__ import annotations

import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse
from uuid import UUID

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000000"
OTHER_TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEFAULT_TENANT_UUID = UUID(DEFAULT_TENANT_ID)

# Tables that gain a tenant_id column. ``(schema, table)``.
TENANTED_TABLES: list[tuple[str, str]] = [
    ("public", "users"),
    ("public", "user_roles"),
    ("public", "user_business_lines"),
    ("raw", "audit_log"),
    ("public", "ai_models"),
    ("raw", "uploads"),
]

# A non-superuser test role. We create it once per test module, reuse
# across all tests, and drop at module teardown. The role is only
# used to verify RLS behaviour; the rest of the suite keeps using
# ``finbp`` directly.
TEST_RLS_ROLE = "m1_rls_probe"


# ---------------------------------------------------------------------------
# Fixtures: pgserver gate + DSN parsing + role setup
# ---------------------------------------------------------------------------


def _parse_pg_dsn() -> dict[str, object]:
    from app.core.config import get_settings

    url = get_settings().database_url.replace("+asyncpg", "")
    u = urlparse(url)
    return {
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "user": u.username,
        "password": u.password or "",
        "database": (u.path or "/postgres").lstrip("/") or "postgres",
    }


@pytest.fixture(scope="module")
def postgres_available():
    """Skip the whole file when pgserver isn't running."""
    cfg = _parse_pg_dsn()
    try:
        with socket.create_connection((cfg["host"], cfg["port"]), timeout=0.5):
            return cfg
    except (OSError, socket.timeout):
        pytest.skip(
            f"Postgres not reachable at {cfg['host']}:{cfg['port']} — "
            f"multi-tenant M1 tests skipped"
        )


def _temp_migrations_dir_with_003(tmp_path: Path, repo_root: Path) -> Path:
    """Create a temporary migrations dir containing a *copy* of 003.

    We use a temp dir so we don't accidentally re-apply 001/002 to
    someone else's DB (those are owned by the parent PR#1).
    """
    d = tmp_path / "m1_migrations"
    d.mkdir()
    src = repo_root / "infra" / "migrations" / "003_multi_tenant_setup.sql"
    (d / "003_multi_tenant_setup.sql").write_text(
        src.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return d


@asynccontextmanager
async def _conn() -> AsyncIterator:
    """Yield a fresh asyncpg connection (bypasses the SQLAlchemy pool)."""
    import asyncpg

    cfg = _parse_pg_dsn()
    conn = await asyncpg.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture(scope="module")
async def rls_probe_role(postgres_available):
    """Create the non-superuser probe role once for the module.

    Grants the minimum SELECT on every RLS-protected table so the
    policy has something to filter. Drops the role at module
    teardown.
    """
    async with _conn() as conn:
        # Drop if leftover from a prior run (best-effort)
        await conn.execute(f'DROP ROLE IF EXISTS "{TEST_RLS_ROLE}"')
        await conn.execute(
            f"CREATE ROLE \"{TEST_RLS_ROLE}\" NOINHERIT LOGIN PASSWORD 'probe'"
        )
        # Grant SELECT on each tenanted table
        for schema, table in TENANTED_TABLES:
            await conn.execute(
                f'GRANT SELECT ON TABLE "{schema}"."{table}" '
                f'TO "{TEST_RLS_ROLE}"'
            )
        # Also grant USAGE on the raw schema so the role can resolve
        # raw.audit_log / raw.uploads by name.
        await conn.execute(
            f'GRANT USAGE ON SCHEMA "raw" TO "{TEST_RLS_ROLE}"'
        )
    try:
        yield TEST_RLS_ROLE
    finally:
        async with _conn() as conn:
            # REVOKE then DROP. We don't fail the test on cleanup.
            try:
                for schema, table in TENANTED_TABLES:
                    await conn.execute(
                        f'REVOKE SELECT ON TABLE "{schema}"."{table}" '
                        f'FROM "{TEST_RLS_ROLE}"'
                    )
                await conn.execute(
                    f'REVOKE USAGE ON SCHEMA "raw" FROM "{TEST_RLS_ROLE}"'
                )
            except Exception:  # noqa: BLE001
                pass
            try:
                await conn.execute(f'DROP ROLE IF EXISTS "{TEST_RLS_ROLE}"')
            except Exception:  # noqa: BLE001
                pass


@asynccontextmanager
async def _as_role(conn, role: str) -> AsyncIterator:
    """``SET LOCAL ROLE`` to a non-superuser role for the transaction.

    Postgres resets the role at transaction end, so this is safe to
    use inside ``async with conn.transaction():``.
    """
    async with conn.transaction():
        await conn.execute(f'SET LOCAL ROLE "{role}"')
        yield conn


# ---------------------------------------------------------------------------
# 1) Migration applies cleanly on a real database
# ---------------------------------------------------------------------------


async def test_003_migration_runs_on_existing_db(postgres_available, tmp_path, repo_root):
    """Apply 003 against the dev DB (which already has the v0.1.0 +
    PR#1 schema) and verify the post-state matches the design contract.

    Test isolation
    --------------
    We snapshot the ``schema_migrations`` row for 003, delete it,
    run the migration, then restore the snapshot. The SQL itself is
    idempotent so re-running is a no-op — but the bookkeeping is
    what ``MigrationRunner`` consults, and we want to exercise the
    real "apply" code path (not just the "skip" path).
    """
    from app.db import session as session_mod
    from app.db.migration_runner import MigrationRunner
    from app.db.session import engine as _engine

    cfg = _parse_pg_dsn()

    # Snapshot + remove the 003 row so the apply path runs.
    async with _conn() as raw_conn:
        saved_row = None
        try:
            saved_row = await raw_conn.fetchrow(
                "SELECT version, filename, applied_at, checksum, duration_ms "
                "FROM schema_migrations WHERE version = '003_multi_tenant_setup'"
            )
            await raw_conn.execute(
                "DELETE FROM schema_migrations WHERE version = '003_multi_tenant_setup'"
            )
        finally:
            pass

    try:
        migrations_dir = _temp_migrations_dir_with_003(tmp_path, repo_root)
        runner = MigrationRunner(migrations_dir=migrations_dir)
        session_mod.reset_engine()

        result = await runner.apply_pending()
        assert len(result.applied) == 1, (
            f"expected 1 migration applied, got {result.applied}"
        )
        assert result.applied[0].version == "003_multi_tenant_setup"
        assert result.skipped == []
        assert result.failed == []
    finally:
        # Restore the bookkeeping row so the rest of the suite sees
        # 003 as already applied.
        async with _conn() as raw_conn:
            try:
                await raw_conn.execute(
                    "DELETE FROM schema_migrations WHERE version = '003_multi_tenant_setup'"
                )
                if saved_row is not None:
                    await raw_conn.execute(
                        "INSERT INTO schema_migrations "
                        "(version, filename, applied_at, checksum, duration_ms) "
                        "VALUES ($1, $2, $3, $4, $5)",
                        saved_row["version"],
                        saved_row["filename"],
                        saved_row["applied_at"],
                        saved_row["checksum"],
                        saved_row["duration_ms"],
                    )
            finally:
                pass
        session_mod.reset_engine()

    # Verify the post-state matches the design contract.
    eng = _engine()
    async with eng.connect() as conn:
        # tenants table + default row
        rows = (
            await conn.execute(
                text(
                    "SELECT id, slug, name, plan, is_active "
                    "FROM tenants WHERE id = :tid"
                ),
                {"tid": DEFAULT_TENANT_ID},
            )
        ).mappings().first()
        assert rows is not None, "default tenant row missing"
        assert rows["slug"] == "default"
        assert rows["is_active"] is True

        # Each tenanted table has a NOT NULL tenant_id column
        for schema, table in TENANTED_TABLES:
            row = (
                await conn.execute(
                    text(
                        "SELECT data_type, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND column_name = 'tenant_id'"
                    ),
                    {"s": schema, "t": table},
                )
            ).first()
            assert row is not None, f"{schema}.{table}: tenant_id column missing"
            assert row[0] == "uuid", f"{schema}.{table}: tenant_id wrong type"
            assert row[1] == "NO", f"{schema}.{table}: tenant_id is nullable"

        # Each tenanted table has RLS enabled + forced
        for schema, table in TENANTED_TABLES:
            row = (
                await conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t"
                    ),
                    {"s": schema, "t": table},
                )
            ).first()
            assert row is not None, f"{schema}.{table}: pg_class row missing"
            assert row[0] is True, f"{schema}.{table}: RLS not enabled"
            assert row[1] is True, f"{schema}.{table}: RLS not forced"

        # Each tenanted table has a tenant_lock policy
        for schema, table in TENANTED_TABLES:
            row = (
                await conn.execute(
                    text(
                        "SELECT 1 FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t "
                        "AND policyname = 'tenant_lock'"
                    ),
                    {"s": schema, "t": table},
                )
            ).first()
            assert row is not None, f"{schema}.{table}: tenant_lock policy missing"

    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 2) Migration is idempotent
# ---------------------------------------------------------------------------


async def test_003_migration_idempotent(postgres_available, repo_root):
    """Re-apply 003 — it must not raise on a second pass.

    The runner itself rejects re-applies (the version is already in
    ``schema_migrations``), so we test the *underlying SQL* by
    re-executing the stripped file as raw asyncpg. That bypasses the
    runner's idempotency layer and proves the DDL itself is safe to
    re-run.
    """
    import re

    src_sql = (
        repo_root / "infra" / "migrations" / "003_multi_tenant_setup.sql"
    ).read_text(encoding="utf-8")
    # Strip the outer BEGIN/COMMIT — we run inside our own tx.
    s = src_sql.strip()
    leading = re.compile(r"^\s*(?:--[^\n]*\n\s*)*BEGIN\s*;\s*", re.IGNORECASE)
    trailing = re.compile(r"\s*COMMIT\s*;\s*(?:--[^\n]*\n\s*)*$", re.IGNORECASE)
    m = leading.match(s)
    if m:
        s = s[m.end():]
    m = trailing.search(s)
    if m:
        s = s[: m.start()].rstrip()

    async with _conn() as conn:
        # First re-run: must not raise.
        await conn.execute(s)
        # Second re-run: also must not raise.
        await conn.execute(s)
        # Verify the tenants row is still exactly one
        n = await conn.fetchval("SELECT COUNT(*) FROM tenants")
        assert n == 1, f"expected 1 tenant row, got {n}"
        # Verify users count is unchanged
        n_users = await conn.fetchval("SELECT COUNT(*) FROM users")
        assert n_users > 0, "users table is empty — backfill test invalid"


# ---------------------------------------------------------------------------
# 3) Seed users backfilled to default tenant
# ---------------------------------------------------------------------------


async def test_seed_users_backfilled(postgres_available):
    """All users in the live DB carry ``tenant_id = DEFAULT_TENANT_ID``.

    The dev DB has 1 admin + 8 BP seed users (the original August
    release) plus any extra accounts created by the test suite. The
    invariant we verify is: every user row carries the default
    tenant and at least 9 rows exist (admin + 8 BP).
    """
    from app.db import session as session_mod
    from app.db.session import engine as _engine

    session_mod.reset_engine()
    eng = _engine()
    async with eng.connect() as conn:
        total = (
            await conn.execute(text("SELECT COUNT(*) FROM users"))
        ).scalar_one()
        backfilled = (
            await conn.execute(
                text(
                    "SELECT COUNT(*) FROM users "
                    "WHERE tenant_id = :tid"
                ),
                {"tid": DEFAULT_TENANT_ID},
            )
        ).scalar_one()
        assert int(total) >= 9, (
            f"expected at least 9 users (admin + 8 BP), got {total}"
        )
        assert int(total) == int(backfilled), (
            f"backfill incomplete: {backfilled}/{total} users on default tenant"
        )
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 4) RLS blocks queries without tenant context
# ---------------------------------------------------------------------------


async def test_rls_blocks_query_without_tenant_context(postgres_available, rls_probe_role):
    """With the probe role (non-superuser) and NO ``app.tenant_id`` set,
    every ``SELECT`` against a tenanted table returns zero rows.

    Without this, RLS would be a paper tiger.
    """
    async with _conn() as conn:
        async with _as_role(conn, rls_probe_role) as c:
            who = await c.fetchval("SELECT current_user")
            assert who == rls_probe_role, (
                f"role switch failed: current_user={who!r}"
            )

            # Every tenanted table returns 0 rows without a tenant
            # context. The probe role can SELECT, so the only thing
            # that can deny the rows is the RLS policy.
            for schema, table in TENANTED_TABLES:
                n = await c.fetchval(
                    f"SELECT COUNT(*) FROM \"{schema}\".\"{table}\""
                )
                assert n == 0, (
                    f"RLS failed to block: {schema}.{table} returned {n} "
                    f"rows to probe role with no tenant context"
                )


# ---------------------------------------------------------------------------
# 5) RLS allows queries with the correct tenant context
# ---------------------------------------------------------------------------


async def test_rls_with_default_tenant_context_returns_rows(postgres_available, rls_probe_role):
    """With ``app.tenant_id = DEFAULT_TENANT_ID`` the probe role sees
    the full set of backfilled rows.
    """
    async with _conn() as conn:
        async with _as_role(conn, rls_probe_role) as c:
            await c.execute(f"SET LOCAL app.tenant_id = '{DEFAULT_TENANT_ID}'")
            n_users = await c.fetchval("SELECT COUNT(*) FROM public.users")
            assert n_users >= 9, (
                f"expected at least 9 users under default tenant, got {n_users}"
            )
            # And the audit_log (which has 1000+ rows in dev)
            n_audit = await c.fetchval("SELECT COUNT(*) FROM raw.audit_log")
            assert n_audit > 100, (
                f"expected audit_log rows under default tenant, got {n_audit}"
            )


# ---------------------------------------------------------------------------
# 6) RLS denies queries with the wrong tenant context
# ---------------------------------------------------------------------------


async def test_rls_with_other_tenant_context_returns_zero(postgres_available, rls_probe_role):
    """With ``app.tenant_id = OTHER_TENANT_ID`` (a tenant that owns
    no rows), the probe sees zero rows.
    """
    async with _conn() as conn:
        async with _as_role(conn, rls_probe_role) as c:
            await c.execute(f"SET LOCAL app.tenant_id = '{OTHER_TENANT_ID}'")
            for schema, table in TENANTED_TABLES:
                n = await c.fetchval(
                    f"SELECT COUNT(*) FROM \"{schema}\".\"{table}\""
                )
                assert n == 0, (
                    f"RLS leaked: {schema}.{table} returned {n} rows "
                    f"for tenant {OTHER_TENANT_ID}"
                )


# ---------------------------------------------------------------------------
# 7) FORCE RLS — owner-bypass protection
# ---------------------------------------------------------------------------


async def test_force_rls_blocks_even_when_owner_would_bypass(postgres_available):
    """``FORCE ROW LEVEL SECURITY`` means RLS fires even for the table
    owner. We verify the ``pg_class.relforcerowsecurity = TRUE``
    bit is set on every tenanted table.

    The actual owner-bypass behaviour is the same property exercised
    in test_rls_blocks_query_without_tenant_context above, where the
    probe role is a non-superuser but the table owner is ``finbp``.
    """
    from app.db import session as session_mod
    from app.db.session import engine as _engine

    session_mod.reset_engine()
    eng = _engine()
    async with eng.connect() as conn:
        for schema, table in TENANTED_TABLES:
            row = (
                await conn.execute(
                    text(
                        "SELECT relforcerowsecurity FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t"
                    ),
                    {"s": schema, "t": table},
                )
            ).first()
            assert row is not None, f"{schema}.{table} not in pg_class"
            assert row[0] is True, (
                f"{schema}.{table}: FORCE ROW LEVEL SECURITY not set "
                f"(owner could bypass RLS)"
            )
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 8) tenant_session() helper — sets GUCs
# ---------------------------------------------------------------------------


async def test_tenant_session_helper_sets_context(postgres_available):
    """``tenant_session(DEFAULT_TENANT_ID)`` exposes the GUC
    ``app.tenant_id = '00000000-...'`` for the duration of the
    ``async with`` block.
    """
    from app.db import session as session_mod
    from app.db.tenant import tenant_session

    session_mod.reset_engine()
    async with tenant_session(DEFAULT_TENANT_UUID) as sa_session:
        row = (
            await sa_session.execute(
                text("SELECT current_setting('app.tenant_id', true)")
            )
        ).scalar_one()
        assert row == DEFAULT_TENANT_ID, (
            f"app.tenant_id not set: got {row!r}"
        )
        row2 = (
            await sa_session.execute(
                text("SELECT current_setting('app.bypass_rls', true)")
            )
        ).scalar_one()
        assert row2 == "off", f"app.bypass_rls not 'off': got {row2!r}"
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 9) tenant_session() with bypass=True
# ---------------------------------------------------------------------------


async def test_tenant_session_helper_bypass_rls(postgres_available):
    """``tenant_session(..., bypass_rls=True)`` exposes
    ``app.bypass_rls = 'on'`` (M2 will honor this; M1's SQL policy
    doesn't read it yet, so this is forward-compatible plumbing)."""
    from app.db import session as session_mod
    from app.db.tenant import tenant_session

    session_mod.reset_engine()
    async with tenant_session(
        DEFAULT_TENANT_UUID, bypass_rls=True
    ) as sa_session:
        row = (
            await sa_session.execute(
                text("SELECT current_setting('app.bypass_rls', true)")
            )
        ).scalar_one()
        assert row == "on", (
            f"app.bypass_rls not 'on' with bypass_rls=True: got {row!r}"
        )
    session_mod.reset_engine()


# ---------------------------------------------------------------------------
# 10) tenant_session() releases the GUC after the block exits
# ---------------------------------------------------------------------------


async def test_tenant_session_releases_guc_on_exit(postgres_available):
    """``SET LOCAL`` is transaction-scoped: after the ``async with``
    block the GUC is empty. This is the property that makes pooled
    connections safe (no tenant leak between requests).
    """
    from app.db import session as session_mod
    from app.db.session import get_session_factory
    from app.db.tenant import tenant_session

    session_mod.reset_engine()

    async with tenant_session(DEFAULT_TENANT_UUID) as _sa_session:
        # Inside the block, GUC is set.
        pass

    # Outside the block, the SQLAlchemy session is closed. The
    # next ``get_session()`` checkout hands back a connection that
    # should NOT have ``app.tenant_id`` set.
    factory = get_session_factory()
    async with factory() as fresh_session:
        row = (
            await fresh_session.execute(
                text("SELECT current_setting('app.tenant_id', true)")
            )
        ).scalar_one()
        # ``true`` (missing_ok) returns '' when the GUC is unset.
        assert row == "", (
            f"app.tenant_id leaked across sessions: {row!r}"
        )

    session_mod.reset_engine()
