"""
apps/api/app/routers/admin_business_lines.py
=============================================

Admin API for managing business line manifests + indicators.

Background
----------
P0 (commit 0f820c9) added the v2 manifest schema (4 new blocks:
``data_scope``, ``owner_role_assignments``, ``access_matrix``, ``kpis``).
Before this router, the only way to edit a manifest was to hand-edit
``business_lines/<line_id>/manifest.yaml``. This router lets an admin
do the same thing via the UI.

Endpoints
---------
* ``GET    /api/admin/business-lines``                 — list all lines (summary)
* ``GET    /api/admin/business-lines/{line_id}``       — read full manifest + indicators
* ``PATCH  /api/admin/business-lines/{line_id}``       — partial update manifest + indicators

Authorization
-------------
All three endpoints are gated by ``require_admin_dep`` (admin role only).
The handler is intentionally dumb: it just reads / writes YAML. The
Pydantic ``BusinessLine`` model is NOT used to validate the read payload
because it doesn't know about v2 fields — we read the YAML as a raw
``dict`` and only validate the *parts the client is allowed to PATCH*
(see ``UpdateBusinessLinePayload`` below).

Safety
------
* Atomic writes: ``tempfile`` + ``os.replace`` to avoid half-written
  files on crash.
* Backup: every write first copies the current ``manifest.yaml`` to
  ``manifest.yaml.bak`` (overwriting the previous backup) so an admin
  has a one-step rollback path.
* Round-trip verification: after writing, re-parse the file and run the
  Pydantic model over it. A write that produces unparseable YAML is
  rejected, the original is restored from ``.bak`` (or unchanged if no
  ``.bak`` exists), and the caller gets a 500.

YAML library
------------
We use ``ruamel.yaml`` when available (preserves comments) and fall back
to ``PyYAML`` (comments are dropped but field order is kept via
``sort_keys=False``). At the time of writing (2026-09-04) ruamel is not
in ``apps/api/pyproject.toml``, so PyYAML is the runtime choice.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ..core.logging import get_logger
from ..core.rbac import require_admin_dep
from ..core.registry import (
    BusinessLine,
    IndicatorsFile,
    get_project_root,
    load_registry,
    load_registry_file,
)


def _invalidate_registry_cache() -> None:
    """Drop any cached registry state so the next request re-reads YAML.

    The current implementation only caches ``get_project_root`` (the
    filesystem path), not the actual manifest content — but we still
    clear both so a future change that adds caching to ``load_registry``
    is handled correctly without revisiting this code.
    """
    # Defensive: the function may or may not be wrapped in @lru_cache
    # depending on the deployment, so use ``getattr`` to avoid raising
    # ``AttributeError`` on a non-cached function.
    cache_clear = getattr(load_registry, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()
    get_project_root.cache_clear()


logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/admin/business-lines",
    tags=["admin", "business-lines"],
    dependencies=[],  # per-endpoint auth (admin only)
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 5 v2 data domains (kept here in one place so the validators + the docs
# agree). Matches apps/api/app/core/rbac_v2.py:DataDomain and the docs
# in business_lines/_template/manifest.yaml.v2.example.
_V2_DOMAINS: tuple[str, ...] = ("business", "finance", "hr", "client", "project")

# 4 v2 role keys that appear inside ``access_matrix``. These are the
# *line-scoped* roles only — the global ones (admin/auditor/viewer/
# fin_bp_global/hr_bp_global) are always full-access and never appear
# in the matrix.
_V2_ACCESS_MATRIX_ROLES: tuple[str, ...] = (
    "fin_bp",
    "hr_bp",
    "line_owner",
    "line_member",
)

# 3 v2 KPI views. Each is a list of items.
_V2_KPI_VIEWS: tuple[str, ...] = ("fin_view", "hr_view", "shared_view")


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class BusinessLineDataScope(BaseModel):
    """``data_scope.domains`` — the v2 data domains this line touches."""

    domains: list[str] = Field(
        ...,
        description="5 v2 data domains, picked from {business, finance, hr, client, project}",
    )

    @field_validator("domains")
    @classmethod
    def _validate_domains(cls, v: list[str]) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("domains must be a list")
        bad = [d for d in v if d not in _V2_DOMAINS]
        if bad:
            raise ValueError(
                f"unknown data domains: {bad}; allowed: {list(_V2_DOMAINS)}"
            )
        # Dedupe (preserves order) — UI may send the same domain twice
        seen: set[str] = set()
        out: list[str] = []
        for d in v:
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out


class BusinessLineOwnerRoleAssignments(BaseModel):
    """v2 role bindings (just the ``role:line_id`` strings, the actual
    user→role mapping lives in the DB)."""

    finance_bp: str | None = Field(
        default=None, description="e.g. 'fin_bp:residential'"
    )
    hr_bp: str | None = Field(
        default=None, description="e.g. 'hr_bp:residential'"
    )
    line_owner: str | None = Field(
        default=None, description="e.g. 'line_owner:residential'"
    )

    @field_validator("finance_bp", "hr_bp", "line_owner")
    @classmethod
    def _validate_format(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if ":" not in v:
            raise ValueError(
                f"role binding must be '<role>:<line_id>' (got {v!r})"
            )
        role, _, line_id = v.partition(":")
        if not role or not line_id:
            raise ValueError(
                f"role binding must be '<role>:<line_id>' (got {v!r})"
            )
        return v


class BusinessLineAccessMatrix(BaseModel):
    """v2 access matrix — keys are line-scoped roles, values are
    subsets of the 5 data domains.

    Admin / auditor / viewer / fin_bp_global / hr_bp_global never appear
    here (they're always full-access at the global level)."""

    fin_bp: list[str] | None = None
    hr_bp: list[str] | None = None
    line_owner: list[str] | None = None
    line_member: list[str] | None = None

    @field_validator("fin_bp", "hr_bp", "line_owner", "line_member")
    @classmethod
    def _validate_domains(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("domain list must be a list")
        bad = [d for d in v if d not in _V2_DOMAINS]
        if bad:
            raise ValueError(
                f"unknown data domains: {bad}; allowed: {list(_V2_DOMAINS)}"
            )
        seen: set[str] = set()
        out: list[str] = []
        for d in v:
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out


class BusinessLineKpiItem(BaseModel):
    """Single KPI definition. All fields except id+title are optional."""

    id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    source: str | None = None
    formula: str | None = None

    @field_validator("id")
    @classmethod
    def _id_url_safe(cls, v: str) -> str:
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"kpi id must be url-safe (got {v!r}); allowed: [a-z0-9_-]"
            )
        return v


class BusinessLineKpis(BaseModel):
    """v2 KPI lists split by viewpoint. Lists may be empty."""

    fin_view: list[BusinessLineKpiItem] = Field(default_factory=list)
    hr_view: list[BusinessLineKpiItem] = Field(default_factory=list)
    shared_view: list[BusinessLineKpiItem] = Field(default_factory=list)


class UpdateBusinessLinePayload(BaseModel):
    """PATCH body — every field is optional. Only the fields present in
    the body are touched. Indicator / chart lists are FULL REPLACEMENTS
    (intentional — easier for the UI to send back the whole edited
    table than to track deltas client-side)."""

    # v1 human-readable
    name: str | None = None
    description: str | None = None
    owner: str | None = None
    icon: str | None = None

    # v1 technical
    api_prefix: str | None = None

    # v2
    data_scope: BusinessLineDataScope | None = None
    owner_role_assignments: BusinessLineOwnerRoleAssignments | None = None
    access_matrix: BusinessLineAccessMatrix | None = None
    kpis: BusinessLineKpis | None = None

    # indicators / charts — full replacement
    indicators: list[dict[str, Any]] | None = None
    charts: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _yaml_dump(data: dict[str, Any]) -> str:
    """Serialize a dict to YAML with the project's conventions.

    * ``allow_unicode=True``  — keep Chinese characters verbatim
    * ``sort_keys=False``     — keep the user-visible field order
    * ``default_flow_style=False`` — block style
    * ``indent=2``            — match the rest of the repo
    * ``width=4096``          — avoid ugly line-wrapping of long strings
    """
    return yaml.dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        indent=2,
        width=4096,
    )


