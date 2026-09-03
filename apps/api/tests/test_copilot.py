"""
apps/api/tests/test_copilot.py

Tests for the AI Copilot module (mock backend + HTTP layer).

Coverage:
  1.  /api/copilot/health returns backend + lines
  2.  /api/copilot/suggestions returns per-line + common lists
  3.  Each of the 8+ mock intent templates is reachable via /ask:
        residential: irr_top, payment_low, redlines, dedup_low
        retail:      noi_top, renovation, collection
        leasing:     vacancy, benchmark
        cross:       cross_overview, line_indicators, sensitivity
  4.  line_id / context_lines constraints
  5.  Empty / oversized question → 400
  6.  Unknown / gibberish question → fallback intent, friendly answer
  7.  Citations are real (have source / title / snippet / url)
  8.  Universality: a 5th business line + API endpoint is reachable
      by the mock engine without code changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """Spin up the FastAPI app and return a TestClient."""
    return TestClient(create_app())


@pytest.fixture
def line_ids():
    """Business lines registered in the test repo (sorted)."""
    return ["my-line", "residential", "retail", "retail-leasing"]


# ─────────────────────────────────────────────────────────────────────────
# 1) Health
# ─────────────────────────────────────────────────────────────────────────


def test_health_endpoint(client):
    r = client.get("/api/copilot/health")
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] in {"mock", "deepseek", "ollama"}
    assert isinstance(data["available_lines"], list)
    assert {"residential", "retail", "retail-leasing"}.issubset(set(data["available_lines"]))
    assert data["api_base"]


def test_health_backend_defaults_to_mock(client, monkeypatch):
    """With no DEEPSEEK_API_KEY / OLLAMA_BASE_URL, /health should report mock."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    r = client.get("/api/copilot/health")
    assert r.json()["backend"] == "mock"


# ─────────────────────────────────────────────────────────────────────────
# 2) Suggestions
# ─────────────────────────────────────────────────────────────────────────


def test_suggestions_endpoint(client):
    r = client.get("/api/copilot/suggestions")
    assert r.status_code == 200
    data = r.json()
    assert "by_line" in data
    assert "common" in data
    # At least 6 suggested questions (3 common + ≥3 line-keyed).
    common = data["common"]
    flat = list(common) + [q for v in data["by_line"].values() for q in v]
    assert len(flat) >= 6
    # Each line with a known id has at least 2 suggestions.
    for lid in ("residential", "retail", "retail-leasing"):
        assert lid in data["by_line"]
        assert len(data["by_line"][lid]) >= 2


# ─────────────────────────────────────────────────────────────────────────
# 3) Mock intent templates — one test per intent
# ─────────────────────────────────────────────────────────────────────────


def _ask(client, question: str, line_id: str | None = None):
    body: dict = {"question": question}
    if line_id is not None:
        body["line_id"] = line_id
    return client.post("/api/copilot/ask", json=body)


def test_intent_residential_irr_top(client):
    r = _ask(client, "住宅 IRR 最高的 3 个项目")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "irr_top"
    assert data["backend"] == "mock"
    assert data["confidence"] > 0.5
    assert len(data["citations"]) >= 1
    # Citations point to the residential line.
    for c in data["citations"]:
        assert "residential" in c["source"]
    # Each citation has a url back into the dashboard.
    for c in data["citations"]:
        assert c["url"] and c["url"].startswith("/residential")


def test_intent_residential_payment_low(client):
    r = _ask(client, "本月回款下降的项目有哪些?")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "payment_low"
    assert data["confidence"] > 0.5
    assert len(data["citations"]) >= 1


def test_intent_residential_redlines(client):
    r = _ask(client, "三道红线触发情况")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "redlines"
    assert data["confidence"] > 0.5
    # Answer text mentions at least one of the three thresholds.
    assert any(
        kw in data["answer"]
        for kw in ("三道红线", "资产负债率", "净负债率", "现金短债比")
    )


def test_intent_residential_dedup_low(client):
    r = _ask(client, "去化速度最低的项目")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "dedup_low"
    assert data["confidence"] > 0.5
    assert "去化" in data["answer"]


def test_intent_retail_noi_top(client):
    r = _ask(client, "零售 NOI top 3")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "noi_top"
    assert data["confidence"] > 0.5
    assert len(data["citations"]) >= 1
    # Citations point to retail properties.
    for c in data["citations"]:
        assert "retail" in c["source"]


def test_intent_retail_renovation(client):
    r = _ask(client, "调改 NPV 为正的项目")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "renovation"
    assert data["confidence"] > 0.5
    assert "NPV" in data["answer"] or "调改" in data["answer"]


def test_intent_retail_collection(client):
    r = _ask(client, "收缴率低于 95% 的物业")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "collection"
    assert data["confidence"] > 0.5
    # Threshold "95" should appear in the answer.
    assert "95" in data["answer"]


