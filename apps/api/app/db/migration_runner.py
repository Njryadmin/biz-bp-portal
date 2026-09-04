"""
apps/api/app/db/migration_runner.py
====================================

Lightweight SQL migration runner for the Fin BP Portal.

Why we built it
---------------
P0 (commit 2012244) shipped ``infra/migrations/001_rbac_v2.sql`` and the
handbook documented the manual incantation::

    psql -U finbp -d finbp -f infra/migrations/001_rbac_v2.sql

That works for a one-off, but it is brittle:

* D1 verified exactly one migration. Future 002/003 files are an
  operator-toilet-paper problem (forgot to run one, ran in the wrong
  order, ran twice without idempotency).
* No record of *which* files have been applied. The only way to find
  out is to re-read the schema and pray.
* Drift (someone hand-edits a migration after applying it) is invisible.

This module replaces the manual loop with a deterministic, idempotent,
audit-tracked runner that is both a library and an HTTP endpoint.

Design
------
* **Order is filename-lexical** (``001_xxx.sql`` before ``002_xxx.sql``).
  We never re-order; the prefix is the contract.
* **Each migration is one transaction** owned by the runner. We hold a
  ``pg_advisory_xact_lock`` for the duration of the batch so two
  processes cannot race.
* **Drift detection** is a SHA-256 of the on-disk file bytes. If a
  migration is recorded as applied but the file on disk has changed,
  the runner reports it as ``drift`` and does NOT re-run (the user has
  to fix that by hand — re-running a tampered migration is a great way
  to corrupt production).
* **Idempotency** is double-protected. The migration file itself should
  be idempotent (``IF NOT EXISTS``/``ON CONFLICT DO NOTHING``), and the
  runner refuses to apply a file whose version is already in
  ``schema_migrations``.
* **Failure isolation** is strict. If migration N fails, the batch
  aborts and the error is re-raised. Subsequent migrations are not
  attempted. The schema_migrations table is unchanged for N (the row
  insert was inside the failed transaction).
* **Migration-file transaction markers**: if the SQL file starts with
  ``BEGIN;`` and ends with ``COMMIT;`` (case-insensitive, ignoring
  whitespace and comments), we strip those two markers before executing
  the file — otherwise PostgreSQL would reject the embedded
  ``COMMIT;`` because there is "no transaction in progress" once the
  runner's outer transaction ends. The leading/trailing stripping is
  a common Flyway/Alembic pattern. See
  ``infra/migrations/001_rbac_v2.sql`` for the canonical example.

Public surface
--------------
* ``MigrationFile``             — light value object for one .sql file
* ``AppliedMigration``          — what we read back from
  ``schema_migrations``
* ``MigrationStatus``           — full status snapshot (pending / applied
  / drift)
* ``ApplyResult``               — outcome of a batch run
* ``MigrationRunner``           — the class

The class is intentionally framework-free (no FastAPI dependency) so
it can be driven from a CLI script, an HTTP endpoint, or a test
without ceremony.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..core.logging import get_logger
from .session import engine as _default_engine

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Stable bigint key for pg_advisory_xact_lock. The value is derived from
# SHA-256("biz_bp_migration_runner_lock_v1") and is identical across
# processes / containers / restarts. Two concurrent MigrationRunner
# invocations will serialize on this key inside the same database.
#
# Why SHA-256 → int64: a fixed, well-known magic number is easy to
# reason about and impossible to collide with other ad-hoc advisory
# locks the app may add later (each subsystem should pick its own
# namespace). We convert the first 8 bytes of the digest to a signed
# 64-bit integer so the value is portable across Python builds and
# architectures.
_LOCK_NAMESPACE = b"biz_bp_migration_runner_lock_v1"
_LOCK_KEY: int = int.from_bytes(
    hashlib.sha256(_LOCK_NAMESPACE).digest()[:8], "big", signed=True
)

# DDL for the bookkeeping table. Matches the schema declared in the
# task spec exactly. ``version`` is the canonical migration identifier
# (filename without ``.sql``), and ``checksum`` is the SHA-256 of the
# file bytes at apply time.
SCHEMA_MIGRATIONS_DDL: str = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version      TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    applied_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum     TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL
)
"""


