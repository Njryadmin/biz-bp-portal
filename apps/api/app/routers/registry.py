"""
apps/api/app/routers/registry.py

Dynamic business-line router loader.

Algorithm (runs once at app startup):

1. Read `business_lines/registry.yaml` via `core.registry.load_registry`.
2. For each entry, look for `business_lines/<line_id>/api/router.py`.
3. `importlib.util.spec_from_file_location(...)` + `module_from_spec` + `loader.exec_module`
   to import the module WITHOUT polluting sys.modules under a global name (we
   do register it under `business_lines.<id>.router` for testability).
4. The module is expected to expose one of:
     - `router`  (an `APIRouter`)
     - `app`     (a `FastAPI`)
   The loader picks the first one it finds.
5. The discovered object is mounted on the root app under `<api_prefix>`.

CRITICAL CONSTRAINT: this file MUST NOT mention any specific business-line
name (no string literal like "consumer_loan" / "wealth_mgmt"). All names
are discovered at runtime from the manifest.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException

from ..core.registry import RegistryEntry, load_registry, registry_version
from ..core.logging import get_logger

logger = get_logger(__name__)


def _import_business_line_module(router_path: Path, line_id: str) -> ModuleType | None:
    """Import a business-line router file via importlib.

    Returns the module, or None if the file doesn't exist.
    """
    if not router_path.exists():
        return None
    # Register under a unique sys.modules key so debugging/re-imports are sane.
    module_name = f"business_lines.{line_id}.router"
    spec = importlib.util.spec_from_file_location(module_name, str(router_path))
    if spec is None or spec.loader is None:
        logger.warning("Could not build spec for %s", router_path)
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.error("Failed to import %s: %s", router_path, exc)
        # Don't keep a half-loaded module in sys.modules
        sys.modules.pop(module_name, None)
        raise
    return module


def _find_router_or_app(module: ModuleType) -> Any:
    """Find an APIRouter or FastAPI instance on a module.

    Convention: `router` is preferred, then `app`. We inspect the module's
    own attributes only (no recursion into imported objects).
    """
    for attr in ("router", "app"):
        obj = getattr(module, attr, None)
        if obj is None:
            continue
        if isinstance(obj, (APIRouter, FastAPI)):
            return obj
    return None


def discover_business_line_routers() -> list[tuple[RegistryEntry, Any]]:
    """Return [(entry, router_or_app), ...] for every registered line."""
    out: list[tuple[RegistryEntry, Any]] = []
    for entry in load_registry():
        # The API loader is intentionally generic — it locates files purely
        # by directory layout, never by hardcoded name.
        candidate = entry.manifest_path.parent / "api" / "router.py"
        if not candidate.exists():
            logger.info(
                "Business line '%s' has no api/router.py at %s; skipping",
                entry.line.id,
                candidate,
            )
            continue
        try:
            module = _import_business_line_module(candidate, entry.line.id)
        except Exception:
            # Surface a placeholder 500 path so the rest of the app still
            # boots even if one business line is broken.
            placeholder = APIRouter()

            @placeholder.get("/__error__")
            def _err(line_id: str = entry.line.id) -> dict:
                raise HTTPException(
                    status_code=500,
                    detail=f"Business line '{line_id}' router failed to import. See API logs.",
                )

            out.append((entry, placeholder))
            continue

        if module is None:
            continue
        obj = _find_router_or_app(module)
        if obj is None:
            logger.warning(
                "Business line '%s' module has no APIRouter/FastAPI at module scope",
                entry.line.id,
            )
            continue
        out.append((entry, obj))
    return out


def _summarize_line(entry: RegistryEntry) -> dict:
    """Project a RegistryEntry down to the cockpit-shaped summary.

    The cockpit layout only needs a small projection; full details come from
    `/api/registry/lines/{line_id}`. We add `display_name` (alias of `name`)
    and `indicators_count` here so the Web layout can render without a second
    round-trip per line.
    """
    dumped = entry.line.model_dump()
    dumped["display_name"] = dumped.get("name") or entry.line.id
    dumped["indicators_count"] = len(entry.indicators)
    return dumped


def build_registry_router() -> APIRouter:
    """Build a router that exposes `/api/registry/*` endpoints."""
    r = APIRouter(prefix="/api/registry", tags=["registry"])

    @r.get("/lines")
    async def list_lines() -> dict:
        entries = load_registry()
        return {
            "version": registry_version(),
            "lines": [_summarize_line(e) for e in entries],
        }

    @r.get("/lines/{line_id}")
    async def get_line(line_id: str) -> dict:
        for entry in load_registry():
            if entry.line.id == line_id:
                return {
                    "line": entry.line.model_dump(),
                    "indicators": [i.model_dump() for i in entry.indicators],
                    "charts": [c.model_dump() for c in entry.charts],
                }
        raise HTTPException(status_code=404, detail=f"unknown line_id: {line_id}")

    @r.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return r


def mount_business_line_routers(app: FastAPI) -> None:
    """Mount each discovered business-line router under its api_prefix.

    Mounted at startup (see app.main.lifespan). Errors for one line do not
    prevent the others from mounting.
    """
    for entry, obj in discover_business_line_routers():
        prefix = entry.line.api_prefix
        if isinstance(obj, FastAPI):
            # A FastAPI sub-app: mount it.
            app.mount(prefix, obj)
            logger.info("Mounted business line '%s' (FastAPI sub-app) at %s", entry.line.id, prefix)
        else:
            # APIRouter
            app.include_router(obj, prefix=prefix)
            logger.info("Mounted business line '%s' (APIRouter) at %s", entry.line.id, prefix)
