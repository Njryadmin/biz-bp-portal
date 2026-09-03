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

from fastapi import APIRouter, Depends, FastAPI, HTTPException

from ..core.auth import CurrentUser, get_current_user
from ..core.rbac import (
    business_line_router_guard,
    filter_accessible_lines,
)
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
    async def list_lines(
        user: CurrentUser = Depends(get_current_user),
    ) -> dict:
        entries = load_registry()
        all_ids = [e.line.id for e in entries]
        allowed_ids = set(filter_accessible_lines(user, all_ids))
        filtered = [e for e in entries if e.line.id in allowed_ids]
        return {
            "version": registry_version(),
            "lines": [_summarize_line(e) for e in filtered],
            "total_registered": len(entries),
        }

    @r.get("/lines/{line_id}")
    async def get_line(
        line_id: str,
        user: CurrentUser = Depends(get_current_user),
    ) -> dict:
        # Enforce access via the same rule as list_lines.
        all_ids = [e.line.id for e in load_registry()]
        if line_id not in set(filter_accessible_lines(user, all_ids)):
            raise HTTPException(
                status_code=403,
                detail=f"no access to business line: {line_id}",
            )
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

    RBAC: each mounted line router is wrapped with a guard dependency
    that requires the caller to be authenticated AND to have access to
    the specific line id. So a user with only ``bp:residential`` cannot
    hit ``/api/lines/retail/...`` even if they know the URL.
    """
    for entry, obj in discover_business_line_routers():
        prefix = entry.line.api_prefix
        line_id = entry.line.id
        if isinstance(obj, FastAPI):
            # A FastAPI sub-app: we cannot inject deps via include_router
            # for mounts, so add a Starlette middleware on the sub-app
            # that enforces auth + line access. We attach the line id
            # as an attribute so the middleware knows which line it
            # guards. The middleware calls the *patchable*
            # ``_load_user_by_id`` so unit tests can swap in a fake
            # user store without hitting the real DB.
            from starlette.middleware.base import BaseHTTPMiddleware
            from starlette.responses import JSONResponse
            from ..core.auth import _cookie_name, decode_token, _load_user_by_id

            line_id_for_guard = line_id

            class _LineGuardMiddleware(BaseHTTPMiddleware):
                async def dispatch(self, request, call_next):
                    token = request.cookies.get(_cookie_name())
                    if not token:
                        auth = request.headers.get("authorization") or request.headers.get(
                            "Authorization"
                        )
                        if auth and auth.lower().startswith("bearer "):
                            token = auth.split(" ", 1)[1].strip()
                    if not token:
                        return JSONResponse(
                            {"detail": "not authenticated"},
                            status_code=401,
                        )
                    try:
                        payload = decode_token(token)
                    except Exception:
                        return JSONResponse(
                            {"detail": "invalid token"},
                            status_code=401,
                        )
                    try:
                        user = await _load_user_by_id(int(payload.sub))
                    except Exception:
                        return JSONResponse(
                            {"detail": "auth backend unavailable"},
                            status_code=503,
                        )
                    if user is None:
                        return JSONResponse(
                            {"detail": "user not found or inactive"},
                            status_code=401,
                        )
                    if (
                        user.has_admin()
                        or user.has_auditor()
                        or "viewer" in user.roles
                        or f"bp:{line_id_for_guard}" in user.roles
                        or line_id_for_guard in user.accessible_lines
                    ):
                        return await call_next(request)
                    return JSONResponse(
                        {
                            "detail": (
                                f"no access to business line '{line_id_for_guard}'; "
                                f"user has roles={sorted(user.roles)}"
                            )
                        },
                        status_code=403,
                    )

            obj.add_middleware(_LineGuardMiddleware)
            app.mount(prefix, obj)
            logger.info(
                "Mounted business line '%s' (FastAPI sub-app, line-guard) at %s",
                entry.line.id,
                prefix,
            )
        else:
            # APIRouter: include_router supports dependencies= kwarg.
            app.include_router(
                obj,
                prefix=prefix,
                dependencies=[Depends(business_line_router_guard(line_id))],
            )
            logger.info(
                "Mounted business line '%s' (APIRouter, line-guard) at %s",
                entry.line.id,
                prefix,
            )
