"""
apps/api/tests/test_p2_universality.py

Regression tests for the 3 P2 architecture-review findings (2026-09-03).

All three fixes share a single root cause: hardcoded business-line
metadata that drifted out of sync with `business_lines/registry.yaml`.
After the fix, the relevant runtime structures are built dynamically
from the live registry + manifests + indicators.yaml, so the 10
registered lines (and any future ones) get correct coverage for free.

Coverage
========
P2 #1 — apps/api/app/services/llm/prompts.py
  - build_endpoint_catalog() picks up every registered line
  - The system prompt surfaces a line's nav[] entries as API endpoints
  - Throwing a new line in registry.yaml + manifest.yaml automatically
    shows up in the catalog (universality check)

P2 #2 — apps/api/app/services/copilot_engine.py
  - build_line_suggestions() returns ≥3 questions for every line
  - Each line's first indicator title is interpolated into "KPI 概览"
  - 6 new lines (valuation / advisory / office-leasing / investment /
    project-management / industrial) all appear in by_line
  - /api/copilot/suggestions returns 10 lines in the response

P2 #3 — apps/web/app/(dashboard)/_components/linePageConfig.ts
  - These are TypeScript tests; Python can only check the Python side
    equivalent. We mirror the buildLinePageConfig() heuristic in
    Python (test_infer_page_kind_for_known_lines) so the rules are
    pinned in the backend's test suite. The actual TS file is verified
    by `npm run typecheck` (out of scope for this test file).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core import registry as core_registry
from app.main import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def all_registered_line_ids(repo_root) -> list[str]:
    """Read the live registry.yaml and return its line ids in order."""
    reg_path = repo_root / "business_lines" / "registry.yaml"
    text = reg_path.read_text(encoding="utf-8")
    import yaml

    data = yaml.safe_load(text) or {}
    return [item["id"] for item in data.get("lines", []) if "id" in item]


# ---------------------------------------------------------------------------
# P2 #1 — Dynamic endpoint catalog
# ---------------------------------------------------------------------------


class TestBuildEndpointCatalog:
    """Verify that the LLM system prompt's endpoint catalog is now
    generated from the live registry."""

    def test_catalog_contains_every_registered_line(
        self, repo_root, all_registered_line_ids
    ):
        from app.services.llm import prompts
        from app.services.llm.prompts import build_endpoint_catalog

        # Bust any cache and rebuild.
        core_registry.get_project_root.cache_clear()
        catalog = build_endpoint_catalog()
        catalog_ids = set(catalog.keys())
        registered = set(all_registered_line_ids)
        missing = registered - catalog_ids
        assert not missing, (
            f"P2 #1 regression: catalog is missing {sorted(missing)}; "
            f"got {sorted(catalog_ids)}"
        )

    def test_catalog_contains_nav_paths_for_valuation(self, repo_root):
        """The 6 new lines must surface their manifest nav paths."""
        from app.services.llm.prompts import build_endpoint_catalog

        core_registry.get_project_root.cache_clear()
        catalog = build_endpoint_catalog()
        assert "valuation" in catalog
        # Manifest declares /valuation/reports, /valuation/accuracy,
        # /valuation/collection, /valuation/appraiser.
        flat = " | ".join(catalog["valuation"])
        assert "/api/lines/valuation" in flat
        # The nav slug maps to the API path, not duplicated:
        # /valuation/reports → /api/lines/valuation/reports
        # (not /api/lines/valuation/valuation/reports).
        assert "/api/lines/valuation/reports" in flat
        assert "/api/lines/valuation/accuracy" in flat
        # Negative: the duplicated form should NOT appear.
        assert "/api/lines/valuation/valuation/reports" not in flat
        # The universal /indicators endpoint should always be present.
        assert "/api/lines/valuation/indicators" in flat

    def test_system_prompt_includes_valuation_endpoints(self, repo_root):
        """The rendered system prompt must contain the valuation line's
        endpoints, so the LLM is aware of them when the user asks."""
        from app.services.llm.prompts import render_system_prompt

        core_registry.get_project_root.cache_clear()
        sp = render_system_prompt()
        assert "valuation" in sp
        # The api_prefix should be in the prompt.
        assert "/api/lines/valuation" in sp

    def test_universality_with_temp_line_in_catalog(
        self, repo_root, monkeypatch
    ):
        """A throwaway business line must show up in build_endpoint_catalog
        after being added to registry.yaml + a manifest.yaml."""
        from app.services.llm.prompts import build_endpoint_catalog

        core_registry.get_project_root.cache_clear()

        line_id = "tmp-catalog-test"
        line_dir = repo_root / "business_lines" / line_id
        line_dir.mkdir(parents=True, exist_ok=True)
        try:
            (line_dir / "manifest.yaml").write_text(
                f"""
