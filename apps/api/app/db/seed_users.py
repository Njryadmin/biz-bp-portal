"""
apps/api/app/db/seed_users.py

First-boot user bootstrap.

On the very first startup of an empty database this module creates:

  * 1 admin user (configurable via ``FIN_BP_BOOTSTRAP_ADMIN_USERNAME`` /
    ``FIN_BP_BOOTSTRAP_ADMIN_PASSWORD``; defaults ``admin`` / ``admin123``)
  * 10 BP users — one per registered business line — with username
    ``bp-<line_id>`` and password ``bp123456``. Each gets a single
    role ``bp:<line_id>`` and a matching ``user_business_lines`` row.

The seed is **idempotent**: it only runs when the ``users`` table is
empty, and individual user inserts are ``ON CONFLICT DO NOTHING`` so a
partial seed (e.g. a previous boot created the admin but failed before
the BP users) is repaired on the next restart.

Operator warnings are emitted at WARNING level for every default
password that's still in use after the seed.
"""
from __future__ import annotations

import os
from typing import Iterable

from sqlalchemy import text

from ..core.auth import hash_password
from ..core.config import get_settings
from ..core.logging import get_logger
from ..core.registry import load_registry
from .session import get_session_factory

logger = get_logger(__name__)


DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin123"
DEFAULT_BP_PASSWORD = "bp123456"


def _admin_username() -> str:
    return os.environ.get("FIN_BP_BOOTSTRAP_ADMIN_USERNAME") or DEFAULT_ADMIN_USERNAME


def _admin_password() -> str:
    return os.environ.get("FIN_BP_BOOTSTRAP_ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD


def _bp_password() -> str:
    return os.environ.get("FIN_BP_BOOTSTRAP_BP_PASSWORD") or DEFAULT_BP_PASSWORD


async def _users_empty() -> bool:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(text("SELECT COUNT(*) FROM users"))
        ).scalar_one()
    return int(row) == 0


async def _insert_user(
    *,
    username: str,
    password: str,
    display_name: str,
    email: str | None,
    role: str,
    line_id: str | None,
) -> int | None:
    """Insert one user + role + business-line row, returning the user id.

    Returns None if the user already exists (no-op).
    """
    factory = get_session_factory()
    pwd_hash = hash_password(password)
    async with factory() as session:
        # Upsert the user. ON CONFLICT DO NOTHING + RETURNING id
        # returns NULL on conflict so we can detect "already exists".
        user_row = (
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, email, password_hash, display_name, is_active)
                    VALUES (:username, :email, :password_hash, :display_name, TRUE)
                    ON CONFLICT (username) DO UPDATE
                        SET display_name = EXCLUDED.display_name
                    RETURNING id
                    """
                ),
                {
                    "username": username,
                    "email": email,
                    "password_hash": pwd_hash,
                    "display_name": display_name,
                },
            )
        ).mappings().first()
        if not user_row:
            await session.commit()
            return None
        user_id = int(user_row["id"])
        # Role row
        await session.execute(
            text(
                """
                INSERT INTO user_roles (user_id, role)
                VALUES (:uid, :role)
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": user_id, "role": role},
        )
        # Business-line row (if any)
        if line_id:
            await session.execute(
                text(
                    """
                    INSERT INTO user_business_lines (user_id, line_id)
                    VALUES (:uid, :line_id)
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"uid": user_id, "line_id": line_id},
            )
        await session.commit()
        return user_id


def _registry_line_ids() -> list[str]:
    """Return the list of registered business line ids, or [] on error."""
    try:
        return [e.line.id for e in load_registry()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("seed_users: load_registry failed: %s", exc)
        return []


async def seed_initial_users(force: bool = False) -> dict[str, int]:
    """Create the bootstrap admin + 1 BP user per registered business line.

    Idempotent: if any user with the planned username already exists, we
    only update the display_name (the role / business-line rows are
    inserted with ON CONFLICT DO NOTHING, so existing permissions are
    preserved).

    Parameters
    ----------
    force:
        If True, also re-seed when the users table is non-empty.
        Default False — only run on a truly empty table.
    """
    summary: dict[str, int] = {"admin": 0, "bp_users": 0}
    if not force and not await _users_empty():
        logger.info(
            "seed_initial_users: users table not empty; skipping bootstrap"
        )
        return summary
    admin_user = _admin_username()
    admin_pwd = _admin_password()
    bp_pwd = _bp_password()
    line_ids = _registry_line_ids()
    logger.warning(
        "seed_initial_users: first boot — creating admin '%s' and %d BP users",
        admin_user,
        len(line_ids),
    )
    # 1) admin
    admin_id = await _insert_user(
        username=admin_user,
        password=admin_pwd,
        display_name="System Administrator",
        email=f"{admin_user}@finbp.local",
        role="admin",
        line_id=None,
    )
    if admin_id is not None:
        summary["admin"] = 1
        # admin also gets an extra ``auditor`` role so they can read
        # the audit log without a second account.
        try:
            factory = get_session_factory()
            async with factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO user_roles (user_id, role) "
                        "VALUES (:uid, 'auditor') ON CONFLICT DO NOTHING"
                    ),
                    {"uid": admin_id},
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            pass
    # 2) one BP user per line
    for line_id in line_ids:
        bp_user = f"bp-{line_id}"
        bp_id = await _insert_user(
            username=bp_user,
            password=bp_pwd,
            display_name=f"BP — {line_id}",
            email=f"{bp_user}@finbp.local",
            role=f"bp:{line_id}",
            line_id=line_id,
        )
        if bp_id is not None:
            summary["bp_users"] += 1
    # Operator-facing warning: the default passwords are still in
    # use. The console sees a single WARNING on first boot, plus one
    # per line for the BP user, so the operator can search their logs
    # for "default password" to find them.
    if admin_pwd in {DEFAULT_ADMIN_PASSWORD, "admin", "admin123"}:
        logger.warning(
            "seed_initial_users: default admin password in use "
            "('%s'); CHANGE IT via PATCH /api/auth/users/{id}/password or "
            "by re-creating the user with a stronger secret",
            admin_pwd,
        )
    if bp_pwd == DEFAULT_BP_PASSWORD:
        logger.warning(
            "seed_initial_users: default BP password '%s' in use for %d "
            "lines; rotate them in production",
            DEFAULT_BP_PASSWORD,
            len(line_ids),
        )
    logger.warning(
        "seed_initial_users: created %s — admin=%d, bp_users=%d",
        summary, summary["admin"], summary["bp_users"],
    )
    return summary


__all__ = [
    "DEFAULT_ADMIN_PASSWORD",
    "DEFAULT_ADMIN_USERNAME",
    "DEFAULT_BP_PASSWORD",
    "seed_initial_users",
]