def test_intent_leasing_vacancy(client):
    r = _ask(client, "空置期最长的业主")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "vacancy"
    assert data["confidence"] > 0.5
    assert "空置" in data["answer"]


def test_intent_leasing_benchmark(client):
    r = _ask(client, "竞品基准差最大的商铺")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "benchmark"
    assert data["confidence"] > 0.5
    assert "基准" in data["answer"] or "对标" in data["answer"]


def test_intent_cross_overview(client):
    r = _ask(client, "三业务线 KPI 概览对比")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "cross_overview"
    # Cross-overview should mention at least 2 of the 3 real lines.
    mentions = sum(
        1 for kw in ("住宅", "零售", "租赁")
        if kw in data["answer"]
    )
    assert mentions >= 2


def test_intent_sensitivity(client):
    r = _ask(client, "做一份敏感性分析")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "sensitivity"
    assert data["confidence"] > 0.5
    # Sensitivity intent should hint at /sensitivity page.
    assert "/sensitivity" in data["answer"] or "敏感" in data["answer"]


def test_intent_line_indicators(client):
    r = _ask(client, "住宅的指标")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "line_indicators"
    assert "residential" in data["answer"] or "住宅" in data["answer"]
    # At least 5 indicators listed.
    bullets = [ln for ln in data["answer"].splitlines() if ln.strip().startswith("-")]
    assert len(bullets) >= 5


# ─────────────────────────────────────────────────────────────────────────
# 4) line_id / context_lines constraints
# ─────────────────────────────────────────────────────────────────────────


def test_explicit_line_id_in_body_routes_correctly(client):
    """Sending line_id explicitly should constrain the answer scope."""
    r = _ask(client, "最高的 3 个项目", line_id="retail")
    assert r.status_code == 200
    data = r.json()
    # Should hit the retail NOI intent (retail line's headline KPI is NOI).
    assert data["intent"] in {"noi_top", "fallback_unknown"}


def test_explicit_line_id_in_question_routes_correctly(client):
    """The same question without line_id should still find the right intent
    via the in-question '零售' keyword."""
    r = _ask(client, "零售 NOI top 3")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "noi_top"


# ─────────────────────────────────────────────────────────────────────────
# 5) Bad input
# ─────────────────────────────────────────────────────────────────────────


def test_empty_question_returns_400(client):
    r = _ask(client, "")
    assert r.status_code == 400
    body = r.json()
    # Detail is a string, not the pydantic validation list.
    assert "detail" in body
    assert isinstance(body["detail"], str)


def test_whitespace_only_question_returns_400(client):
    r = _ask(client, "   \n\t  ")
    assert r.status_code == 400
    assert "detail" in r.json()


def test_oversized_question_returns_400(client):
    big = "x" * 2500
    r = _ask(client, big)
    # Pydantic will reject via max_length=2000 → 422.
    # OR if Pydantic's outer check is bypassed (it isn't here since we have max_length),
    # the engine would return 400. We accept either 400 or 422 for safety.
    assert r.status_code in (400, 422)


# ─────────────────────────────────────────────────────────────────────────
# 6) Unknown / gibberish question → friendly fallback
# ─────────────────────────────────────────────────────────────────────────


def test_gibberish_question_returns_friendly_fallback(client):
    r = _ask(client, "我爱你")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "fallback_unknown"
    assert "抱歉" in data["answer"] or "没能" in data["answer"]
    # Fallback answer always has at least one citation (the suggestions list).
    assert len(data["citations"]) >= 1


def test_empty_meaningful_words_falls_back(client):
    r = _ask(client, "abracadabra")
    assert r.status_code == 200
    data = r.json()
    assert data["intent"] == "fallback_unknown"


# ─────────────────────────────────────────────────────────────────────────
# 7) Citations are well-formed and point at real sources
# ─────────────────────────────────────────────────────────────────────────


def test_citations_have_required_fields(client):
    r = _ask(client, "住宅 IRR 最高的 3 个项目")
    assert r.status_code == 200
    data = r.json()
    assert len(data["citations"]) >= 1
    for c in data["citations"]:
        # All required fields present
        assert isinstance(c.get("source"), str) and c["source"]
        assert isinstance(c.get("title"), str) and c["title"]
        assert isinstance(c.get("snippet"), str) and c["snippet"]
        # url is optional but if present must be a string
        if c.get("url") is not None:
            assert isinstance(c["url"], str)
        # Source must mention one of the known lines.
        assert any(
            ln in c["source"]
            for ln in ("residential", "retail", "retail-leasing")
        )


