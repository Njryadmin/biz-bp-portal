"""
apps/api/tests/test_migration_runner.py
========================================

Tests for the SQL migration runner (``app.db.migration_runner``)
plus the admin HTTP endpoints (``app.routers.migrations``).

What we cover
-------------
* Listing files in ``infra/migrations`` (lexical order)
* ``ensure_migrations_table`` is idempotent
* ``apply_pending`` runs new migrations, skips already-applied ones,
  supports dry-run, and aborts the batch on the first failure
* Drift detection: applied migration whose on-disk file has changed
* Rollback: a failed migration leaves ``schema_migrations`` unchanged
* Concurrent ``apply_pending`` is serialised by the advisory lock
* Admin-only HTTP endpoints: 403 for non-admin, dry-run works for admin

Test isolation
--------------
The pgserver database is shared across the test session. Each test
clears ``schema_migrations`` before AND after, and uses ``tmp_path``
for its own migration files (so we never touch the real
``infra/migrations/001_rbac_v2.sql`` except in a single end-to-end
test that verifies the real file is listed correctly).

Implementation note
-------------------
All DB-touching tests are written as ``async def`` so they use the
pytest-asyncio loop (configured via ``asyncio_mode = "auto"`` in
pyproject.toml). This sidesteps the "engine bound to a closed event
loop" footgun that hits when mixing ``asyncio.run`` and
``reset_engine()``.
"""
from __future__ import annotations

import hashlib
import socket
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


# ---------------------------------------------------------------------------
# Fixtures: pgserver gate + per-test DB reset
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
            f"migration runner tests skipped"
        )


@pytest.fixture
async def reset_schema_migrations(postgres_available):
    """Clear the ``schema_migrations`` table before AND after each test.

    The table itself is left in place — the next ``ensure_migrations_table``
    call re-uses it. We truncate because the runner's primary contract
    is "the bookkeeping table reflects what's been applied", and we
    want each test to start from a known state.

    Why we call ``reset_engine()`` first: pytest-asyncio creates a
    fresh event loop per async test by default, but SQLAlchemy's
    async engine is bound to the loop that first asked for a
    connection. The previous test's loop is closed by the time this
    fixture runs, so any cached engine will fail with "Event loop
    is closed" on its first ``pool_pre_ping``. Forcing a reset here
    re-creates the engine on THIS test's loop.
    """
    from app.db import session as session_mod
    from app.db.migration_runner import SCHEMA_MIGRATIONS_DDL

    session_mod.reset_engine()
    from app.db.session import engine as _engine

    eng = _engine()
    async with eng.begin() as conn:
        await conn.execute(text(SCHEMA_MIGRATIONS_DDL))
        await conn.execute(text("TRUNCATE TABLE schema_migrations"))
    try:
        yield
    finally:
        # Cleanup: same truncation. The conftest's audit/seed patches
        # are still in scope, so the cleanup runs without DB-side
        # side effects. Reset the engine on the way out too so the
        # NEXT test starts clean.
        session_mod.reset_engine()
        eng = _engine()
        async with eng.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE schema_migrations"))


@pytest.fixture
def temp_migrations_dir(tmp_path: Path) -> Path:
    """A clean ``infra/migrations`` substitute for the test.

    Each test gets its own dir so we never touch the real
    ``001_rbac_v2.sql``. The runner is given this dir explicitly.
    """
    d = tmp_path / "migrations"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_migration(directory: Path, filename: str, sql: str) -> Path:
    """Drop a SQL file into ``directory``. Returns the path."""
    p = directory / filename
    p.write_text(sql, encoding="utf-8")
    return p


def _make_simple_migration(filename: str) -> str:
    """A no-op-but-valid SQL migration.

    Uses ``CREATE TABLE IF NOT EXISTS`` so the file is itself
    idempotent (the runner is also idempotent, but the file should
    be too — see the ``MigrationRunner`` docstring). The table name
    is derived from the filename so two migrations in the same
    directory don't collide.
    """
    table_name = "migration_test_" + hashlib.md5(filename.encode()).hexdigest()[:10]
    return (
        f"CREATE TABLE IF NOT EXISTS {table_name} ("
        f"  id INT PRIMARY KEY, "
        f"  tag TEXT NOT NULL"
        f");\n"
    )


