"""
apps/api/app/db/tenant.py
=========================

Multi-tenant context helper (M1).

This module provides ``tenant_session()`` — an async context manager that
yields a SQLAlchemy ``AsyncSession`` with the Postgres GUCs
``app.tenant_id`` and ``app.bypass_rls`` bound to the current transaction
via ``SET LOCAL``.

M1 only ships the helper + a ``DEFAULT_TENANT_ID`` constant. The actual
middleware that sets ``X-Tenant-ID`` from the HTTP header and re-issues
every router call through ``tenant_session()`` is M2 (see
``rbac-diff.md`` §14).

Why ``SET LOCAL`` and not ``SET``
---------------------------------
``SET LOCAL`` confines the GUC change to the current transaction. The
SQLAlchemy ``AsyncSession`` created by ``async with factory() as session``
is implicitly transactional, so the GUC is automatically released when
the ``async with`` block exits (and the session is rolled back / committed
+ closed). This is important: a connection returned to the engine's pool
must NOT carry tenant context forward, or the next request from a
different tenant would inherit the previous tenant's filter — a classic
multi-tenant data-leak bug.

The cost: every block must be transactional. The SQLAlchemy default
``expire_on_commit=False`` and ``autocommit=False`` (the async engine's
default) match that requirement — DO NOT enable autocommit on the
session factory used here.

Why two GUCs (``app.tenant_id`` and ``app.bypass_rls``)
--------------------------------------------------------
M1's RLS policy only checks ``tenant_id`` (see
``infra/migrations/003_multi_tenant_setup.sql``). The ``bypass_rls`` GUC
is plumbed through now so M2's policy upgrade can OR it in without
changing every call site::

    USING (
        current_setting('app.bypass_rls', true) = 'on'
        OR tenant_id = current_setting('app.tenant_id', true)::uuid
    )

This means the M1 helper has a stable contract: callers always pass
``bypass_rls=True`` for super-admin paths even though the flag is a
no-op today. M2 will pick it up without further refactors.

Usage
-----
::

    from app.db.tenant import tenant_session, DEFAULT_TENANT_ID

    async with tenant_session(DEFAULT_TENANT_ID) as session:
        rows = (await session.execute(
            text("SELECT id, username FROM users WHERE id = :id"),
            {"id": 42},
        )).all()
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from sqlalchemy import text


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: The "all-zeros" UUID is reserved for the bootstrap / legacy tenant.
#: All rows from the v0.1.0 + PR#1 era are backfilled to this tenant in
#: ``003_multi_tenant_setup.sql``. M2+ will add real tenant slugs
#: ('acme-realty', 'jll', 'cbre', ...) and the M2 middleware will switch
#: the active tenant from the ``X-Tenant-ID`` header.
DEFAULT_TENANT_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")

#: GUC name for the active tenant id. Must match the value used in the
#: RLS policy in ``003_multi_tenant_setup.sql``.
GUC_TENANT_ID: str = "app.tenant_id"

#: GUC name for the super-admin bypass. Currently a no-op at the SQL
#: level (M1 policy doesn't read it), but plumbed through the helper
#: so M2 can enable bypass without changing every call site.
GUC_BYPASS_RLS: str = "app.bypass_rls"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@asynccontextmanager
async def tenant_session(
    tenant_id: UUID,
    bypass_rls: bool = False,
) -> AsyncIterator:
    """Yield an :class:`AsyncSession` bound to ``tenant_id``.

    Within the ``async with`` block, every query issued through the
    session sees Postgres GUCs ``app.tenant_id`` = ``str(tenant_id)`` and
    ``app.bypass_rls`` = ``'on' | 'off'``. RLS policies that reference
    those GUCs (see ``003_multi_tenant_setup.sql``) will then filter /
    allow rows accordingly.

    Parameters
    ----------
    tenant_id:
        The tenant the queries should be scoped to. Pass
        :data:`DEFAULT_TENANT_ID` for the legacy / bootstrap tenant.
    bypass_rls:
        When ``True``, sets ``app.bypass_rls = 'on'`` so a future RLS
        policy upgrade can let the connection see across tenants.
        Today (M1) the SQL policy does not consult this GUC, so
        ``bypass_rls=True`` still goes through the normal tenant
        filter — but the call site is forward-compatible with M2.

    Yields
    ------
    :class:`sqlalchemy.ext.asyncio.AsyncSession`
        A session whose connection has the GUCs set. Use it exactly like
        any other ``AsyncSession`` (execute, scalars, etc.). The session
        is committed/rolled back when the ``async with`` block exits,
        and the GUC changes are released with it (SET LOCAL semantics).

    Notes
    -----
    The session is obtained from the global
    :func:`app.db.session.get_session_factory` factory, so the
    ``AsyncEngine`` is shared with the rest of the app. This is
    intentional: the pool's connection-handout logic does the
    right thing once ``SET LOCAL`` is in effect for the duration of
    the transaction.

    The ``async with factory() as session`` block already opens a
    transaction (SQLAlchemy async default is ``autocommit=False``), so
    ``SET LOCAL`` is safe to issue from within the block.
    """
    # Local import to avoid a circular dependency at module load time
    # (session.py imports from app.core, app.db.tenant is a leaf).
    from .session import get_session_factory

    factory = get_session_factory()
    bypass_value = "on" if bypass_rls else "off"

    async with factory() as session:
        # SET LOCAL is transaction-scoped — released automatically on
        # commit / rollback / connection release. We issue two
        # separate ``text()`` calls rather than one multi-statement
        # string because asyncpg's prepared-statement protocol
        # (which SQLAlchemy uses by default) refuses multi-statement
        # SQL with "cannot insert multiple commands into a prepared
        # statement". We don't use bind parameters either: asyncpg's
        # protocol also rejects placeholders inside ``SET`` (Postgres
        # parses SET values as ``value`` not as an expression list).
        # The values are stringified UUIDs / literal ``on|off`` so
        # direct interpolation is safe.
        await session.execute(
            text(f"SET LOCAL {GUC_TENANT_ID} = '{tenant_id}'")
        )
        await session.execute(
            text(f"SET LOCAL {GUC_BYPASS_RLS} = '{bypass_value}'")
        )
        yield session


__all__ = [
    "DEFAULT_TENANT_ID",
    "GUC_BYPASS_RLS",
    "GUC_TENANT_ID",
    "tenant_session",
]