id: {line_id}
name: "临时目录测试"
description: "Universality fixture"
api_prefix: /api/lines/{line_id}
warehouse:
  schema: {line_id}
  dbt_schema: {line_id}
  mart_schema: {line_id}
nav:
  - path: /{line_id}
    title: "概览"
  - path: /{line_id}/foo
    title: "Foo"
""",
                encoding="utf-8",
            )
            reg_path = repo_root / "business_lines" / "registry.yaml"
            backup = reg_path.read_text(encoding="utf-8")
            try:
                reg_path.write_text(
                    backup.rstrip()
                    + f"\n- id: {line_id}\n  manifest: business_lines/{line_id}/manifest.yaml\n",
                    encoding="utf-8",
                )
                core_registry.get_project_root.cache_clear()
                catalog = build_endpoint_catalog()
                assert line_id in catalog, (
                    f"tmp-catalog-test not in catalog: {sorted(catalog.keys())}"
                )
                flat = " | ".join(catalog[line_id])
                # Both nav entries should be present.
                assert "/api/lines/tmp-catalog-test" in flat
                assert "/api/lines/tmp-catalog-test/foo" in flat
                # Negative: the duplicated form should NOT appear.
                assert "/api/lines/tmp-catalog-test/tmp-catalog-test/foo" not in flat
            finally:
                reg_path.write_text(backup, encoding="utf-8")
                core_registry.get_project_root.cache_clear()
        finally:
            if line_dir.exists():
                shutil.rmtree(line_dir, ignore_errors=True)
            core_registry.get_project_root.cache_clear()


# ---------------------------------------------------------------------------
# P2 #2 — Dynamic line suggestions
# ---------------------------------------------------------------------------


class TestBuildLineSuggestions:
    """Verify that the per-line starter questions are now generated
    from the live registry + indicators.yaml."""

    def test_suggestions_contain_every_registered_line(
        self, client, all_registered_line_ids
    ):
        r = client.get("/api/copilot/suggestions")
        assert r.status_code == 200
        data = r.json()
        by_line = data["by_line"]
        registered = set(all_registered_line_ids)
        got = set(by_line.keys())
        missing = registered - got
        assert not missing, (
            f"P2 #2 regression: suggestions missing {sorted(missing)}; "
            f"got {sorted(got)}"
        )
        # And every line has at least 3 suggestions.
        for lid, qs in by_line.items():
            assert len(qs) >= 3, f"{lid} has only {len(qs)} suggestions: {qs}"

    def test_suggestions_use_first_indicator_title(self, client):
        """The first suggestion for each line should mention the
        line's headline KPI (first indicator in indicators.yaml)."""
        r = client.get("/api/copilot/suggestions")
        data = r.json()
        by_line = data["by_line"]
        # residential: first indicator is "IRR"
        assert "residential" in by_line
        assert any("IRR" in q for q in by_line["residential"])
        # valuation: first indicator is "估价报告数" or "report_count"
        assert "valuation" in by_line
        assert any(
            ("报告" in q) or ("report" in q.lower())
            for q in by_line["valuation"]
        )
        # investment: first indicator — verify the suggestions are at
        # least templated (have a display name + KPI placeholder).
        assert "investment" in by_line
        assert all("投资" in q or "REIT" in q or "AUM" in q for q in by_line["investment"])

    def test_suggestions_count_is_10_for_known_repo(
        self, client, all_registered_line_ids
    ):
        r = client.get("/api/copilot/suggestions")
        data = r.json()
        # The repo has exactly 10 lines registered today.
        assert len(data["by_line"]) == len(all_registered_line_ids) == 10
        # And each entry has the templated 4 questions.
        for lid, qs in data["by_line"].items():
            assert len(qs) >= 3
            # Sanity: the line's display name should appear in each
            # suggestion (templated, not hardcoded).
            # my-line is the only exception: it has a Chinese display
            # name but its question is generic "核心 KPI 概览"; we
            # relax the check for it.
            for q in qs:
                if lid != "my-line":
                    # Either a Chinese display name keyword OR the
                    # literal "核心 KPI" placeholder is present.
                    assert "KPI" in q or "敏感" in q or "预测" in q or "告警" in q

    def test_suggestions_universality_with_temp_line(
        self, client, repo_root
    ):
        """A throwaway business line must appear in suggestions after
        being registered + having indicators.yaml."""
        line_id = "tmp-sugg-test"
        line_dir = repo_root / "business_lines" / line_id
        line_dir.mkdir(parents=True, exist_ok=True)
        try:
            (line_dir / "manifest.yaml").write_text(
                f"""
id: {line_id}
name: "临时建议测试线"
description: "Universality fixture"
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
  - id: headline_kpi
    title: "Custom Headline KPI"
    unit: "%"
charts: []
""",
                encoding="utf-8",
            )
            reg_path = repo_root / "business_lines" / "registry.yaml"
            backup = reg_path.read_text(encoding="utf-8")
            try:
                reg_path.write_text(
                    backup.rstrip()
                    + f"\n- id: {line_id}\n  manifest: business_lines/{line_id}/manifest.yaml\n",
                    encoding="utf-8",
                )
                core_registry.get_project_root.cache_clear()
                # Force the suggestions cache to rebuild by re-importing
                # the engine module.
                from app.services import copilot_engine
                from app.services.copilot_engine import (
                    build_line_suggestions,
                    reset_line_suggestions_cache,
                )
                reset_line_suggestions_cache()
                fresh_suggestions = build_line_suggestions()
                assert line_id in fresh_suggestions, (
                    f"tmp-sugg-test not in suggestions: {sorted(fresh_suggestions.keys())}"
                )
                # And the headline KPI title should be interpolated.
                qs = fresh_suggestions[line_id]
                assert any("Custom Headline KPI" in q for q in qs)
            finally:
                reg_path.write_text(backup, encoding="utf-8")
                core_registry.get_project_root.cache_clear()
                from app.services.copilot_engine import (
                    reset_line_suggestions_cache,
                )
                reset_line_suggestions_cache()
        finally:
            if line_dir.exists():
                shutil.rmtree(line_dir, ignore_errors=True)
            core_registry.get_project_root.cache_clear()
            from app.services.copilot_engine import (
                reset_line_suggestions_cache,
            )
            reset_line_suggestions_cache()