def _make_failing_migration() -> str:
    """SQL that is syntactically valid but logically broken. Will throw
    on execution (UNIQUE violation) and roll the transaction back."""
    return (
        "CREATE TABLE _migration_fails (\n"
        "  id INT PRIMARY KEY\n"
        ");\n"
        "INSERT INTO _migration_fails (id) VALUES (1);\n"
        "INSERT INTO _migration_fails (id) VALUES (1);\n"  # 2nd insert → UNIQUE violation
    )


# ---------------------------------------------------------------------------
# 1) list_migrations
# ---------------------------------------------------------------------------


async def test_list_migrations_returns_real_files(reset_schema_migrations, repo_root):
    """The real ``infra/migrations`` directory contains ``001_rbac_v2.sql``
    (and as of F also ``002_placeholder.sql``). The runner should list
    them in lexical order."""
    from app.db.migration_runner import MigrationRunner

    real_dir = repo_root / "infra" / "migrations"
    runner = MigrationRunner(migrations_dir=real_dir)

    files = await runner.list_migrations()

    versions = [f.version for f in files]
    assert "001_rbac_v2" in versions, f"expected 001_rbac_v2 in {versions}"
    # Lexical order
    assert versions == sorted(versions), f"not sorted: {versions}"


# ---------------------------------------------------------------------------
# 2) ensure_migrations_table — idempotent
# ---------------------------------------------------------------------------


async def test_ensure_migrations_table_idempotent(reset_schema_migrations):
    """Calling ``ensure_migrations_table`` twice in a row must not raise.
    Also: the table must exist after the call."""
    from app.db.migration_runner import MigrationRunner
    from app.db.session import engine as _engine

    runner = MigrationRunner(migrations_dir=Path("/nonexistent"))

    await runner.ensure_migrations_table()
    # Second call: must not raise
    await runner.ensure_migrations_table()

    # The table exists (verifiable by SELECT FROM it)
    eng = _engine()
    async with eng.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = 'schema_migrations'"
                )
            )
        ).first()
    assert row is not None


# ---------------------------------------------------------------------------
# 3) apply_pending — runs new migrations
# ---------------------------------------------------------------------------


async def test_apply_pending_runs_new_migration(
    reset_schema_migrations, temp_migrations_dir
):
    """With an empty ``schema_migrations``, a new file must be applied
    and the row must show up with a non-zero duration."""
    from app.db.migration_runner import MigrationRunner

    _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )

    runner = MigrationRunner(migrations_dir=temp_migrations_dir)
    result = await runner.apply_pending()

    assert result.dry_run is False
    assert len(result.applied) == 1
    assert result.applied[0].version == "001_test_migration"
    assert result.applied[0].duration_ms >= 0
    assert result.skipped == []
    assert result.failed == []


# ---------------------------------------------------------------------------
# 4) apply_pending — skips already applied
# ---------------------------------------------------------------------------


async def test_apply_pending_skips_already_applied(
    reset_schema_migrations, temp_migrations_dir
):
    """Running ``apply_pending`` twice in a row must apply once, then
    skip on the second call (idempotency)."""
    from app.db.migration_runner import MigrationRunner

    _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )

    runner = MigrationRunner(migrations_dir=temp_migrations_dir)
    first = await runner.apply_pending()
    second = await runner.apply_pending()

    assert len(first.applied) == 1
    assert first.skipped == []
    assert second.applied == []
    assert second.skipped == ["001_test_migration"]


# ---------------------------------------------------------------------------
# 5) apply_pending — dry_run
# ---------------------------------------------------------------------------


async def test_apply_pending_dry_run(
    reset_schema_migrations, temp_migrations_dir
):
    """``dry_run=True`` lists what would be applied without touching
    ``schema_migrations``."""
    from app.db.migration_runner import MigrationRunner

    _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )

    runner = MigrationRunner(migrations_dir=temp_migrations_dir)
    result = await runner.apply_pending(dry_run=True)

    assert result.dry_run is True
    assert result.would_apply == ["001_test_migration"]
    assert result.applied == []
    assert result.skipped == []

    # And the table is still empty
    applied = await runner.list_applied()
    assert applied == set()


# ---------------------------------------------------------------------------
# 6) drift detection
# ---------------------------------------------------------------------------


