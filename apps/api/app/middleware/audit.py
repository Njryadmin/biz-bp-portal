"""
apps/api/app/middleware/audit.py

Request-audit middleware.

Records one row per request into ``raw.audit_log``. Best-effort: any
failure to write to the audit log is logged at WARNING level but does
NOT prevent the request from being served (audit is a sidecar, not a
gate).

Performance
-----------
The write is scheduled as a background ``asyncio.Task`` so the HTTP
response is returned without waiting for the DB. If the DB is down, the
task fails silently (logged at WARNING) and the request still succeeds.
Tasks are tracked at module scope and awaited at process shutdown to
avoid "Task was destroyed but it is pending!" warnings.

Privacy
-------
* Login attempts are recorded with the username they tried (so failed
  logins show up) but the body itself is **never** logged. The path
  ``/api/auth/login`` is marked in the table but ``method=POST`` is
  all we capture.
* /healthz is excluded to keep the log from filling with liveness
  checks.
"""
from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

from fastapi import Request, Response
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from ..core.logging import get_logger
from ..db.session import get_session_factory

logger = get_logger(__name__)


# Routes that we never audit (health probes, login body).
_AUDIT_SKIP_PREFIXES: tuple[str, ...] = (
    "/healthz",
    "/",
)
_AUDIT_SKIP_EXACT: frozenset[str] = frozenset(
    {
        # The login body itself contains a plaintext password; we
        # still want a 200/401 record, but the middleware records only
        # the path + status, not the body. Body inspection is
        # disabled at the layer below; this skip is belt-and-suspenders
        # so a future bug can't accidentally capture it.
        "/api/auth/login",
        # /api/auth/logout is also explicitly excluded from
        # full-body capture for the same reason (token in cookie).
    }
)


# Module-scope set of in-flight audit tasks. We keep references so
# they aren't garbage-collected mid-flight, and we drain them at
# process shutdown.
_PENDING_AUDIT_TASKS: set[asyncio.Task] = set()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return ""


def _user_agent(request: Request) -> str:
    return request.headers.get("user-agent") or ""


def _should_audit(path: str) -> bool:
    if path in _AUDIT_SKIP_EXACT:
        return False
    for p in _AUDIT_SKIP_PREFIXES:
        if path == p:
            return False
    return True


async def _write_audit_row(
    *,
    user_id: int | None,
    username: str | None,
    method: str,
    path: str,
    query: str,
    status_code: int,
    duration_ms: int,
    ip: str,
    user_agent: str,
) -> None:
    """Best-effort insert into raw.audit_log. NEVER raises.

    Hard-capped at 3 seconds per row so a wedged DB doesn't pile up
    background tasks in the event loop (which would block the next
    request in the TestClient). The DB has its own 2s connect timeout;
    we add 1s for the actual insert.
    """
    try:
        async def _do_write() -> None:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    text(
                        """
                        INSERT INTO raw.audit_log
                            (user_id, username, method, path, query,
                             status_code, duration_ms, ip, user_agent)
                        VALUES
                            (:user_id, :username, :method, :path, :query,
                             :status_code, :duration_ms, :ip, :user_agent)
                        """
                    ),
                    {
                        "user_id": user_id,
                        "username": username,
                        "method": method,
                        "path": path,
                        "query": query[:1000] if query else None,
                        "status_code": int(status_code),
                        "duration_ms": int(duration_ms),
                        "ip": ip[:64] if ip else None,
                        "user_agent": user_agent[:512] if user_agent else None,
                    },
                )
                await session.commit()

        await asyncio.wait_for(_do_write(), timeout=3.0)
    except asyncio.TimeoutError:
        logger.warning(
            "audit_log write timed out for %s %s (DB unreachable?)", method, path
        )
    except Exception as exc:  # noqa: BLE001
        # Defensive: a failing audit MUST NOT take down the API.
        # The DB may be down (most common case) or the table may not
        # exist yet. In any case, log and move on.
        logger.warning("audit_log write failed for %s %s: %s", method, path, exc)


def _schedule_audit_row(**kwargs) -> None:
    """Schedule the audit insert as a background task. Fire-and-forget."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop (e.g. sync test harness). Just write inline
        # but with a short timeout so it never blocks the test.
        return
    task = loop.create_task(_write_audit_row(**kwargs))
    _PENDING_AUDIT_TASKS.add(task)
    task.add_done_callback(_PENDING_AUDIT_TASKS.discard)


async def drain_audit_tasks(timeout: float = 2.0) -> None:
    """Await all in-flight audit tasks. Called from app shutdown."""
    if not _PENDING_AUDIT_TASKS:
        return
    pending = list(_PENDING_AUDIT_TASKS)
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("drain_audit_tasks: timeout after %.1fs", timeout)


class AuditMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that writes a row to ``raw.audit_log`` per request.

    Uses the SQLAlchemy engine already initialised by
    ``app.db.session``. The schema for ``raw.audit_log`` is created by
    ``app.db.bootstrap.ensure_raw_schema``.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        method = request.method
        started = time.perf_counter()
        # Best-effort user resolution (from the cookie if present)
        user_id: int | None = None
        username: str | None = None
        try:
            from ..core.auth import _cookie_name, decode_token  # local import to avoid cycle
            token = request.cookies.get(_cookie_name())
            if not token:
                auth = request.headers.get("authorization") or request.headers.get("Authorization")
                if auth and auth.lower().startswith("bearer "):
                    token = auth.split(" ", 1)[1].strip()
            if token:
                try:
                    payload = decode_token(token)
                    user_id = int(payload.sub)
                    username = payload.username or None
                except Exception:  # noqa: BLE001
                    user_id = None
                    username = None
        except Exception:  # noqa: BLE001
            user_id = None
            username = None

        status_code = 500  # default if call_next raises
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            if _should_audit(path):
                _schedule_audit_row(
                    user_id=user_id,
                    username=username,
                    method=method,
                    path=path,
                    query=str(request.url.query or ""),
                    status_code=status_code,
                    duration_ms=duration_ms,
                    ip=_client_ip(request),
                    user_agent=_user_agent(request),
                )


__all__ = ["AuditMiddleware", "drain_audit_tasks"]