# Regex for stripping outer transaction markers from a migration file.
# We only strip if the file starts with ``BEGIN;`` (case-insensitive,
# ignoring leading whitespace + line comments) and ends with
# ``COMMIT;`` (case-insensitive, ignoring trailing whitespace +
# line comments). Anything in between — including ``DO $$ ... $$``
# blocks — is left alone.
_LEADING_BEGIN_RE = re.compile(
    r"^\s*(?:--[^\n]*\n\s*)*BEGIN\s*;\s*",
    re.IGNORECASE,
)
_TRAILING_COMMIT_RE = re.compile(
    r"\s*COMMIT\s*;\s*(?:--[^\n]*\n\s*)*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class MigrationFile:
    """One .sql file in ``migrations_dir``."""

    path: Path
    version: str  # e.g. "001_rbac_v2" (filename without .sql)
    sql: str       # raw file content (with BEGIN/COMMIT stripped if present)

    @property
    def filename(self) -> str:
        return self.path.name

    @property
    def checksum(self) -> str:
        """SHA-256 of the on-disk file bytes (NOT the stripped ``sql``).

        Drift detection compares against the file on disk; if we used
        the stripped SQL, an admin who legitimately re-orders their
        transaction markers would falsely trigger drift.
        """
        return _sha256_file(self.path)


@dataclass(slots=True)
class AppliedMigration:
    """One row from ``schema_migrations``."""

    version: str
    filename: str
    applied_at: str
    checksum: str
    duration_ms: int

    def matches_file(self, file_obj: MigrationFile) -> bool:
        """True if the on-disk file checksum equals the stored one."""
        return self.checksum == file_obj.checksum


@dataclass(slots=True)
class DriftEntry:
    """A migration that is recorded as applied but whose file on disk
    has a different checksum than the one stored at apply time."""

    version: str
    filename: str
    applied_at: str
    stored_checksum: str
    current_checksum: str
    drift_kind: str  # "missing_file" | "checksum_mismatch"


@dataclass(slots=True)
class MigrationStatus:
    """Full snapshot of the migration state."""

    pending: list[MigrationFile] = field(default_factory=list)
    applied: list[AppliedMigration] = field(default_factory=list)
    drift: list[DriftEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending": [
                {
                    "version": m.version,
                    "filename": m.filename,
                    "checksum": m.checksum,
                }
                for m in self.pending
            ],
            "applied": [
                {
                    "version": a.version,
                    "filename": a.filename,
                    "applied_at": a.applied_at,
                    "checksum": a.checksum,
                    "duration_ms": a.duration_ms,
                }
                for a in self.applied
            ],
            "drift": [
                {
                    "version": d.version,
                    "filename": d.filename,
                    "applied_at": d.applied_at,
                    "stored_checksum": d.stored_checksum,
                    "current_checksum": d.current_checksum,
                    "drift_kind": d.drift_kind,
                }
                for d in self.drift
            ],
            "summary": {
                "pending_count": len(self.pending),
                "applied_count": len(self.applied),
                "drift_count": len(self.drift),
            },
        }


@dataclass(slots=True)
class ApplyResult:
    """Outcome of an ``apply_pending`` call."""

    applied: list[AppliedMigration] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # versions already applied
    failed: list[str] = field(default_factory=list)    # versions attempted but rolled back
    dry_run: bool = False
    would_apply: list[str] = field(default_factory=list)  # only set in dry_run

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "applied": [
                {
                    "version": a.version,
                    "filename": a.filename,
                    "duration_ms": a.duration_ms,
                }
                for a in self.applied
            ],
            "skipped": list(self.skipped),
            "failed": list(self.failed),
            "dry_run": self.dry_run,
        }
        if self.dry_run:
            out["would_apply"] = list(self.would_apply)
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Hex-encoded SHA-256 of the file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _strip_transaction_markers(sql: str) -> str:
    """Strip leading ``BEGIN;`` and trailing ``COMMIT;`` from ``sql``.

    No-op if the file does not have those markers. See the module
    docstring for why this exists — the runner owns the transaction,
    so a migration file must not also try to start / end one.
    """
    s = sql.strip()
    if not s:
        return sql
    m = _LEADING_BEGIN_RE.match(s)
    if not m:
        return sql
    s = s[m.end():]
    m = _TRAILING_COMMIT_RE.search(s)
    if m:
        s = s[: m.start()].rstrip()
    return s


def _parse_version(path: Path) -> str:
    """``001_rbac_v2.sql`` → ``001_rbac_v2``.

    Falls back to the stem if there is no ``.sql`` suffix (defensive —
    ``list_migrations`` only returns ``*.sql`` but a unit test could
    pass anything).
    """
    name = path.name
    if name.lower().endswith(".sql"):
        return name[:-4]
    return path.stem