def test_answer_includes_real_data_from_api(client):
    """The answer should reference real project / property names from the
    seed data, not generic placeholders."""
    r = _ask(client, "住宅 IRR 最高的 3 个项目")
    data = r.json()
    answer = data["answer"]
    # At least one of the residential seed project names should appear.
    # The full set is PRJ-001..008; we only check that the answer isn't
    # entirely generic.
    assert "PRJ-" in answer or any(
        city in answer for city in ("上海", "北京", "深圳", "杭州", "成都", "广州", "南京", "苏州")
    )


# ─────────────────────────────────────────────────────────────────────────
# 8) Universality: 5th business line auto-discoverable
# ─────────────────────────────────────────────────────────────────────────


def test_universality_with_temp_line(client, repo_root, tmp_path, monkeypatch):
    """A throwaway business line + minimal API endpoint should be reachable
    by the mock engine without any code change to copilot_engine.py.

    We:
      1. Create business_lines/tmp-copilot-test/ with manifest.yaml +
         indicators.yaml + api/router.py.
      2. Register it in business_lines/registry.yaml (backup first).
      3. Clear the registry cache + ask "tmp-copilot-test 的指标".
      4. Expect a 200 response that mentions the line.
      5. Restore the registry, clear cache, clean up.
    """
    import shutil

    # 1. Prepare tmp line dir
    line_id = "tmp-copilot-test"
    line_dir = repo_root / "business_lines" / line_id
    line_dir.mkdir(parents=True, exist_ok=True)
    try:
        (line_dir / "manifest.yaml").write_text(
            f"""
id: {line_id}
name: "临时测试业务线"
description: "Copilot universality fixture"
api_prefix: /api/lines/{line_id}
warehouse:
  schema: {line_id}
  dbt_schema: {line_id}
  mart_schema: {line_id}
nav:
  - path: /{line_id}
    title: "概览"
""",
            encoding="utf-8",
        )
        (line_dir / "indicators.yaml").write_text(
            """
indicators:
  - id: dummy_kpi
    title: "Dummy KPI"
    unit: "%"
    format: "percent"
charts: []
""",
            encoding="utf-8",
        )
        api_dir = line_dir / "api"
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "router.py").write_text(
            """
from fastapi import APIRouter
router = APIRouter()
@router.get("/ping")
async def ping():
    return {"status": "ok", "line": "tmp-copilot-test"}
@router.get("/indicators")
async def indicators():
    return {
        "line_id": "tmp-copilot-test",
        "indicators": [
            {"id": "dummy_kpi", "title": "Dummy KPI", "unit": "%", "format": "percent", "value": 0.5}
        ],
        "count": 1,
    }
@router.get("/projects")
async def projects():
    return {
        "line_id": "tmp-copilot-test",
        "count": 0,
        "projects": [],
    }
""",
            encoding="utf-8",
        )

        # 2. Patch registry.yaml
        reg_path = repo_root / "business_lines" / "registry.yaml"
        backup = reg_path.read_text(encoding="utf-8")
        try:
            reg_path.write_text(
                backup.rstrip() + f"\n- id: {line_id}\n  manifest: business_lines/{line_id}/manifest.yaml\n",
                encoding="utf-8",
            )
            # Bust the registry lru_caches (use the proper API).
            # Only get_project_root is wrapped with @lru_cache; load_registry
            # and load_registry_file are plain functions. The env var
            # approach is sufficient because load_registry() reads
            # get_project_root() each call.
            import os
            os.environ["FIN_BP_PROJECT_ROOT"] = str(repo_root)
            from app.core import registry as core_registry
            core_registry.get_project_root.cache_clear()

            from app.core.registry import load_registry
            entries = load_registry()
            assert any(e.line.id == line_id for e in entries), (
                f"tmp-copilot-test not in registry: {[e.line.id for e in entries]}"
            )

            # 3. Ask the copilot about the new line.
            from fastapi.testclient import TestClient
            from app.main import create_app
            fresh = TestClient(create_app())
            r = fresh.get("/api/copilot/health")
            assert r.status_code == 200
            assert line_id in r.json()["available_lines"]

            r2 = fresh.post(
                "/api/copilot/ask",
                json={"question": f"{line_id} 的指标", "line_id": line_id},
            )
            assert r2.status_code == 200, r2.text
            data = r2.json()
            # Should at minimum have a valid CopilotResponse shape.
            assert "answer" in data
            assert "intent" in data
            assert "backend" in data
            # And the answer should mention the line or its dummy KPI.
            assert line_id in data["answer"] or "Dummy KPI" in data["answer"]
        finally:
            # 5. Restore registry.yaml
            reg_path.write_text(backup, encoding="utf-8")
    finally:
        # Cleanup tmp line
        if line_dir.exists():
            shutil.rmtree(line_dir, ignore_errors=True)
        # Re-bust caches
        from app.core import registry as core_registry
        core_registry.get_project_root.cache_clear()