async def test_drift_detection(reset_schema_migrations, temp_migrations_dir):
    """Apply a file, then modify its contents, then re-check status.
    The modified file must be reported as drift with the right kind."""
    from app.db.migration_runner import MigrationRunner

    f = _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )

    runner = MigrationRunner(migrations_dir=temp_migrations_dir)
    await runner.apply_pending()

    # Modify the file on disk after apply
    f.write_text(
        "-- changed post-apply\n" + _make_simple_migration("001_test_migration"),
        encoding="utf-8",
    )

    status = await runner.status()
    assert len(status.drift) == 1
    assert status.drift[0].version == "001_test_migration"
    assert status.drift[0].drift_kind == "checksum_mismatch"
    assert status.drift[0].stored_checksum != status.drift[0].current_checksum

    # Pending is empty (the file is recognised as applied-but-drifted,
    # not as "needs to be applied").
    assert status.pending == []
    # Applied still has the row.
    assert len(status.applied) == 1


# ---------------------------------------------------------------------------
# 7) failed migration rolls back
# ---------------------------------------------------------------------------


async def test_failed_migration_rolls_back(
    reset_schema_migrations, temp_migrations_dir
):
    """A migration that throws must leave ``schema_migrations``
    unchanged (the failed transaction is rolled back, including the
    INSERT into the bookkeeping table).

    We use a multi-file batch so we can also verify that the
    subsequent migration is NOT attempted (batch aborts on failure)."""
    from app.db.migration_runner import MigrationRunner

    _write_migration(
        temp_migrations_dir,
        "001_good_migration.sql",
        _make_simple_migration("001_good_migration"),
    )
    _write_migration(
        temp_migrations_dir,
        "002_bad_migration.sql",
        _make_failing_migration(),
    )
    _write_migration(
        temp_migrations_dir,
        "003_never_attempted.sql",
        _make_simple_migration("003_never_attempted"),
    )

    runner = MigrationRunner(migrations_dir=temp_migrations_dir)

    with pytest.raises(Exception):
        await runner.apply_pending()

    # schema_migrations must only have the FIRST migration (which
    # applied successfully before the second one failed). The third
    # was never attempted.
    applied = await runner.list_applied()
    assert applied == {"001_good_migration"}, (
        f"expected only 001_good_migration to be applied, got {applied}"
    )

    # The failing migration's table is also gone (transaction rolled
    # back). We can verify by SELECT — if the rollback worked, the
    # table is not in the database.
    from app.db.session import engine as _engine

    eng = _engine()
    async with eng.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_name = '_migration_fails'"
                )
            )
        ).first()
    assert row is None


# ---------------------------------------------------------------------------
# 8) concurrent apply — advisory lock serialises
# ---------------------------------------------------------------------------


async def test_advisory_lock_prevents_concurrent_apply(
    reset_schema_migrations, temp_migrations_dir
):
    """Two concurrent ``apply_pending`` calls on the same database must
    serialise on the advisory lock. The total count of applied rows
    in ``schema_migrations`` is still 1 (no double-apply)."""
    import asyncio

    from app.db.migration_runner import MigrationRunner

    _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )

    runner = MigrationRunner(migrations_dir=temp_migrations_dir)

    async def _apply_once() -> list[str]:
        result = await runner.apply_pending()
        return [a.version for a in result.applied]

    # Two concurrent apply_pending calls. asyncio.gather runs them
    # in parallel; the advisory lock inside _apply_one serialises
    # them.
    r1, r2 = await asyncio.gather(_apply_once(), _apply_once())

    # The order between the two calls is undefined; what matters is
    # that the union is {"001_test_migration"} and the total applied
    # across both calls sums to 1.
    union = set(r1) | set(r2)
    total_applied = len(r1) + len(r2)
    assert union == {"001_test_migration"}, f"unexpected union: {union}"
    assert total_applied == 1, f"expected exactly 1 apply, got {total_applied}"

    # And the database has exactly one row
    applied = await runner.list_applied()
    assert applied == {"001_test_migration"}


# ---------------------------------------------------------------------------
# 9) HTTP: admin status endpoint requires admin
# ---------------------------------------------------------------------------


@contextmanager
def _admin_client(postgres_available) -> Iterator[TestClient]:
    from app.core.auth import CurrentUser
    from app.core.rbac import require_admin_dep
    from app.db import session as session_mod
    from app.main import create_app

    app = create_app()
    admin = CurrentUser(
        id=1,
        username="admin",
        display_name="Test Admin",
        email="admin@test.local",
        is_active=True,
        roles=["admin"],
        accessible_lines=[],
    )
    app.dependency_overrides[require_admin_dep] = lambda: admin
    session_mod.reset_engine()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        session_mod.reset_engine()