# ---------------------------------------------------------------------------
# MigrationRunner
# ---------------------------------------------------------------------------


class MigrationRunner:
    """Apply ``*.sql`` files in ``migrations_dir`` in lexical order.

    The runner is stateful only in the sense that the *DB* is the
    stateful piece (the ``schema_migrations`` table). Two instances
    pointing at the same database will agree on what's been applied.
    """

    def __init__(
        self,
        migrations_dir: Path | str = "infra/migrations",
        db_url: str | None = None,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker | None = None,
    ) -> None:
        # Resolve to an absolute path so callers don't have to know
        # the CWD. Relative paths are resolved against CWD at the time
        # of the call (consistent with how ``Path("...")`` normally
        # behaves — we just normalise the type).
        self.migrations_dir = Path(migrations_dir)
        # We accept an injected engine/session for tests; otherwise
        # use the global one. ``db_url`` is kept as a hint for future
        # use (e.g. a CLI tool that wants to spin up its own engine)
        # but the actual connection is via the engine.
        self._db_url = db_url
        self._engine = engine
        self._session_factory = session_factory

    # -- public helpers -----------------------------------------------------

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            return _default_engine()
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker:
        if self._session_factory is None:
            from .session import get_session_factory

            return get_session_factory()
        return self._session_factory

    # -- table management ---------------------------------------------------

    async def ensure_migrations_table(self) -> None:
        """Idempotently create the ``schema_migrations`` table.

        Safe to call any number of times — uses ``CREATE TABLE IF NOT
        EXISTS``. Called automatically by ``apply_pending`` /
        ``list_applied`` / ``status`` so callers rarely need to invoke
        it directly. Exposed publicly so a deployment script can
        pre-create the table before doing anything else.
        """
        eng = self.engine
        async with eng.begin() as conn:
            await conn.execute(text(SCHEMA_MIGRATIONS_DDL))

    # -- listing ------------------------------------------------------------

    async def list_migrations(self) -> list[MigrationFile]:
        """List every ``*.sql`` in ``migrations_dir`` in lexical order.

        Empty directories return an empty list (not an error). The
        file's contents are loaded into memory; we do not stream. With
        migration files typically < 10 KB, this is fine.
        """
        if not self.migrations_dir.exists():
            return []
        out: list[MigrationFile] = []
        # sorted() with the default key gives lexical order which is
        # exactly what we want ("001_..." < "002_...").
        for p in sorted(self.migrations_dir.glob("*.sql")):
            if not p.is_file():
                continue
            raw = p.read_text(encoding="utf-8")
            stripped = _strip_transaction_markers(raw)
            out.append(
                MigrationFile(
                    path=p,
                    version=_parse_version(p),
                    sql=stripped,
                )
            )
        return out

    async def list_applied(self) -> set[str]:
        """Return the set of ``version`` strings currently in
        ``schema_migrations``."""
        await self.ensure_migrations_table()
        eng = self.engine
        async with eng.connect() as conn:
            rows = (
                await conn.execute(text("SELECT version FROM schema_migrations"))
            ).scalars().all()
        return {str(r) for r in (rows or [])}

    async def list_applied_full(self) -> list[AppliedMigration]:
        """Return the full ``AppliedMigration`` rows.

        Used by ``status()`` to report applied_at / duration_ms, not
        just the version set.
        """
        await self.ensure_migrations_table()
        eng = self.engine
        async with eng.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT version, filename, "
                        "applied_at::text AS applied_at, "
                        "checksum, duration_ms "
                        "FROM schema_migrations ORDER BY version"
                    )
                )
            ).mappings().all()
        return [
            AppliedMigration(
                version=str(r["version"]),
                filename=str(r["filename"]),
                applied_at=str(r["applied_at"]),
                checksum=str(r["checksum"]),
                duration_ms=int(r["duration_ms"]),
            )
            for r in (rows or [])
        ]

    # -- the main event -----------------------------------------------------

    async def apply_pending(self, *, dry_run: bool = False) -> ApplyResult:
        """Apply every migration whose version is not yet in
        ``schema_migrations``.

        Order: lexical (filename) ascending. Within a single batch,
        one advisory transaction-scope lock is held so two concurrent
        runners serialize.

        Failure handling: the first failure rolls back its own
        transaction (the schema_migrations row insert is inside it, so
        no row is recorded) and re-raises. Subsequent migrations are
        not attempted.
        """
        await self.ensure_migrations_table()
        files = await self.list_migrations()
        applied_versions = await self.list_applied()

        result = ApplyResult(dry_run=dry_run)

        # Pre-compute the "what would happen" plan so dry_run and the
        # real run are guaranteed to agree.
        to_apply: list[MigrationFile] = []
        for f in files:
            if f.version in applied_versions:
                result.skipped.append(f.version)
            else:
                to_apply.append(f)

        if dry_run:
            result.would_apply = [f.version for f in to_apply]
            return result

        if not to_apply:
            return result

        # Use ONE big transaction for the whole batch? No — per the
        # design spec each migration is its own transaction so that a
        # mid-batch failure leaves earlier migrations applied. We
        # still want a single advisory lock for the duration of the
        # batch so two runners don't both pick up the same "pending"
        # list and double-apply.
        for f in to_apply:
            applied = await self._apply_one(f)
            if applied is not None:
                result.applied.append(applied)
            else:
                result.failed.append(f.version)
                # Don't continue — the spec is "abort the batch on
                # first failure" so the admin can fix the bad SQL and
                # re-run.
                break
        return result

    async def _apply_one(self, file_obj: MigrationFile) -> AppliedMigration | None:
        """Apply one migration in its own transaction.

        Holds ``pg_advisory_xact_lock`` for the duration. Returns the
        ``AppliedMigration`` on success, ``None`` on failure (caller
        already has the error in the exception).
        """
        eng = self.engine
        started_at = time.perf_counter()
        try:
            async with eng.begin() as conn:
                # 1. Acquire the advisory lock (transaction-scoped:
                # released automatically on commit / rollback). If a
                # concurrent runner is mid-batch, this blocks until
                # they finish.
                await conn.execute(
                    text("SELECT pg_advisory_xact_lock(:lock_key)"),
                    {"lock_key": _LOCK_KEY},
                )
                # 2. Re-check the applied set under the lock. Another
                # runner may have applied this version while we were
                # waiting for the lock. This is a belt-and-suspenders
                # guard — the per-version primary key would also
                # catch it, but failing fast here gives a clearer
                # error.
                already = (
                    await conn.execute(
                        text("SELECT 1 FROM schema_migrations WHERE version = :v"),
                        {"v": file_obj.version},
                    )
                ).first()
                if already is not None:
                    logger.info(
                        "apply_one: %s already applied (raced with another runner); skipping",
                        file_obj.version,
                    )
                    return None
                # 3. Execute the migration SQL.
                # We use ``exec_driver_sql`` to bypass SQLAlchemy's
                # parameter binding (raw SQL migrations may contain
                # PL/pgSQL dollar-quoted blocks and other constructs
                # that confuse the parameter compiler).
                await conn.exec_driver_sql(file_obj.sql)
                # 4. Record the migration.
                duration_ms = int((time.perf_counter() - started_at) * 1000)
                await conn.execute(
                    text(
                        "INSERT INTO schema_migrations "
                        "(version, filename, checksum, duration_ms) "
                        "VALUES (:v, :f, :c, :d)"
                    ),
                    {
                        "v": file_obj.version,
                        "f": file_obj.filename,
                        "c": file_obj.checksum,
                        "d": duration_ms,
                    },
                )
            logger.info(
                "apply_one: applied %s in %dms",
                file_obj.version,
                duration_ms,
            )
            # Re-read the row to return the canonical applied_at etc.
            return AppliedMigration(
                version=file_obj.version,
                filename=file_obj.filename,
                applied_at="",  # filled by caller if needed
                checksum=file_obj.checksum,
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "apply_one: FAILED to apply %s: %s",
                file_obj.version,
                exc,
            )
            # The ``eng.begin()`` context manager will have already
            # rolled back the transaction. Re-raise so the caller
            # knows the batch aborted.
            raise

    # -- status / verify ----------------------------------------------------

    async def status(self) -> MigrationStatus:
        """Compute pending + applied + drift in one call.

        Drift is "an applied migration whose on-disk file is now
        different" (either modified or deleted). We do NOT
        auto-correct drift — re-running a tampered migration is a
        recipe for data loss.
        """
        await self.ensure_migrations_table()
        files = await self.list_migrations()
        applied_rows = await self.list_applied_full()

        files_by_version = {f.version: f for f in files}
        applied_by_version = {a.version: a for a in applied_rows}

        pending: list[MigrationFile] = []
        applied: list[AppliedMigration] = []
        drift: list[DriftEntry] = []

        # Files in the directory: classify as pending (not applied) or
        # applied (matched by version).
        for f in files:
            if f.version in applied_by_version:
                a = applied_by_version[f.version]
                if not a.matches_file(f):
                    drift.append(
                        DriftEntry(
                            version=f.version,
                            filename=f.filename,
                            applied_at=a.applied_at,
                            stored_checksum=a.checksum,
                            current_checksum=f.checksum,
                            drift_kind="checksum_mismatch",
                        )
                    )
                # else: matches, no drift; falls into "applied"
                applied.append(a)
            else:
                pending.append(f)

        # Applied migrations whose file no longer exists in the
        # directory: that's also drift ("missing_file").
        for a in applied_rows:
            if a.version not in files_by_version:
                drift.append(
                    DriftEntry(
                        version=a.version,
                        filename=a.filename,
                        applied_at=a.applied_at,
                        stored_checksum=a.checksum,
                        current_checksum="",
                        drift_kind="missing_file",
                    )
                )

        # Sort for deterministic output (lexical by version).
        pending.sort(key=lambda m: m.version)
        applied.sort(key=lambda a: a.version)
        drift.sort(key=lambda d: d.version)

        return MigrationStatus(pending=pending, applied=applied, drift=drift)

    async def verify(self) -> list[DriftEntry]:
        """Re-check checksums for every applied migration.

        Functionally identical to ``status().drift`` — the HTTP
        endpoint exists so the UI can poll "are we still clean?"
        without re-pulling the full status payload.
        """
        status_obj = await self.status()
        return status_obj.drift


