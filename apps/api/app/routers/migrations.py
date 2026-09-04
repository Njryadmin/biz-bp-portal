"""
apps/api/app/routers/migrations.py
====================================

Admin HTTP endpoints around the SQL migration runner.

Background
----------
The runner itself (``app.db.migration_runner``) is framework-free and
can be driven from a CLI or a test. This router exposes three
admin-only endpoints so the dashboard / admin UI can:

* ``GET  /api/admin/migrations/status``   — see what's pending, applied,
                                             and any drift.
* ``POST /api/admin/migrations/apply``    — run pending migrations,
                                             optionally as a dry run.
* ``POST /api/admin/migrations/verify``   — re-check checksums without
                                             applying anything.

Authorization
-------------
All three endpoints are gated by ``require_admin_dep`` — same as
``/api/admin/business-lines`` (D1, 2026-09-04). Drift / pending status
is a deployment-quality signal and should never be exposed to a
non-admin (it leaks the migration history which is internal state).

Dry-run safety
--------------
``apply`` with ``dry_run=true`` does NOT acquire the advisory lock and
does NOT write to ``schema_migrations``. It returns the same
``ApplyResult`` shape as a real apply, just with ``dry_run: true`` and
a ``would_apply`` list. This is the only safe way to preview what an
apply would do without side effects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core.auth import CurrentUser
from ..core.logging import get_logger
from ..core.rbac import require_admin_dep
from ..core.registry import get_project_root
from ..db.migration_runner import MigrationRunner

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(
    prefix="/api/admin/migrations",
    tags=["admin", "migrations"],
    dependencies=[],  # per-endpoint auth (admin only)
)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ApplyMigrationsRequest(BaseModel):
    """POST body for ``/apply``.

    ``dry_run`` is the only knob we expose. The runner is responsible
    for ordering and skip logic — the client just says "go" (or
    "preview").
    """

    dry_run: bool = Field(
        default=False,
        description="If true, list what would be applied without running anything.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_migrations_dir() -> Path:
    """Resolve the default ``infra/migrations`` directory.

    Uses ``get_project_root()`` so the endpoint works regardless of the
    process's CWD. The directory is allowed to be missing (e.g. an
    environment that doesn't ship migrations) — the runner treats that
    as "no migrations" and returns an empty status.
    """
    return get_project_root() / "infra" / "migrations"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="Admin: list pending / applied / drift SQL migrations",
)
async def get_migration_status(
    _user: CurrentUser = Depends(require_admin_dep),
) -> dict[str, Any]:
    """Return the current migration state.

    Response shape::

        {
          "pending": [ {version, filename, checksum}, ... ],
          "applied": [ {version, filename, applied_at, checksum, duration_ms}, ... ],
          "drift":   [ {version, filename, applied_at, stored_checksum, current_checksum, drift_kind}, ... ],
          "summary": {pending_count, applied_count, drift_count}
        }

    ``drift_kind`` is either ``"checksum_mismatch"`` (file modified on
    disk) or ``"missing_file"`` (file deleted from disk). Both mean
    "an operator touched something they shouldn't have"; the runner
    will not auto-correct either case.
    """
    runner = MigrationRunner(migrations_dir=_default_migrations_dir())
    status_obj = await runner.status()
    return status_obj.to_dict()


@router.post(
    "/apply",
    summary="Admin: apply pending SQL migrations (or preview with dry_run=true)",
)
async def apply_migrations(
    body: ApplyMigrationsRequest,
    _user: CurrentUser = Depends(require_admin_dep),
) -> dict[str, Any]:
    """Run the migration runner.

    With ``dry_run=true`` returns the same response shape as a real
    apply, but ``dry_run`` is ``true`` in the body and ``would_apply``
    carries the list of versions that WOULD have been applied. The
    schema_migrations table is untouched and the advisory lock is not
    acquired.

    With ``dry_run=false`` (default) actually applies pending
    migrations. If a migration fails, the batch aborts and the response
    is a 500 with the failure reason in ``detail``; the
    ``schema_migrations`` table is unchanged (the failed migration's
    transaction was rolled back).
    """
    runner = MigrationRunner(migrations_dir=_default_migrations_dir())
    try:
        result = await runner.apply_pending(dry_run=body.dry_run)
    except Exception as exc:  # noqa: BLE001
        logger.exception("migration apply failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"migration apply failed: {exc}",
        ) from exc
    return result.to_dict()


@router.post(
    "/verify",
    summary="Admin: re-verify migration checksums (drift detection only)",
)
async def verify_migrations(
    _user: CurrentUser = Depends(require_admin_dep),
) -> dict[str, Any]:
    """Recompute the checksum of every applied migration's file and
    return any drift.

    Functionally a thin wrapper over ``MigrationRunner.verify()``. We
    expose it as its own endpoint so a UI can poll for drift without
    re-pulling the full status payload (applied migrations are
    potentially large).
    """
    runner = MigrationRunner(migrations_dir=_default_migrations_dir())
    drift = await runner.verify()
    return {
        "drift_count": len(drift),
        "drift": [
            {
                "version": d.version,
                "filename": d.filename,
                "applied_at": d.applied_at,
                "stored_checksum": d.stored_checksum,
                "current_checksum": d.current_checksum,
                "drift_kind": d.drift_kind,
            }
            for d in drift
        ],
    }


__all__ = ["router"]