@contextmanager
def _nonadmin_client(postgres_available) -> Iterator[TestClient]:
    from fastapi import HTTPException, status as http_status
    from app.core.rbac import require_admin_dep
    from app.db import session as session_mod
    from app.main import create_app

    app = create_app()

    async def _failing_dep():
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="admin role required",
        )

    app.dependency_overrides[require_admin_dep] = _failing_dep
    session_mod.reset_engine()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        session_mod.reset_engine()


def test_api_admin_status_requires_admin(postgres_available, reset_schema_migrations):
    """Non-admin caller → 403 on ``GET /api/admin/migrations/status``."""
    with _nonadmin_client(postgres_available) as c:
        r = c.get("/api/admin/migrations/status")
    assert r.status_code == 403, r.text


def test_api_admin_apply_requires_admin(postgres_available, reset_schema_migrations):
    """Non-admin caller → 403 on ``POST /api/admin/migrations/apply``."""
    with _nonadmin_client(postgres_available) as c:
        r = c.post("/api/admin/migrations/apply", json={"dry_run": False})
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# 10) HTTP: apply with dry_run returns "would_apply" without DB changes
# ---------------------------------------------------------------------------


def test_api_admin_apply_with_dry_run(
    postgres_available, reset_schema_migrations, temp_migrations_dir, monkeypatch
):
    """Admin calls ``POST /api/admin/migrations/apply`` with
    ``dry_run=true``. The endpoint must report what it would apply
    and must NOT touch the database.

    The endpoint resolves the migrations dir via
    ``get_project_root() / 'infra' / 'migrations'``. We don't want
    to depend on the real dir for this test, so we monkey-patch the
    module-level ``_default_migrations_dir`` to return our temp dir.
    """
    from app.routers import migrations as migrations_router

    _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )
    monkeypatch.setattr(
        migrations_router, "_default_migrations_dir", lambda: temp_migrations_dir
    )

    with _admin_client(postgres_available) as c:
        r = c.post("/api/admin/migrations/apply", json={"dry_run": True})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["would_apply"] == ["001_test_migration"]
    assert body["applied"] == []
    assert body["failed"] == []

    # The database is untouched: ``schema_migrations`` is still empty.
    # (The test client closed the engine pool, so we re-create it
    # before the verification query.)
    from app.db import session as session_mod
    from app.db.migration_runner import MigrationRunner

    session_mod.reset_engine()
    runner = MigrationRunner(migrations_dir=temp_migrations_dir)
    # We need a fresh loop for the verification query. The TestClient
    # closed its loop, so we use a sync engine.execute via SQLAlchemy
    # directly.
    from app.db.session import engine as _engine

    eng = _engine()
    async def _check_empty() -> set[str]:
        async with eng.connect() as conn:
            rows = (
                await conn.execute(text("SELECT version FROM schema_migrations"))
            ).scalars().all()
        return {str(r) for r in (rows or [])}

    import asyncio

    applied = asyncio.run(_check_empty())
    assert applied == set()


# ---------------------------------------------------------------------------
# 11) HTTP: status endpoint returns the expected shape
# ---------------------------------------------------------------------------


def test_api_admin_status_shape(
    postgres_available, reset_schema_migrations, temp_migrations_dir, monkeypatch
):
    """``GET /api/admin/migrations/status`` must return the
    ``{pending, applied, drift, summary}`` shape even when the DB is
    empty (no migrations applied yet)."""
    from app.routers import migrations as migrations_router

    _write_migration(
        temp_migrations_dir,
        "001_test_migration.sql",
        _make_simple_migration("001_test_migration"),
    )
    monkeypatch.setattr(
        migrations_router, "_default_migrations_dir", lambda: temp_migrations_dir
    )

    with _admin_client(postgres_available) as c:
        r = c.get("/api/admin/migrations/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) >= {"pending", "applied", "drift", "summary"}
    assert body["summary"]["pending_count"] == 1
    assert body["summary"]["applied_count"] == 0
    assert body["summary"]["drift_count"] == 0
    assert body["pending"][0]["version"] == "001_test_migration"
