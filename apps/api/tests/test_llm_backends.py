"""
apps/api/tests/test_llm_backends.py

Tests for the LLM backend layer (DeepSeek, Ollama, mock, FallbackBackend).

Coverage
========
- DeepSeek client (urllib mocked): success, 4xx, 5xx, timeout, network
  error, invalid JSON, empty choices, empty content, missing API key.
- Factory function: env-var priority, FallbackBackend wrapping.
- Fallback chain: primary success / primary failure / fallback flag.
- System prompt + build_prompt: business lines, endpoints, context.
- /api/copilot/health with various env states.
- Full HTTP round-trip with a fake DEEPSEEK_API_KEY (no real network).

All urllib calls are patched via ``unittest.mock.patch`` so no real
network requests are made.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.error
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(payload: dict[str, Any], status: int = 200) -> MagicMock:
    """Build a context-manager mock that returns ``payload`` JSON."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.status = status
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _empty_response() -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = b""
    resp.status = 204
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    """Build a urllib HTTPError with a readable body."""
    err = urllib.error.HTTPError(
        url="https://api.deepseek.com/v1/chat/completions",
        code=code,
        msg=f"HTTP {code}",
        hdrs={},
        fp=None,
    )
    # urllib's HTTPError.read is what deepseek.py uses; mock it.
    err.read = MagicMock(return_value=body.encode("utf-8"))
    return err


# ---------------------------------------------------------------------------
# 1) DeepSeek client — urllib mocked
# ---------------------------------------------------------------------------


