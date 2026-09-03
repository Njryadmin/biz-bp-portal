"""
apps/api/app/services/llm/factory.py

LLM 厂商工厂——通过 ``ai_models`` 表实现运行时切换。

设计
----
在此模块出现之前，活跃后端由环境变量决定：
``DEEPSEEK_API_KEY`` → ``DeepSeekBackend``；
``OLLAMA_BASE_URL`` → ``OllamaBackend``；否则 ``MockBackend``。
在单一部署下能工作，但切换厂商需要重启 API（或重建容器）。

新的流程改为由数据库驱动。工厂现在：

  1. 在 ``ai_models`` 中查找同时满足 ``is_default=TRUE AND
     enabled=TRUE AND is_active=TRUE`` 的行。找到后，按该行的
     ``provider`` / ``model_name`` / ``base_url`` / ``api_key`` 字段
     实例化对应的厂商类。
  2. 如果没有配置默认行，则回退到旧版环境变量检查
     （``get_legacy_env_backend``），保证已有部署无需任何管理后台
     操作就能继续工作。
  3. 如果 (1) 和 (2) 都没有得到可用厂商，则返回 ``MockBackend``，
     以保证系统不会完全瘫痪。

``get_active_model()`` 是其余代码应当使用的唯读侧辅助函数。
它返回一个带类型的 dict（结构与行一致），如果未配置默认行则
返回 None。它不会抛出异常——调用方自行决定如何处理"无模型"。

厂商矩阵
--------
工厂目前支持六种 provider 字符串（与 ``ai_models.provider`` 的 SQL
CHECK 约束一致）：

  * ``mock``       → ``MockBackend``（无 I/O，确定性）
  * ``deepseek``   → ``DeepSeekBackend``（失败时回退到 mock）
  * ``ollama``     → ``OllamaBackend``
  * ``openai``     → ``OpenAICompatibleBackend``（base_url 可选，
                     默认 https://api.openai.com/v1/chat/completions）
  * ``anthropic``  → ``OpenAICompatibleBackend``（anthropic 风格的 base_url）
  * ``custom``     → ``OpenAICompatibleBackend``（任意 OpenAI 兼容端点；
                     运维人员提供自定义 base_url）

前三个复用现有的后端类（API 无需变动）。后三个共用一个新的
``OpenAICompatibleBackend`` 类，它沿用与 DeepSeek 相同的
chat-completions 协议但支持运行时配置（现有 DeepSeek 类在构造
时读取环境变量，因此不能直接复用于数据库配置）。

API 密钥处理
------------
``api_key`` 列存储的是加密后的密钥（Fernet，参见 ``core/secret.py``）
或 ``env:VAR_NAME`` 引用。``decrypt_secret`` 在调用时解析这两种形式。
如果表中字段和环境变量均为空，工厂会返回配置错误（运维人员
会在 ``/test`` 端点的响应中看到）。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text

from ...core.logging import get_logger
from ...core.secret import decrypt_secret
from ...db.session import get_session_factory
from .base import LLMBackend
from .deepseek import DeepSeekBackend
from .mock import MockBackend
from .ollama import OllamaBackend

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Active-model row dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AIModelRow:
    """Lightweight typed view of one ``ai_models`` row.

    The factory uses this to instantiate a backend. Only the fields
    the backend needs are stored; everything else (audit timestamps
    etc.) is fetched by the admin API, not by the factory.
    """

    id: int
    name: str
    provider: str
    model_name: str
    base_url: Optional[str]
    api_key: Optional[str]
    enabled: bool
    is_default: bool
    is_active: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model_name": self.model_name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "enabled": self.enabled,
            "is_default": self.is_default,
            "is_active": self.is_active,
        }


# ---------------------------------------------------------------------------
# OpenAI-compatible adapter (used for openai / anthropic / custom)
# ---------------------------------------------------------------------------


class OpenAICompatibleBackend:
    """Generic OpenAI Chat-Completions client.

    Used for the three new provider strings (``openai``, ``anthropic``,
    ``custom``). The wire format is the same as DeepSeek — JSON body
    with ``messages`` / ``model`` / ``temperature`` / ``max_tokens``,
    a Bearer token, and a ``choices[0].message.content`` reply.

    ``anthropic`` is mapped to this backend for the same reason every
    other vendor router does: Anthropic's Messages API is similar but
    uses ``system`` as a top-level field rather than as a messages
    entry. To keep the surface area small, we route the anthropic
    provider through the OpenAI-compatible path, which works against
    any OpenAI-compatible proxy in front of Anthropic (e.g. LiteLLM).
    Pure-Anthropic adapters can be added later as a separate class.
    """

    name: str = "openai-compatible"

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: Optional[str],
        base_url: str,
        temperature: float = 0.3,
        timeout: float = 30.0,
    ) -> None:
        self.provider_label = provider
        self.name = provider
        self.model = model
        self.api_key = api_key or ""
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        # Stats
        self.last_call_status: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_latency_ms: Optional[int] = None
        self.call_count: int = 0
        self.success_count: int = 0

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int = 1024,
        system_prompt: Optional[str] = None,
    ) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt or "你是一名金融 BP 业务助手。"},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            self.base_url, data=payload, headers=headers, method="POST"
        )
        self.call_count += 1
        started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                err_body = str(exc)
            self.last_call_status = "error"
            self.last_error = f"HTTP {exc.code}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(
                f"{self.provider_label} HTTP {exc.code}: {err_body[:200]}"
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            self.last_call_status = "timeout"
            self.last_error = f"timeout after {self.timeout}s"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise TimeoutError(
                f"{self.provider_label} request timed out after {self.timeout}s"
            ) from exc
        except urllib.error.URLError as exc:
            self.last_call_status = "error"
            self.last_error = f"URLError: {exc.reason}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(
                f"{self.provider_label} network error: {exc.reason}"
            ) from exc

        if status >= 400:
            self.last_call_status = "error"
            self.last_error = f"HTTP {status}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(f"{self.provider_label} HTTP {status}: {raw[:200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            self.last_call_status = "error"
            self.last_error = f"invalid JSON: {exc}"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(
                f"{self.provider_label} returned invalid JSON: {exc}"
            ) from exc
        choices = data.get("choices") or []
        if not choices:
            self.last_call_status = "error"
            self.last_error = "empty choices"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(f"{self.provider_label} returned empty 'choices'")
        content = ((choices[0] or {}).get("message") or {}).get("content")
        if not content:
            self.last_call_status = "error"
            self.last_error = "empty content"
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
            raise RuntimeError(f"{self.provider_label} returned empty 'content'")
        self.last_call_status = "ok"
        self.last_error = None
        self.last_latency_ms = int((time.monotonic() - started) * 1000)
        self.success_count += 1
        return str(content)

    async def embed(self, text: str) -> list[float]:
        # No-op — the OpenAI-compatible adapter only supports chat completions.
        return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


# Default base URLs for built-in providers. Operators can override via
# the ``base_url`` column in the ``ai_models`` table.
_DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "anthropic": "https://api.anthropic.com/v1/messages",
    "ollama": "http://localhost:11434/api/chat",
    "custom": "",
    "mock": "",
}


def get_active_model() -> Optional[AIModelRow]:
    """Return the row that the factory should use right now.

    Selection rules, in order:
      1. ``is_default=TRUE AND enabled=TRUE AND is_active=TRUE`` — the
         most recently updated default wins if more than one row
         somehow has ``is_default=TRUE`` (a defensive tie-breaker — the
         API enforces uniqueness on write).
      2. The first ``enabled=TRUE AND is_active=TRUE`` row sorted by
         ``id ASC``. This is the recovery path if every default was
         accidentally cleared.

    Returns None if the table is empty OR the DB is unreachable. The
    factory then falls back to the env-var path.

    Safe to call from any context (sync bootstrap, sync engine init,
    inside a running asyncio loop in a request handler). Implementation
    note: we always run the async query in a fresh worker thread so we
    never collide with a running loop and never block a handler that's
    already on the loop.
    """
    try:
        get_session_factory()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_model: session factory unavailable: %s", exc)
        return None
    try:
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-model-read") as ex:
            future = ex.submit(_run_async_in_worker, _fetch_active_row())
            return future.result(timeout=5)
    except FutTimeout:
        logger.warning("get_active_model: DB lookup timed out after 5s")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_active_model: DB lookup failed: %s", exc)
        return None


def _run_async_in_worker(coro):
    """Helper for ``get_active_model``: run ``coro`` in a fresh event
    loop on the current (worker) thread. We use a tiny wrapper so the
    ``ThreadPoolExecutor.submit`` line above stays readable.
    """
    import asyncio
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # ``asyncio.run`` couldn't get a loop in this thread (e.g. the
        # worker thread is shutting down). Try the explicit new-loop
        # form, then close it.
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()


# Kept for backwards-compat: some legacy tests import it. It is a
# no-op shim now that ``get_active_model`` is fully sync.
def _fetch_active_row_sync() -> Optional[AIModelRow]:
    return get_active_model()


async def _fetch_active_row() -> Optional[AIModelRow]:
    """Async helper for ``get_active_model`` — kept separate so tests
    can await it directly.
    """
    factory = get_session_factory()
    async with factory() as session:
        # Try the default first
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, provider, model_name, base_url, api_key,
                           enabled, is_default, is_active
                    FROM ai_models
                    WHERE is_default = TRUE
                      AND enabled = TRUE
                      AND is_active = TRUE
                    ORDER BY updated_at DESC, id ASC
                    LIMIT 1
                    """
                )
            )
        ).mappings().first()
        if row:
            return _row_from_mapping(row)
        # Fallback: any enabled+active row
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, name, provider, model_name, base_url, api_key,
                           enabled, is_default, is_active
                    FROM ai_models
                    WHERE enabled = TRUE AND is_active = TRUE
                    ORDER BY id ASC
                    LIMIT 1
                    """
                )
            )
        ).mappings().first()
        if row:
            return _row_from_mapping(row)
        return None


def _row_from_mapping(m: Any) -> AIModelRow:
    """Build an ``AIModelRow`` from a SQLAlchemy row-mapping."""
    return AIModelRow(
        id=int(m["id"]),
        name=str(m["name"]),
        provider=str(m["provider"]),
        model_name=str(m["model_name"]),
        base_url=m["base_url"],
        api_key=m["api_key"],
        enabled=bool(m["enabled"]),
        is_default=bool(m["is_default"]),
        is_active=bool(m["is_active"]),
    )


# ---------------------------------------------------------------------------
# Backend construction
# ---------------------------------------------------------------------------


def _build_backend_for_row(row: AIModelRow) -> LLMBackend:
    """Instantiate a backend that matches ``row.provider``.

    Raises ``ValueError`` for unknown providers (the API layer turns
    that into a 502 with the message in ``detail``). For deepseek
    and ollama, the existing backend classes are used; their
    constructors read env vars at __init__ time, so we temporarily
    mutate os.environ to point them at the row's model + base_url.
    """
    provider = row.provider.lower()
    base_url = row.base_url or _DEFAULT_BASE_URLS.get(provider, "")
    api_key = decrypt_secret(row.api_key)

    if provider == "mock":
        return MockBackend()

    if provider == "deepseek":
        if not api_key:
            raise ValueError("deepseek provider requires an api_key")
        # The existing DeepSeekBackend reads DEEPSEEK_API_KEY /
        # DEEPSEEK_BASE_URL / DEEPSEEK_MODEL at __init__ time. We push
        # the row's values into the environment for the call, then
        # construct the backend. The override is process-scoped (not
        # thread-scoped); a concurrent request may pick up a different
        # value. This is acceptable for a low-traffic admin endpoint
        # and matches the previous env-driven design.
        return _with_env(
            {
                "DEEPSEEK_API_KEY": api_key,
                "DEEPSEEK_BASE_URL": base_url or "https://api.deepseek.com/v1/chat/completions",
                "DEEPSEEK_MODEL": row.model_name,
            },
            lambda: DeepSeekBackend(),
        )

    if provider == "ollama":
        # Ollama is local + no auth; the env push is enough.
        return _with_env(
            {
                "OLLAMA_BASE_URL": base_url or "http://localhost:11434",
                "OLLAMA_MODEL": row.model_name,
            },
            lambda: OllamaBackend(),
        )

    if provider in {"openai", "anthropic", "custom"}:
        if provider != "anthropic" and not base_url:
            raise ValueError(
                f"{provider} provider requires a non-empty base_url"
            )
        if not base_url:
            base_url = _DEFAULT_BASE_URLS[provider]
        if provider == "openai" and not api_key:
            # OpenAI is auth-required; raise a clear error.
            raise ValueError("openai provider requires an api_key")
        return OpenAICompatibleBackend(
            provider=provider,
            model=row.model_name,
            api_key=api_key,
            base_url=base_url,
        )

    raise ValueError(f"unknown provider: {provider!r}")


def _with_env(values: dict[str, str], build: Any) -> Any:
    """Set ``values`` in ``os.environ`` for the duration of ``build()``.

    Restores the previous values (or deletes the keys if they didn't
    exist) on the way out, even if ``build`` raises. This is the
    same pattern the rest of the codebase uses (e.g. the existing
    ``get_llm_backend`` function).
    """
    saved: dict[str, Optional[str]] = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            os.environ[k] = v
        return build()
    finally:
        for k, prev in saved.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev


# ---------------------------------------------------------------------------
# Public factory entry points
# ---------------------------------------------------------------------------


def get_llm_backend() -> LLMBackend:
    """Pick the active LLM backend at runtime.

    Resolution order (mirrors the docstring at the top of this file):
      1. ``ai_models`` default row (DB-driven)
      2. Env-var fallback (``get_legacy_env_backend``)
      3. ``MockBackend`` — the "always works" tail
    """
    row = get_active_model()
    if row is not None:
        try:
            backend = _build_backend_for_row(row)
            logger.info(
                "get_llm_backend: using db-configured model id=%s name=%s provider=%s",
                row.id, row.name, row.provider,
            )
            return backend
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_llm_backend: db-configured model id=%s failed to build: %s; "
                "falling back to env/mock", row.id, exc,
            )
    legacy = get_legacy_env_backend()
    if legacy is not None:
        return legacy
    return MockBackend()


def get_legacy_env_backend() -> Optional[LLMBackend]:
    """The pre-registry env-var path. Returns None if no env is set.

    Kept as a separate function so the test suite can exercise both
    paths independently, and so the admin UI can render a "you're
    using the env-var fallback" banner by checking whether
    ``get_active_model()`` is None.
    """
    if os.environ.get("DEEPSEEK_API_KEY"):
        try:
            return DeepSeekBackend()
        except Exception:  # noqa: BLE001
            return None
    if os.environ.get("OLLAMA_BASE_URL"):
        try:
            return OllamaBackend()
        except Exception:  # noqa: BLE001
            return None
    return None


def configured_backend_name() -> str:
    """Return the *configured* backend name (no I/O).

    Mirrors the legacy function in ``__init__.py`` but goes through
    the DB first. Falls back to the env-var name for the case where
    no default row is configured.
    """
    row = get_active_model()
    if row is not None:
        return row.provider
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("OLLAMA_BASE_URL"):
        return "ollama"
    return "mock"


def get_primary_backend() -> LLMBackend:
    """Return the configured primary backend (no FallbackBackend wrapper)."""
    return get_llm_backend()


__all__ = [
    "AIModelRow",
    "OpenAICompatibleBackend",
    "configured_backend_name",
    "get_active_model",
    "get_legacy_env_backend",
    "get_llm_backend",
    "get_primary_backend",
]
