"""
apps/api/app/services/llm/ollama.py

Optional Ollama backend (local LLM).

Activates only when the env var OLLAMA_BASE_URL is set. The factory
`get_llm_backend()` in `llm/__init__.py` checks this var.

We use the standard Ollama HTTP API at /api/chat. No SDK required.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .base import LLMBackend


DEFAULT_BASE = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:7b"
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "30.0"))


class OllamaBackend:
    """Ollama local LLM backend."""

    name: str = "ollama"

    def __init__(self) -> None:
        self.base = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE).rstrip("/")
        self.model = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)

    async def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        url = f"{self.base}/api/chat"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "你是一名金融 BP 业务助手。"},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("message") or {}
            return msg.get("content") or "[Ollama 返回空 content]"
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            return f"[Ollama 调用失败: {exc}]"

    async def embed(self, text: str) -> list[float]:
        try:
            req = urllib.request.Request(
                f"{self.base}/api/embeddings",
                data=json.dumps({"model": self.model, "prompt": text}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            emb = data.get("embedding")
            if isinstance(emb, list):
                return [float(x) for x in emb]
        except Exception:
            pass
        return []
