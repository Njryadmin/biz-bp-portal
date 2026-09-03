"""
apps/api/app/core/secret.py

Symmetric encryption helper for at-rest secrets (e.g. LLM api_key values
stored in the ``ai_models`` table).

We use Fernet from the ``cryptography`` package (already a transitive
dep via passlib / JWT in this codebase). Fernet = AES-128-CBC + HMAC-SHA256
+ a version byte, keyed by a 32-byte URL-safe base64 secret. It guarantees
both confidentiality and integrity, and the resulting ciphertext is
URL-safe base64 so it round-trips through TEXT columns cleanly.

Key handling
------------
* Production: set ``BIZ_BP_AI_SECRET_KEY`` to a 32-byte URL-safe
  base64-encoded secret. ``cryptography.fernet.Fernet.generate_key()``
  produces one.
* Dev (default): when no key is set the helper degrades to a *plaintext
  pass-through* (``encrypt(s) -> s``, ``decrypt(s) -> s``) so a fresh
  checkout still works. The runtime emits a single WARNING the first
  time it's used, so operators see the dev-mode banner in their logs.
* Env references: a stored api_key value that starts with ``env:`` is
  treated as a reference to an environment variable. ``decrypt_api_key``
  resolves the reference at read time so the secret is never written
  to the database.

This module is intentionally tiny — no rotation, no versioning, no
audit log. Add those in a follow-up if the operator policy requires.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_logger = logging.getLogger("fin_bp.secret")
_dev_mode_warned: bool = False


def _resolve_secret_key() -> bytes | None:
    """Return the Fernet key, or None for dev-mode plaintext fallback.

    Order of resolution:
      1. ``BIZ_BP_AI_SECRET_KEY`` env var (URL-safe base64)
      2. ``ai_secret_key`` field on the Settings object
    Both must decode as a 32-byte Fernet key; bad keys raise
    ``ValueError`` (caller-side error — a misconfigured prod secret
    should be loud, not silently fall back to plaintext).
    """
    raw = os.environ.get("BIZ_BP_AI_SECRET_KEY") or get_settings().ai_secret_key
    if not raw:
        return None
    try:
        key = raw.encode("ascii")
        # Fernet() will raise ValueError if the key is not 32 URL-safe
        # base64 bytes. We don't construct Fernet here because we want
        # the explicit error message; the caller will do that.
        Fernet(key)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "BIZ_BP_AI_SECRET_KEY is set but is not a valid Fernet key "
            "(must be 32 URL-safe base64 bytes). Regenerate with "
            "`python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\"` and update the env var. "
            f"Underlying error: {exc}"
        ) from exc
    return key


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet | None:
    """Build (or skip) the Fernet instance. Cached so we only do the
    dev-mode warning once per process.
    """
    global _dev_mode_warned
    key = _resolve_secret_key()
    if key is None:
        if not _dev_mode_warned:
            _logger.warning(
                "BIZ_BP_AI_SECRET_KEY not set — ai_models.api_key will be "
                "stored as plaintext. Set BIZ_BP_AI_SECRET_KEY in production "
                "(generate with `python -c \"from cryptography.fernet import "
                "Fernet; print(Fernet.generate_key().decode())\"`)."
            )
            _dev_mode_warned = True
        return None
    return Fernet(key)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encrypt_secret(plaintext: str) -> str:
    """Encrypt ``plaintext`` with Fernet; return the URL-safe base64 token.

    In dev mode (no key configured) the plaintext is returned unchanged,
    prefixed with ``plain:`` so the read path can tell that no
    encryption was applied (vs a real Fernet token).
    """
    if not plaintext:
        return ""
    f = _get_fernet()
    if f is None:
        return f"plain:{plaintext}"
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(stored: str | None) -> str | None:
    """Decrypt a stored secret back to plaintext.

    Handles all three storage formats:
      * ``env:VAR_NAME`` → read the environment variable at call time.
      * ``plain:xxx``    → strip the marker (dev mode).
      * ``xxx``          → assume Fernet ciphertext; return the decrypted
        plaintext, or ``None`` if the token is invalid (corrupted /
        rotated key).
    Returns ``None`` for the empty string and for unreadable ciphertext.
    """
    if not stored:
        return None
    if stored.startswith("env:"):
        var = stored[4:].strip()
        if not var:
            return None
        return os.environ.get(var)
    if stored.startswith("plain:"):
        return stored[len("plain:"):]
    f = _get_fernet()
    if f is None:
        # No key → cannot decrypt a real ciphertext. Best-effort: return
        # the raw value (the operator will see the gibberish in logs and
        # notice something is wrong).
        return stored
    try:
        return f.decrypt(stored.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        _logger.warning(
            "decrypt_secret: stored value did not decrypt (key rotated? "
            "corrupted row?) — returning None"
        )
        return None


def is_env_reference(value: str | None) -> bool:
    """True if ``value`` is an ``env:VAR`` reference rather than a literal."""
    return bool(value) and value.startswith("env:")


__all__ = [
    "decrypt_secret",
    "encrypt_secret",
    "is_env_reference",
]