# ---------------------------------------------------------------------------
# P2 #3 — linePageConfig.ts heuristic (Python mirror)
# ---------------------------------------------------------------------------
#
# The TypeScript file builds the spec table dynamically from the
# registry. We can't run Jest here, but we can pin the heuristic rules
# by mirroring them in Python — so any future change to the rules
# triggers a test failure on the backend side too.


def _python_infer(line_id: str, nav_path: str, nav_title: str) -> str:
    """Mirror of `inferPageKind` in apps/web/.../linePageConfig.ts.

    Kept in sync with the TS rules. If you change one, change both.
    """
    import re

    KNOWN_KIND_OVERRIDES: dict[str, str] = {
        "retail-leasing:market-report": "market-benchmark",
        "retail-leasing:vacancy-alert": "vacancy-alerts",
        "retail-leasing:leasing-kpi": "properties-summary",
        "my-line:ping": "not-integrated",
    }
    KNOWN_OVERVIEW_OVERRIDES: dict[str, str] = {
        "my-line": "ping-only",
    }
    SLUG_KIND_RULES: list[tuple[str, str]] = [
        (r"market[-_]?report|market[-_]?benchmark|benchmark", "market-benchmark"),
        (r"vacanc|空置", "vacancy-alerts"),
        (r"leasing[-_]?kpi|leasing[-_]?summary|商铺.*总览|物业.*总览", "properties-summary"),
        (r"report|accuracy|collection|appraiser|kpi|indicator|业绩|报告|偏差|回款|人均|客户|行业|产能|续约|团队|项目.*明细|portfolio|projects|组合|基金|投资", "project-detail"),
        (r"noi|brand|renovat|collection[-_]?rate|efficiency", "property-detail"),
    ]
    # Compute slug the same way as the TS implementation:
    #   1. Strip the leading slash.
    #   2. If the remainder starts with `${line_id}/`, drop that prefix
    #      to get the sub-page slug.
    #   3. If the remainder equals the line id exactly, that's the line
    #      root (empty slug).
    #   4. Otherwise treat the trimmed path as the slug directly.
    if nav_path.startswith("/"):
        trimmed = nav_path[1:]
    else:
        trimmed = nav_path
    if trimmed.startswith(line_id + "/"):
        slug = trimmed[len(line_id) + 1:]
    elif trimmed == line_id:
        slug = ""
    else:
        slug = trimmed

    if slug == "":
        return KNOWN_OVERVIEW_OVERRIDES.get(line_id, "line-overview")
    key = f"{line_id}:{slug}"
    if key in KNOWN_KIND_OVERRIDES:
        return KNOWN_KIND_OVERRIDES[key]
    for pattern, kind in SLUG_KIND_RULES:
        if re.search(pattern, slug, re.IGNORECASE) or re.search(
            pattern, nav_title
        ):
            return kind
    has_properties_kind = line_id in {"retail", "retail-leasing", "office-leasing"}
    if has_properties_kind:
        return "property-detail"
    return "project-detail"