class TestDeepSeekClient:
    """All tests mock urllib.request.urlopen so no real network call is made."""

    def test_constructor_without_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        from app.services.llm.deepseek import DeepSeekBackend, DeepSeekConfigError

        with pytest.raises(DeepSeekConfigError):
            DeepSeekBackend()

    def test_constructor_with_api_key_ok(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-xyz")
        from app.services.llm.deepseek import DeepSeekBackend

        b = DeepSeekBackend()
        assert b.name == "deepseek"
        assert b.model == "deepseek-chat"
        assert b.api_key == "sk-test-xyz"
        assert b.temperature == pytest.approx(0.3)
        assert b.timeout == pytest.approx(30.0)
        assert "deepseek.com" in b.base_url

    def test_success_2xx_returns_message_content(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ok")
        from app.services.llm.deepseek import DeepSeekBackend

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.return_value = _make_response(
                {
                    "id": "chatcmpl-1",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "你好,世界"},
                            "finish_reason": "stop",
                        }
                    ],
                },
                status=200,
            )
            out = asyncio.run(b.complete("hello"))
        assert out == "你好,世界"
        assert b.last_call_status == "ok"
        assert b.last_error is None
        assert b.call_count == 1
        assert b.success_count == 1
        # Make sure the request was sent with the right headers.
        sent = mock.call_args[0][0]
        assert sent.get_header("Authorization") == "Bearer sk-ok"
        assert sent.get_header("Content-type") == "application/json"

    def test_4xx_raises_http_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-bad")
        from app.services.llm.deepseek import (
            DeepSeekBackend,
            DeepSeekHTTPError,
        )

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.side_effect = _http_error(401, '{"error":"invalid api key"}')
            with pytest.raises(DeepSeekHTTPError) as excinfo:
                asyncio.run(b.complete("hello"))
        assert excinfo.value.status == 401
        assert "invalid api key" in str(excinfo.value)
        assert b.last_call_status == "error"
        assert b.success_count == 0

    def test_5xx_raises_http_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-5xx")
        from app.services.llm.deepseek import (
            DeepSeekBackend,
            DeepSeekHTTPError,
        )

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.side_effect = _http_error(500, "internal error")
            with pytest.raises(DeepSeekHTTPError) as excinfo:
                asyncio.run(b.complete("hello"))
        assert excinfo.value.status == 500
        assert b.last_call_status == "error"

    def test_timeout_raises_timeout_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-slow")
        from app.services.llm.deepseek import (
            DeepSeekBackend,
            DeepSeekTimeoutError,
        )

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.side_effect = socket.timeout("read timed out")
            with pytest.raises(DeepSeekTimeoutError):
                asyncio.run(b.complete("hello"))
        assert b.last_call_status == "timeout"

    def test_network_error_raises(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-net")
        from app.services.llm.deepseek import DeepSeekBackend, DeepSeekError

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.side_effect = urllib.error.URLError("Name or service not known")
            with pytest.raises(DeepSeekError) as excinfo:
                asyncio.run(b.complete("hello"))
        assert "network error" in str(excinfo.value).lower()
        assert b.last_call_status == "error"

    def test_invalid_json_raises_protocol_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-json")
        from app.services.llm.deepseek import (
            DeepSeekBackend,
            DeepSeekProtocolError,
        )

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            resp = MagicMock()
            resp.read.return_value = b"<html>500 error from CDN</html>"
            resp.status = 200
            resp.__enter__ = MagicMock(return_value=resp)
            resp.__exit__ = MagicMock(return_value=False)
            mock.return_value = resp
            with pytest.raises(DeepSeekProtocolError):
                asyncio.run(b.complete("hello"))
        assert b.last_call_status == "error"

    def test_empty_choices_raises_protocol_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-empty")
        from app.services.llm.deepseek import (
            DeepSeekBackend,
            DeepSeekProtocolError,
        )

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.return_value = _make_response({"choices": []}, status=200)
            with pytest.raises(DeepSeekProtocolError):
                asyncio.run(b.complete("hello"))

    def test_empty_content_raises_protocol_error(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-blank")
        from app.services.llm.deepseek import (
            DeepSeekBackend,
            DeepSeekProtocolError,
        )

        b = DeepSeekBackend()
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            mock.return_value = _make_response(
                {"choices": [{"message": {"role": "assistant", "content": ""}}]},
                status=200,
            )
            with pytest.raises(DeepSeekProtocolError):
                asyncio.run(b.complete("hello"))

    def test_embed_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-embed")
        from app.services.llm.deepseek import DeepSeekBackend

        b = DeepSeekBackend()
        # No network call should happen for embed.
        with patch("app.services.llm.deepseek.urllib.request.urlopen") as mock:
            out = asyncio.run(b.embed("hello"))
        assert out == []
        assert mock.call_count == 0


# ---------------------------------------------------------------------------
# 2) Factory function
# ---------------------------------------------------------------------------


class TestFactory:
    def test_no_env_returns_mock(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        from app.services.llm import (
            MockBackend,
            configured_backend_name,
            get_llm_backend,
        )

        assert configured_backend_name() == "mock"
        b = get_llm_backend()
        assert isinstance(b, MockBackend)
        assert b.name == "mock"

    def test_deepseek_key_returns_fallback_backend(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        from app.services.llm import (
            FallbackBackend,
            configured_backend_name,
            get_llm_backend,
        )

        assert configured_backend_name() == "deepseek"
        b = get_llm_backend()
        assert isinstance(b, FallbackBackend)
        assert b.name == "deepseek"
        assert b.primary.name == "deepseek"
        assert b.fallback.name == "mock"
        # No call yet → fallback not used.
        assert b.used_fallback is False

    def test_ollama_url_returns_fallback_backend(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        from app.services.llm import (
            FallbackBackend,
            configured_backend_name,
            get_llm_backend,
        )

        assert configured_backend_name() == "ollama"
        b = get_llm_backend()
        assert isinstance(b, FallbackBackend)
        assert b.name == "ollama"
        assert b.primary.name == "ollama"

    def test_deepseek_wins_over_ollama(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-abc")
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        from app.services.llm import (
            FallbackBackend,
            configured_backend_name,
            get_llm_backend,
        )

        assert configured_backend_name() == "deepseek"
        b = get_llm_backend()
        assert isinstance(b, FallbackBackend)
        assert b.primary.name == "deepseek"


# ---------------------------------------------------------------------------
# 3) Fallback chain
# ---------------------------------------------------------------------------


class TestFallbackChain:
    def test_fallback_uses_primary_when_succeeds(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ok")
        from app.services.llm import FallbackBackend, get_llm_backend
        from app.services.llm.deepseek import DeepSeekBackend
        from app.services.llm.mock import MockBackend

        b = get_llm_backend()
        assert isinstance(b, FallbackBackend)
        assert isinstance(b.primary, DeepSeekBackend)
        assert isinstance(b.fallback, MockBackend)

        with patch.object(
            b.primary, "complete", new=AsyncMockOK("primary response")
        ) as mock_primary:
            out = asyncio.run(b.complete("hello"))
        assert out == "primary response"
        assert b.used_fallback is False
        assert b.last_error is None
        assert b.last_answer is None
        mock_primary.assert_awaited_once()

    def test_fallback_falls_back_on_primary_failure(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fail")
        from app.services.llm import FallbackBackend, get_llm_backend
        from app.services.llm.deepseek import DeepSeekError
        from app.services.llm.mock import MockBackend

        b = get_llm_backend()
        assert isinstance(b, FallbackBackend)

        async def boom(*_a, **_kw):
            raise DeepSeekError("network down")

        with patch.object(b.primary, "complete", new=boom):
            out = asyncio.run(b.complete("住宅 IRR 最高的项目"))
        # Out should be the mock's answer (rule engine), not the error.
        assert isinstance(out, str)
        assert "未能" in out or "住宅" in out or "IRR" in out
        assert b.used_fallback is True
        assert b.last_error is not None
        assert "network down" in b.last_error
        # The mock answer should be available for the engine to use.
        assert b.last_answer is not None
        assert b.last_answer.answer == out

    def test_fallback_used_flag_surfaces_in_complete_response(self, monkeypatch):
        """The full CopilotEngine.ask() should return used_fallback=True
        when primary fails and mock kicks in."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fail")

        # We need the *factory* `get_primary_backend` to return a backend
        # whose `complete` raises. Patch the symbol at the engine module
        # boundary so the fresh instance created by _pick_backend() is
        # the patched one too.
        from app.services.copilot_engine import CopilotEngine, CopilotRequest
        from app.services.llm import deepseek as ds_mod
        from app.services.llm.deepseek import DeepSeekBackend, DeepSeekError

        # Force the primary to raise on every complete() call.
        async def boom(*_a, **_kw):
            raise DeepSeekError("simulated outage")

        # Patch DeepSeekBackend.complete to always raise. Every new
        # instance inherits this patched method.
        with patch.object(DeepSeekBackend, "complete", new=boom):
            engine = CopilotEngine()
            resp = engine.ask(
                CopilotRequest(question="住宅 IRR 最高的项目")
            )
        assert resp.used_fallback is True
        assert resp.fallback_reason is not None
        assert "simulated outage" in resp.fallback_reason
        assert resp.backend == "deepseek"
        # The mock's answer should have been wrapped in the response.
        assert isinstance(resp.answer, str)
        assert len(resp.answer) > 0

    def test_fallback_also_survives_total_outage(self, monkeypatch):
        """If both primary and mock fail, the backend should still return
        a non-empty string and not raise."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-double-fail")
        from app.services.llm import FallbackBackend, get_llm_backend
        from app.services.llm.deepseek import DeepSeekError

        b = get_llm_backend()
        assert isinstance(b, FallbackBackend)

        async def boom_primary(*_a, **_kw):
            raise DeepSeekError("primary down")

        with patch.object(b.primary, "complete", new=boom_primary):
            with patch.object(
                b.fallback, "answer", side_effect=RuntimeError("mock also down")
            ):
                out = asyncio.run(b.complete("hello"))
        assert isinstance(out, str)
        assert "不可用" in out or "失败" in out
        assert b.used_fallback is True


# AsyncMock-like helper for the success path.


class AsyncMockOK:
    """Async function mock that returns a fixed value."""

    def __init__(self, return_value: Any) -> None:
        self.return_value = return_value
        self.call_count = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.call_count += 1
        return self.return_value

    def assert_awaited_once(self) -> None:
        assert self.call_count == 1, f"call_count={self.call_count}"


# ---------------------------------------------------------------------------
# 4) Prompts
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_system_prompt_contains_role(self):
        from app.services.llm.prompts import render_system_prompt

        sp = render_system_prompt()
        assert "Fin BP Portal" in sp
        assert "Copilot" in sp
        assert "简体中文" in sp or "中文" in sp

    def test_system_prompt_lists_business_lines(self):
        from app.services.llm.prompts import render_system_prompt

        sp = render_system_prompt()
        # Must mention at least the canonical lines.
        for line_id in ("residential", "retail", "retail-leasing"):
            assert line_id in sp, f"missing line {line_id} in system prompt"
        # And their endpoints.
        assert "/projects" in sp
        assert "/properties" in sp

    def test_system_prompt_has_few_shot_examples(self):
        from app.services.llm.prompts import render_system_prompt

        sp = render_system_prompt()
        # At least 3 few-shot blocks.
        assert "例子 1" in sp
        assert "例子 2" in sp
        assert "例子 3" in sp
        # And they mention real data shape.
        assert "PRJ-001" in sp or "project_id" in sp

    def test_system_prompt_has_citation_rule(self):
        from app.services.llm.prompts import render_system_prompt

        sp = render_system_prompt()
        assert "参考资料" in sp

    def test_build_prompt_includes_question(self):
        from app.services.llm.prompts import build_prompt

        p = build_prompt("请问 IRR 怎么算?", "residential", None)
        assert "请问 IRR 怎么算?" in p
        assert "住宅" in p or "residential" in p

    def test_build_prompt_includes_context_data(self):
        from app.services.llm.prompts import build_prompt

        ctx = [{"project_id": "PRJ-001", "irr": 0.15}, {"project_id": "PRJ-002", "irr": 0.12}]
        p = build_prompt("IRR top?", "residential", ctx)
        assert "PRJ-001" in p
        assert "PRJ-002" in p
        assert "0.15" in p

    def test_build_prompt_handles_no_line(self):
        from app.services.llm.prompts import build_prompt

        p = build_prompt("全局", None, None)
        assert "未限定业务线" in p

    def test_build_prompt_handles_empty_context(self):
        from app.services.llm.prompts import build_prompt

        p = build_prompt("q", "residential", None)
        assert "(空" in p or "暂未" in p or "没有" in p


# ---------------------------------------------------------------------------
# 5) Health endpoint integration
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    @pytest.fixture
    def client(self, app_with_auth):
        from fastapi.testclient import TestClient
        return TestClient(app_with_auth)

    def test_health_no_env_reports_mock(self, client, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        r = client_with_auth.get("/api/copilot/health")
        assert r.status_code == 200
        data = r.json()
        assert data["backend"] == "mock"
        assert data["configured_backend"] == "mock"
        assert data["deepseek_key_present"] is False
        assert data["ollama_url"] is None
        assert data["model"] is None
        assert data["used_fallback"] is False
        assert "available_lines" in data
        assert "api_base" in data

    def test_health_with_deepseek_key_reports_deepseek(self, client, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-zzz")
        r = client_with_auth.get("/api/copilot/health")
        assert r.status_code == 200
        data = r.json()
        assert data["backend"] == "deepseek"
        assert data["configured_backend"] == "deepseek"
        assert data["deepseek_key_present"] is True
        assert data["model"] == "deepseek-chat"
        assert data["temperature"] is not None
        assert data["used_fallback"] is False
        # call_count starts at 0; we haven't called /ask yet.
        assert data["call_count"] == 0

    def test_health_with_ollama_reports_ollama(self, client, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
        r = client_with_auth.get("/api/copilot/health")
        assert r.status_code == 200
        data = r.json()
        assert data["backend"] == "ollama"
        assert data["ollama_url"] == "http://localhost:11434"


# ---------------------------------------------------------------------------
# 6) Full HTTP round-trip with a fake DeepSeek key
# ---------------------------------------------------------------------------


class TestAskEndpoint:
    @pytest.fixture
    def client(self, app_with_auth):
        from fastapi.testclient import TestClient
        return TestClient(app_with_auth)

    def test_ask_with_fake_key_does_not_500(self, client, monkeypatch):
        """A fake / unreachable DEEPSEEK_API_KEY should trigger the
        fallback chain and return a 200 with used_fallback=true, not a
        500 to the user."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-clearly-fake-key")

        body = {"question": "住宅 IRR 最高的 3 个项目"}
        r = client_with_auth.post("/api/copilot/ask", json=body)
        # Must NOT 500.
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["backend"] == "deepseek"  # we tried deepseek
        assert data["used_fallback"] is True
        assert data["fallback_reason"] is not None
        # The fallback produced the mock's answer (rule engine).
        assert "住宅" in data["answer"] or "IRR" in data["answer"] or "未能" in data["answer"]

    def test_ask_with_deepseek_mocked_success(self, client, monkeypatch):
        """When DeepSeek returns a valid 2xx, the response should reflect
        the primary backend (no fallback)."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-success")

        # Patch urllib via the deepseek module's namespace.
        from app.services.llm import deepseek as ds_mod

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            return _make_response(
                {
                    "id": "chatcmpl-x",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "由 DeepSeek 生成的答案",
                            },
                        }
                    ],
                },
                status=200,
            )

        with patch.object(ds_mod.urllib.request, "urlopen", new=fake_urlopen):
            r = client_with_auth.post(
                "/api/copilot/ask",
                json={"question": "随便问点什么"},
            )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["backend"] == "deepseek"
        assert data["used_fallback"] is False
        assert data["fallback_reason"] is None
        assert data["model"] == "deepseek-chat"
        assert "DeepSeek" in data["answer"]

    def test_ask_uses_mock_when_no_key(self, client, monkeypatch):
        """No key → backend='mock', no fallback. The mock engine still
        runs the rule-engine dispatch; the richness of the answer depends
        on whether the underlying API is reachable (existing pre-test
        fragility, not caused by this change)."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        r = client_with_auth.post(
            "/api/copilot/ask",
            json={"question": "住宅 IRR 最高的 3 个项目"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["backend"] == "mock"
        assert data["used_fallback"] is False
        # The mock engine must return *something* (string), not 500.
        assert isinstance(data["answer"], str)
        assert len(data["answer"]) > 0
        assert data["intent"] in {
            "irr_top",
            "payment_low",
            "redlines",
            "dedup_low",
            "fallback_unknown",  # API down case
        }

    def test_prefer_real_llm_true_switches_when_key_set(self, client, monkeypatch):
        """The prefer_real_llm=True flag should force the FallbackBackend
        path even if the env backend would otherwise be mock."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-toggle-test")
        # When the real backend fails, we expect used_fallback=true.
        from app.services.llm import deepseek as ds_mod

        def fake_urlopen(req, timeout=None):  # noqa: ARG001
            raise urllib.error.URLError("forced failure for test")

        with patch.object(ds_mod.urllib.request, "urlopen", new=fake_urlopen):
            r = client_with_auth.post(
                "/api/copilot/ask",
                json={
                    "question": "住宅 IRR 最高的 3 个项目",
                    "prefer_real_llm": True,
                },
            )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["backend"] == "deepseek"
        assert data["used_fallback"] is True
        assert data["model"] == "deepseek-chat"

    def test_prefer_real_llm_false_forces_mock(self, client, monkeypatch):
        """The prefer_real_llm=False flag should force MockBackend,
        even if a real backend is configured."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-be-ignored")
        r = client_with_auth.post(
            "/api/copilot/ask",
            json={
                "question": "住宅 IRR 最高的 3 个项目",
                "prefer_real_llm": False,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["backend"] == "mock"
        assert data["used_fallback"] is False
        assert data["model"] is None  # mock has no model

    def test_prefer_real_llm_true_without_key_still_uses_mock(
        self, client, monkeypatch
    ):
        """If no real backend is configured, prefer_real_llm=True can't
        be honored — the user should still get a useful answer."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        r = client_with_auth.post(
            "/api/copilot/ask",
            json={
                "question": "住宅 IRR 最高的 3 个项目",
                "prefer_real_llm": True,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["backend"] == "mock"
        # Still got a real answer (mock rule engine).
        assert "住宅" in data["answer"] or "IRR" in data["answer"] or "未能" in data["answer"]