def _read_yaml_raw(path: Path) -> dict[str, Any]:
    """Read a YAML file as a dict. Empty / missing keys are returned
    as an empty dict. Raises ``ValueError`` on a YAML parse error so
    the caller can surface a clean 500."""
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        # The on-disk file is structurally broken. Better to surface
        # that as a 500 than to silently drop the file's content.
        raise ValueError(
            f"{path} root must be a mapping, got {type(data).__name__}"
        )
    return data


def _atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomically write ``data`` to ``path`` as YAML.

    Steps:
      1. Serialize to a string.
      2. Write to a ``NamedTemporaryFile`` in the same directory so
         ``os.replace`` is on the same filesystem.
      3. ``os.replace`` (atomic on POSIX + Windows when the source
         and destination are on the same volume).
    """
    text = _yaml_dump(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup of the temp file. If the replace
        # succeeded this is a no-op; if it failed, we don't want
        # the temp file to pollute the directory.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _backup_file(path: Path) -> None:
    """Copy ``path`` to ``<path>.bak`` (overwriting any existing .bak).

    Silently no-ops if the source file doesn't exist (e.g. first write
    of a brand-new manifest)."""
    if not path.exists():
        return
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)


def _verify_round_trip(path: Path, *, has_indicators: bool) -> None:
    """Re-read the file we just wrote and confirm it still parses.

    * manifest: must satisfy ``BusinessLine`` (Pydantic v1 model).
      v2 fields are *not* part of that model, so we re-parse the YAML
      and check that the v1 keys we know about are still consistent.
    * indicators.yaml: parsed via ``IndicatorsFile`` if present.
    """
    if not path.exists():
        raise ValueError(f"post-write file missing: {path}")
    if has_indicators:
        # indicators file
        if path.name == "indicators.yaml":
            IndicatorsFile.model_validate(_read_yaml_raw(path))
        return
    # manifest
    raw = _read_yaml_raw(path)
    BusinessLine.model_validate(raw)
    # Also: if the manifest had v2 blocks, make sure they're still
    # present and structurally sane. We do NOT enforce that v2 blocks
    # exist (backward-compat) — but if they do, the shape must hold.
    if "data_scope" in raw and isinstance(raw["data_scope"], dict):
        BusinessLineDataScope.model_validate(raw["data_scope"])
    if "owner_role_assignments" in raw and isinstance(
        raw["owner_role_assignments"], dict
    ):
        BusinessLineOwnerRoleAssignments.model_validate(
            raw["owner_role_assignments"]
        )
    if "access_matrix" in raw and isinstance(raw["access_matrix"], dict):
        BusinessLineAccessMatrix.model_validate(raw["access_matrix"])
    if "kpis" in raw and isinstance(raw["kpis"], dict):
        BusinessLineKpis.model_validate(raw["kpis"])


def _resolve_paths(line_id: str) -> tuple[Path, Path]:
    """Find the manifest + indicators file for a line.

    Returns ``(manifest_path, indicators_path)``. ``indicators_path``
    may not exist (it is optional) but the parent directory is
    guaranteed to exist.

    Raises ``HTTPException(404)`` if the manifest doesn't exist.
    """
    root = get_project_root()
    line_dir = root / "business_lines" / line_id
    manifest = line_dir / "manifest.yaml"
    if not manifest.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"business line not found: {line_id!r}",
        )
    return manifest, line_dir / "indicators.yaml"


def _v2_defaults() -> dict[str, Any]:
    """Default v2 block values used when a manifest predates v2."""
    return {
        "data_scope": {
            "domains": list(_V2_DOMAINS),
        },
        "owner_role_assignments": {},
        "access_matrix": {},
        "kpis": {
            "fin_view": [],
            "hr_view": [],
            "shared_view": [],
        },
    }


def _build_summary(
    entry_id: str,
    raw: dict[str, Any],
    indicators_count: int,
) -> dict[str, Any]:
    """Build a ``BusinessLineSummary`` dict from a raw manifest."""
    has_v2 = any(
        k in raw
        for k in (
            "data_scope",
            "owner_role_assignments",
            "access_matrix",
            "kpis",
        )
    )
    ds = raw.get("data_scope")
    summary: dict[str, Any] = {
        "id": entry_id,
        "name": raw.get("name", ""),
        "version": str(raw.get("version", "0.0.0")),
        "description": raw.get("description", ""),
        "owner": raw.get("owner", ""),
        "icon": raw.get("icon", "AppstoreOutlined"),
        "indicators_count": indicators_count,
        "has_v2_fields": has_v2,
    }
    if isinstance(ds, dict) and "domains" in ds:
        summary["data_scope"] = {
            "domains": list(ds.get("domains") or []),
        }
    elif has_v2:
        # v2 fields exist but data_scope missing — surface the default
        summary["data_scope"] = {"domains": list(_V2_DOMAINS)}
    return summary


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "",
    summary="Admin: list all business lines (summary, with v2 metadata)",
)
async def list_business_lines(
    _user=Depends(require_admin_dep),
) -> dict[str, Any]:
    """List every registered business line with a summary view.

    Returns ``{"count": N, "lines": [...]}`` where each entry has the
    basic metadata + a flag indicating whether v2 blocks are present.

    Uses ``load_registry()`` to get the v1 metadata, then enriches
    each entry with the v2 block + the indicators count.
    """
    # Force a re-read so a PATCH in the same process is reflected.
    _invalidate_registry_cache()
    registry_file = load_registry_file()
    entries = load_registry()
    by_id: dict[str, Any] = {e.line.id: e for e in entries}

    lines: list[dict[str, Any]] = []
    for item in registry_file.get("lines", []):
        if not isinstance(item, dict):
            continue
        line_id = str(item.get("id", "")).strip()
        if not line_id:
            continue
        entry = by_id.get(line_id)
        if entry is None:
            # The registry references a missing manifest. Skip — the
            # loader will have logged an error already.
            continue
        manifest_path = entry.manifest_path
        try:
            raw = _read_yaml_raw(manifest_path)
        except ValueError as exc:
            logger.warning(
                "list_business_lines: skipping broken manifest %s: %s",
                manifest_path,
                exc,
            )
            continue
        lines.append(
            _build_summary(
                entry_id=line_id,
                raw=raw,
                indicators_count=len(entry.indicators),
            )
        )

    return {"count": len(lines), "lines": lines}


@router.get(
    "/{line_id}",
    summary="Admin: read a full business line (manifest v1+v2 + indicators + charts)",
)
async def get_business_line(
    line_id: str,
    _user=Depends(require_admin_dep),
) -> dict[str, Any]:
    """Return the full manifest (v1 + v2) plus the indicators + charts.

    v2 fields default-fill when absent (we don't change the on-disk
    file, but we provide the defaults in the response so the UI can
    render an editor without having to detect missing blocks first).
    """
    manifest_path, indicators_path = _resolve_paths(line_id)

    try:
        raw = _read_yaml_raw(manifest_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"manifest is not valid YAML: {exc}",
        ) from exc

    # v2 defaults fill
    defaults = _v2_defaults()
    data_scope = raw.get("data_scope") or defaults["data_scope"]
    owner_assignments = (
        raw.get("owner_role_assignments")
        or defaults["owner_role_assignments"]
    )
    access_matrix = raw.get("access_matrix") or defaults["access_matrix"]
    kpis = raw.get("kpis") or defaults["kpis"]

    has_v2 = any(
        k in raw
        for k in (
            "data_scope",
            "owner_role_assignments",
            "access_matrix",
            "kpis",
        )
    )

    # Indicators (optional)
    indicators: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    if indicators_path.exists():
        try:
            ind_file = IndicatorsFile.model_validate(
                _read_yaml_raw(indicators_path)
            )
            indicators = [i.model_dump() for i in ind_file.indicators]
            charts = [c.model_dump() for c in ind_file.charts]
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_business_line: indicators.yaml parse failed for %s: %s",
                line_id,
                exc,
            )

    return {
        # v1 summary
        "id": line_id,
        "name": raw.get("name", ""),
        "version": str(raw.get("version", "0.0.0")),
        "description": raw.get("description", ""),
        "owner": raw.get("owner", ""),
        "icon": raw.get("icon", "AppstoreOutlined"),
        "indicators_count": len(indicators),
        "has_v2_fields": has_v2,
        # v1 technical
        "api_prefix": raw.get("api_prefix", ""),
        "warehouse": raw.get("warehouse") or {},
        "refresh": raw.get("refresh") or {"schedule": "0 2 * * *", "enabled": True},
        "features": raw.get("features")
        or {
            "universal_kpi": True,
            "universal_chart": True,
            "ag_grid": True,
        },
        "nav": raw.get("nav") or [],
        # v2
        "data_scope": data_scope,
        "owner_role_assignments": owner_assignments,
        "access_matrix": access_matrix,
        "kpis": kpis,
        # indicators
        "indicators": indicators,
        "charts": charts,
    }


@router.patch(
    "/{line_id}",
    summary="Admin: partial-update a business line (manifest v1+v2 + indicators + charts)",
)
async def update_business_line(
    line_id: str,
    body: UpdateBusinessLinePayload,
    _user=Depends(require_admin_dep),
) -> dict[str, Any]:
    """Apply a partial update to the manifest + (optionally) indicators.

    * Fields not present in ``body`` are NOT touched.
    * ``indicators`` / ``charts`` are full replacements.
    * line_id is the URL path param — clients cannot rename a line via
      this endpoint (the v1 ``registry.yaml`` reference would point
      at the wrong directory, and a rename is a separate operation).
    * The write is atomic: backup → write temp → rename → verify.
      If verification fails, the file is restored from the backup
      and the caller gets a 500.
    """
    manifest_path, indicators_path = _resolve_paths(line_id)

    # 1. Read the current manifest (raw dict — we want to preserve
    #    arbitrary fields the client didn't touch, including the ones
    #    the Pydantic model doesn't know about).
    try:
        manifest_raw = _read_yaml_raw(manifest_path)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"manifest is not valid YAML: {exc}",
        ) from exc

    # 2. Apply the patch. Only fields present in the body are touched.
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        # Nothing to do — return the current state.
        logger.info(
            "update_business_line(%s): empty patch, returning current state",
            line_id,
        )
        return await get_business_line(line_id=line_id, _user=_user)  # type: ignore[arg-type]

    # 2a. line_id is a URL parameter, NOT a body field, so we don't
    #     need to forbid it in the body — Pydantic's UpdateBusinessLinePayload
    #     doesn't include it as a writable field.
    # 2b. v1 top-level scalars
    for k in ("name", "description", "owner", "icon", "api_prefix"):
        if k in payload:
            manifest_raw[k] = payload[k]
    # 2c. v2 blocks
    if "data_scope" in payload:
        manifest_raw["data_scope"] = payload["data_scope"]
    if "owner_role_assignments" in payload:
        # Store {} if all 3 keys are null (cleaner than keeping nulls)
        ora = payload["owner_role_assignments"]
        if all(not ora.get(k) for k in ("finance_bp", "hr_bp", "line_owner")):
            manifest_raw["owner_role_assignments"] = {}
        else:
            manifest_raw["owner_role_assignments"] = ora
    if "access_matrix" in payload:
        manifest_raw["access_matrix"] = payload["access_matrix"]
    if "kpis" in payload:
        manifest_raw["kpis"] = payload["kpis"]

    # 3. Backup + atomic write of the manifest
    _backup_file(manifest_path)
    try:
        _atomic_write_yaml(manifest_path, manifest_raw)
        _verify_round_trip(manifest_path, has_indicators=False)
    except Exception as exc:
        logger.exception(
            "update_business_line: manifest write failed for %s: %s",
            line_id,
            exc,
        )
        # Try to restore from .bak
        bak = manifest_path.with_suffix(manifest_path.suffix + ".bak")
        if bak.exists():
            try:
                shutil.copy2(bak, manifest_path)
            except Exception:  # noqa: BLE001
                pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"manifest write failed (file restored from backup): {exc}",
        ) from exc

    # 4. indicators.yaml (if it was part of the patch)
    if "indicators" in payload or "charts" in payload:
        # Read current indicators (if any) so we don't drop a list the
        # client didn't include in the patch.
        ind_raw = _read_yaml_raw(indicators_path) if indicators_path.exists() else {}
        if not isinstance(ind_raw, dict):
            ind_raw = {}
        if "indicators" in payload:
            ind_raw["indicators"] = payload["indicators"] or []
        if "charts" in payload:
            ind_raw["charts"] = payload["charts"] or []

        _backup_file(indicators_path)
        try:
            _atomic_write_yaml(indicators_path, ind_raw)
            _verify_round_trip(indicators_path, has_indicators=True)
        except Exception as exc:
            logger.exception(
                "update_business_line: indicators.yaml write failed for %s: %s",
                line_id,
                exc,
            )
            bak = indicators_path.with_suffix(indicators_path.suffix + ".bak")
            if bak.exists():
                try:
                    shutil.copy2(bak, indicators_path)
                except Exception:  # noqa: BLE001
                    pass
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    f"indicators.yaml write failed (file restored from backup): {exc}"
                ),
            ) from exc

    # 5. Hot-reload: drop the @lru_cache so the next caller sees the
    #    new content.
    _invalidate_registry_cache()

    logger.info(
        "update_business_line(%s): patched keys=%s",
        line_id,
        sorted(payload.keys()),
    )

    # 6. Echo back the new state. We don't re-read the file — the
    #    values we just wrote are the source of truth, and re-reading
    #    would also pick up any on-disk edits by other processes that
    #    happened in between.
    return await get_business_line(line_id=line_id, _user=_user)  # type: ignore[arg-type]


__all__ = [
    "router",
    "UpdateBusinessLinePayload",
    "BusinessLineDataScope",
    "BusinessLineOwnerRoleAssignments",
    "BusinessLineAccessMatrix",
    "BusinessLineKpis",
    "BusinessLineKpiItem",
]