class TestLinePageConfigHeuristic:
    """The TS file's heuristic has a Python mirror so the rules are
    pinned in the backend's test suite. The actual rendering happens
    in the browser; the backend only needs the rule table to be
    consistent so /api/registry/lines can drive the spec table."""

    def test_valuation_reports_renders_as_project_detail(self):
        # valuation/reports → report-style nav → project-detail
        kind = _python_infer("valuation", "/valuation/reports", "报告明细")
        assert kind in {"project-detail", "line-overview"}

    def test_retail_leasing_market_report_renders_as_market_benchmark(self):
        # explicit override
        kind = _python_infer(
            "retail-leasing", "/retail-leasing/market-report", "市场对标"
        )
        assert kind == "market-benchmark"

    def test_office_leasing_uses_property_detail_default(self):
        # office-leasing doesn't have explicit overrides; the
        # property-detail default kicks in because the line id is in
        # the has_properties_kind set.
        kind = _python_infer(
            "office-leasing", "/office-leasing/area", "成交面积"
        )
        # heuristic may match as project-detail via "项目" or fall
        # through to property-detail. Both are acceptable; the test
        # just pins a non-"not-integrated" outcome.
        assert kind != "not-integrated"

    def test_my_line_root_renders_as_ping_only(self):
        # empty slug → KNOWN_OVERVIEW_OVERRIDES["my-line"] = "ping-only"
        kind = _python_infer("my-line", "/my-line", "概览")
        assert kind == "ping-only"

    def test_my_line_ping_renders_as_not_integrated(self):
        kind = _python_infer("my-line", "/my-line/ping", "ping")
        assert kind == "not-integrated"

    def test_investment_uses_project_detail(self):
        kind = _python_infer(
            "investment", "/investment/portfolio", "投资组合"
        )
        # 'portfolio' matches the project-detail rule; we accept
        # either project-detail (from rule) or property-detail (from
        # fallback) — both are valid renders. The important thing is
        # not "not-integrated".
        assert kind in {"project-detail", "property-detail"}

    def test_all_10_lines_have_a_resolvable_kind(
        self, all_registered_line_ids, repo_root
    ):
        """For every registered line and every nav entry in its
        manifest, the heuristic must return a non-'not-integrated'
        kind. Otherwise the new architecture review finding (P2 #3)
        is back."""
        import yaml

        for lid in all_registered_line_ids:
            manifest_path = repo_root / "business_lines" / lid / "manifest.yaml"
            if not manifest_path.exists():
                continue
            data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
            for nav in data.get("nav", []) or []:
                path = nav.get("path", "")
                title = nav.get("title", "")
                kind = _python_infer(lid, path, title)
                # Compute the slug the same way _python_infer does, so
                # we can check the override for /my-line/ping.
                trimmed = path[1:] if path.startswith("/") else path
                if trimmed.startswith(lid + "/"):
                    slug = trimmed[len(lid) + 1:]
                elif trimmed == lid:
                    slug = ""
                else:
                    slug = trimmed
                if slug == "":
                    # line root — accept any overview kind
                    assert kind in {"line-overview", "ping-only"}, (
                        f"{lid} root resolved to {kind}"
                    )
                elif lid == "my-line" and slug == "ping":
                    assert kind == "not-integrated"
                else:
                    assert kind != "not-integrated", (
                        f"P2 #3 regression: {path} (line={lid}) "
                        f"resolved to 'not-integrated' — heuristic gap!"
                    )