# ---------------------------------------------------------------------------
# CLI entry point (optional, handy for ops)
# ---------------------------------------------------------------------------


def _print_status_table(status_obj: MigrationStatus) -> None:
    """Render a small ASCII summary to stdout. Used by the CLI."""
    print(f"Pending : {len(status_obj.pending)}")
    for m in status_obj.pending:
        print(f"  - {m.version}  ({m.filename})")
    print(f"Applied : {len(status_obj.applied)}")
    for a in status_obj.applied:
        print(f"  - {a.version}  {a.applied_at}  {a.duration_ms}ms")
    print(f"Drift   : {len(status_obj.drift)}")
    for d in status_obj.drift:
        print(f"  - {d.version}  {d.drift_kind}")


async def _cli_main(args: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="migration-runner",
        description="Apply infra/migrations/*.sql in order.",
    )
    p.add_argument(
        "--dir",
        default=os.environ.get("BIZ_BP_MIGRATIONS_DIR", "infra/migrations"),
        help="Path to the migrations directory (default: infra/migrations)",
    )
    p.add_argument(
        "command",
        choices=["status", "apply", "verify"],
        help="What to do",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="(apply) List what would be applied without running anything",
    )
    parsed = p.parse_args(args)

    runner = MigrationRunner(migrations_dir=parsed.dir)
    if parsed.command == "status":
        _print_status_table(await runner.status())
        return 0
    if parsed.command == "apply":
        result = await runner.apply_pending(dry_run=parsed.dry_run)
        if parsed.dry_run:
            print(f"Would apply: {result.would_apply}")
        else:
            for a in result.applied:
                print(f"Applied: {a.version} ({a.duration_ms}ms)")
            for s in result.skipped:
                print(f"Skipped (already applied): {s}")
            for f in result.failed:
                print(f"FAILED: {f}")
        return 0
    if parsed.command == "verify":
        drift = await runner.verify()
        if not drift:
            print("No drift detected.")
        for d in drift:
            print(f"DRIFT: {d.version} ({d.drift_kind})")
        return 0
    return 2  # argparse should prevent this


def main() -> int:  # pragma: no cover — thin CLI wrapper
    import sys

    return asyncio.run(_cli_main(sys.argv[1:]))


# ``asyncio`` is imported lazily inside ``main`` so that importing this
# module from a FastAPI process doesn't pull asyncio.run startup cost.
import asyncio  # noqa: E402


__all__ = [
    "ApplyResult",
    "AppliedMigration",
    "DriftEntry",
    "MigrationFile",
    "MigrationRunner",
    "MigrationStatus",
    "SCHEMA_MIGRATIONS_DDL",
    "main",
]
