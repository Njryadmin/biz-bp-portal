"""
apps/api/tests/test_scrapers.py

Tests for the web-scraping framework (apps/api/app/services/scrapers)
and its HTTP router.

Coverage:
  1-3.  BaseScraper lifecycle (validate / validate passthrough / to_landing_row)
  4-6.  Registry (register / get / get_all / discover / reset)
  7-10. Utils (http_get smoke / retry / rate-limit)
  11-13. nbs_house_price (parse / fallback / fetch-failure path)
  14-19. lianjia_deals (parse with real fixture / fallback / fetch-failure /
                      validate keeps good rows / parse fails on broken HTML /
                      multi-city fixture parses all cities)
  20-24. policy_crawler (parse merges corpus / live parse with fixture /
                         validate dedupes / fallback returns corpus /
                         parse fails gracefully on broken HTML)
  25-29. HTTP router (list / detail / run one / run all / 404)
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.scrapers import (
    BaseScraper,
    ScraperRunResult,
    get,
    get_all,
    http_get,
    rate_limit_check,
    register,
    reset_rate_limit,
    retry_with_backoff,
    run_one,
)
from app.services.scrapers import registry as registry_module
from app.services.scrapers.scrapers.lianjia_deals import (
    LIANJIA_CITIES,
    LianjiaDealsScraper,
)
from app.services.scrapers.scrapers.nbs_house_price import (
    NbsHousePriceScraper,
)
from app.services.scrapers.scrapers.policy_crawler import (
    MOHURD_INDEX,
    PolicyCrawler,
    _HISTORICAL_POLICIES,
)


# ─────────────────────────────────────────────────────────────────────────
# Path constants — fixture files captured from real network responses
# ─────────────────────────────────────────────────────────────────────────

_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"


def _fixture(name: str) -> str:
    """Read a fixture file and return its UTF-8 text. Skip if missing."""
    path = _FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return path.read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_registry():
    """Wipe the registry + limiter between tests so order doesn't matter."""
    registry_module.reset()
    reset_rate_limit()
    yield
    registry_module.reset()
    reset_rate_limit()


@pytest.fixture
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────
# 1-3) BaseScraper lifecycle
# ─────────────────────────────────────────────────────────────────────────


def test_base_scraper_validate_drops_rows_with_missing_required_fields():
    class _Demo(BaseScraper):
        source_id = "demo_x"
        name = "Demo"
        required_fields = ("city", "period")

        async def fetch(self):
            return []

        def parse(self, raw):
            return []

    s = _Demo()
    rows = [
        {"city": "BJ", "period": "2024-12"},
        {"city": "", "period": "2024-12"},
        {"city": "SH", "period": ""},
        {"city": "GZ", "period": "2024-12"},
    ]
    kept = s.validate(rows)
    assert len(kept) == 2
    assert {r["city"] for r in kept} == {"BJ", "GZ"}


def test_base_scraper_validate_keeps_rows_when_no_required_fields():
    class _Demo(BaseScraper):
        source_id = "demo_y"
        name = "Demo"

        async def fetch(self):
            return []

        def parse(self, raw):
            return []

    s = _Demo()
    rows = [{"a": 1}, {"a": 2}, {"b": None}]
    assert s.validate(rows) == rows


def test_base_scraper_to_landing_row_stamps_source_and_fetched_at():
    class _Demo(BaseScraper):
        source_id = "demo_z"
        name = "Demo"

        async def fetch(self):
            return []

        def parse(self, raw):
            return []

    s = _Demo()
    out = s.to_landing_row({"city": "BJ", "period": "2024-12"})
    assert out["source"] == "demo_z"
    assert "fetched_at" in out
    # parseable
    datetime.fromisoformat(out["fetched_at"])


# ─────────────────────────────────────────────────────────────────────────
# 4-6) Registry
# ─────────────────────────────────────────────────────────────────────────


def test_registry_register_get_get_all():
    class _Demo(BaseScraper):
        source_id = "reg_test_1"
        name = "RegTest1"

        async def fetch(self):
            return []

        def parse(self, raw):
            return []

    s = _Demo()
    register(s)
    assert get("reg_test_1") is s
    assert s in get_all()


def test_registry_discover_scrapers_finds_three():
    registry_module.discover_scrapers()
    found = {s.source_id for s in get_all()}
    # All three bundled scrapers should be present.
    assert {"nbs_house_price", "lianjia_deals", "policy_crawler"}.issubset(found)


def test_registry_reset_clears():
    register(BaseScraper.__new__(NbsHousePriceScraper))  # instance w/o init
    assert len(get_all()) >= 1
    registry_module.reset()
    assert get_all() == []


# ─────────────────────────────────────────────────────────────────────────
# 7-10) Utils
# ─────────────────────────────────────────────────────────────────────────


def test_http_get_smoke():
    """Hit a tiny local URL to ensure the call works. We use example.org
    which serves a small static page."""
    r = http_get("https://example.org/", timeout=10)
    assert r.status_code == 200
    assert "Example" in r.text


def test_retry_with_backoff_succeeds_after_transient():
    calls = {"n": 0}

    @retry_with_backoff(max_retries=2, base_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("blip")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_retry_with_backoff_raises_after_exhausting():
    calls = {"n": 0}

    @retry_with_backoff(max_retries=2, base_delay=0.01)
    def always_fail():
        calls["n"] += 1
        raise ConnectionError("nope")

    with pytest.raises(ConnectionError):
        always_fail()
    assert calls["n"] == 3  # initial + 2 retries


def test_rate_limit_check_enforces_budget():
    reset_rate_limit("example.com")
    for i in range(3):
        assert rate_limit_check("example.com", max_per_minute=3) is True
    # 4th call exceeds budget
    assert rate_limit_check("example.com", max_per_minute=3) is False
    # Other domain is independent
    assert rate_limit_check("other.com", max_per_minute=3) is True


# ─────────────────────────────────────────────────────────────────────────
# 11-13) NBS scraper
# ─────────────────────────────────────────────────────────────────────────


def test_nbs_parse_builds_canonical_rows():
    html = """
    <html><body>
    <h2>2024年12月份70个大中城市新建商品住宅价格指数</h2>
    <table>
      <tr><th>城市</th><th>同比</th><th>环比</th></tr>
      <tr><td>北京</td><td>-1.2</td><td>-0.3</td></tr>
      <tr><td>上海</td><td>2.1</td><td>0.4</td></tr>
    </table>
    <h2>2024年12月份70个大中城市二手住宅价格指数</h2>
    <table>
      <tr><th>城市</th><th>同比</th><th>环比</th></tr>
      <tr><td>北京</td><td>-4.5</td><td>-0.7</td></tr>
      <tr><td>上海</td><td>-2.3</td><td>-0.2</td></tr>
    </table>
    </body></html>
    """
    s = NbsHousePriceScraper()
    parsed = s.parse([{"_url": "x", "_html": html}])
    # Should give us 4 records (2 cities × 2 tables).
    assert len(parsed) == 4
    by_city_period = {(r["city"], r["period"]): r for r in parsed}
    assert "2024-12" in {r["period"] for r in parsed}
    # New-home rows exist.
    beijing = [r for r in parsed if r["city"] == "北京" and r["new_home_index_yoy"] is not None]
    assert beijing and beijing[0]["new_home_index_yoy"] == -1.2


def test_nbs_fallback_returns_three_cities():
    s = NbsHousePriceScraper()
    fallback = s.fallback()
    assert len(fallback) >= 3
    assert all(r.get("is_fallback") is True for r in fallback)
    assert all(r.get("city") for r in fallback)


def test_nbs_fetch_failure_triggers_fallback(monkeypatch):
    s = NbsHousePriceScraper()
    # Patch the instance's fetch method to always raise. This is more
    # robust than monkey-patching the module-level ``http_get`` name
    # because the autouse ``_clear_registry`` fixture evicts the
    # cached module from ``sys.modules`` between tests.
    async def _raise():
        raise httpx.HTTPError("blocked in test")
    monkeypatch.setattr(s, "fetch", _raise)
    result = asyncio.run(s.run())
    assert result.status == "degraded"
    assert result.used_fallback is True
    assert result.rows >= 3


# ─────────────────────────────────────────────────────────────────────────
# 14-19) Lianjia scraper
# ─────────────────────────────────────────────────────────────────────────


def test_lianjia_parse_builds_rows_from_real_fixture():
    """Parse a real lianjia ershoufang page → ≥ 1 valid row per city."""
    s = LianjiaDealsScraper()
    html = _fixture("lianjia_ershoufang_bj.html")
    raw = [
        {"city": "北京", "city_code": "bj", "period": "2026-09",
         "_url": "https://bj.lianjia.com/ershoufang/", "_html": html},
    ]
    parsed = s.parse(raw)
    # We expect at least 1 listing; the live page typically has 30.
    assert len(parsed) >= 1
    # The first row carries the canonical schema:
    row = parsed[0]
    for key in ("city", "district", "period", "unit_price_yuan_sqm",
                "total_price_wan", "title", "source"):
        assert key in row, f"missing {key} in row {row!r}"
    assert row["city"] == "北京"
    # All rows pass validate (have city, district, period, and a price signal)
    valid = s.validate(parsed)
    assert len(valid) == len(parsed)


def test_lianjia_parse_multi_city_fixtures():
    """Parse all four city fixtures → at least one row per city."""
    s = LianjiaDealsScraper()
    raw = []
    for code, name in LIANJIA_CITIES:
        raw.append(
            {
                "city": name,
                "city_code": code,
                "period": "2026-09",
                "_url": f"https://{code}.lianjia.com/ershoufang/",
                "_html": _fixture(f"lianjia_ershoufang_{code}.html"),
            }
        )
    parsed = s.parse(raw)
    valid = s.validate(parsed)
    # 4 cities × 30 listings = 120 rows in the live run; here we assert
    # the lower bound (real fixture should be ≥ 100).
    assert len(valid) >= 100, f"expected ≥ 100 valid rows, got {len(valid)}"
    cities = {r["city"] for r in valid}
    assert {"北京", "上海", "深圳", "广州"}.issubset(cities)


def test_lianjia_parse_raises_on_broken_html():
    """If every city page returns an empty body, parse() raises."""
    s = LianjiaDealsScraper()
    raw = [
        {"city": "北京", "city_code": "bj", "period": "2026-09",
         "_url": "x", "_html": ""},
    ]
    with pytest.raises(RuntimeError):
        s.parse(raw)


def test_lianjia_parse_skips_individual_failing_pages():
    """One bad city shouldn't sink the whole run."""
    s = LianjiaDealsScraper()
    raw = [
        {"city": "北京", "city_code": "bj", "period": "2026-09",
         "_url": "x", "_html": ""},
        {"city": "上海", "city_code": "sh", "period": "2026-09",
         "_url": "y", "_html": _fixture("lianjia_ershoufang_sh.html")},
    ]
    parsed = s.parse(raw)
    # Shanghai rows should still come through.
    shanghai = [r for r in parsed if r["city"] == "上海"]
    assert len(shanghai) >= 1


def test_lianjia_fallback_has_at_least_five_rows():
    s = LianjiaDealsScraper()
    fb = s.fallback()
    assert len(fb) >= 5
    assert all(r.get("is_fallback") is True for r in fb)
    cities = {r["city"] for r in fb}
    assert len(cities) >= 2


def test_lianjia_fetch_failure_triggers_fallback(monkeypatch):
    s = LianjiaDealsScraper()
    async def _raise():
        raise httpx.HTTPError("blocked in test")
    monkeypatch.setattr(s, "fetch", _raise)
    result = asyncio.run(s.run())
    assert result.status == "degraded"
    assert result.used_fallback is True
    assert result.rows >= 5


def test_lianjia_fetch_rate_limit_triggers_fallback(monkeypatch):
    """The framework's ``rate_limit_check`` is a polite, in-process throttle.
    When the budget is exhausted, ``fetch()`` raises and the fallback
    chain takes over. We use max_per_minute=4 to match what ``fetch()``
    itself uses, otherwise the per-call budget differs from the deque
    length and the test silently passes.
    """
    s = LianjiaDealsScraper()
    # exhaust the per-domain budget (max_per_minute=4, the same value
    # the lianjia fetch() uses internally).
    from app.services.scrapers.scrapers import lianjia_deals as lianjia_mod
    reset_rate_limit("lianjia.com")
    for _ in range(10):
        lianjia_mod.rate_limit_check("lianjia.com", max_per_minute=4)
    result = asyncio.run(s.run())
    assert result.status == "degraded"
    assert result.used_fallback is True
    assert result.rows >= 5


# ─────────────────────────────────────────────────────────────────────────
# 20-24) Policy crawler
# ─────────────────────────────────────────────────────────────────────────


def test_policy_parse_merges_corpus_with_live():
    s = PolicyCrawler()
    out = s.parse([])
    # No live data → just the historical corpus.
    assert len(out) == len(_HISTORICAL_POLICIES)
    # All have policy_id and the level set.
    for r in out:
        assert r.get("policy_id")
        assert r.get("level") in {"国家", "省", "市"}


def test_policy_parse_real_mohurd_fixture():
    """Parse the captured mohurd.gov.cn homepage → ≥ 1 live policy row."""
    s = PolicyCrawler()
    html = _fixture("mohurd_home.html")
    out = s.parse([{"_url": MOHURD_INDEX, "_html": html}])
    # The corpus is always present; the fixture should add live ones.
    live = [r for r in out if r.get("policy_id", "").startswith("POL-MOHURD-")]
    assert len(live) >= 1, "expected ≥ 1 live policy from the mohurd fixture"
    for r in live:
        assert r.get("title")
        assert r.get("publish_date")
        assert r.get("city") == "全国"
        assert r.get("level") == "国家"
        assert r.get("source_url", "").startswith("http")


def test_policy_validate_dedupes_by_policy_id():
    s = PolicyCrawler()
    rows = list(_HISTORICAL_POLICIES) + [
        dict(_HISTORICAL_POLICIES[0]),  # duplicate
        {
            "policy_id": "",  # invalid (no id)
            "title": "no id",
            "publish_date": "2024-12-01",
            "city": "全国",
            "level": "国家",
            "content": "",
        },
    ]
    validated = s.validate(rows)
    # Should drop the duplicate + the empty-id row.
    assert len(validated) == len(_HISTORICAL_POLICIES)
    ids = [r["policy_id"] for r in validated]
    assert len(set(ids)) == len(ids)


def test_policy_fallback_returns_full_corpus():
    s = PolicyCrawler()
    fb = s.fallback()
    assert len(fb) == len(_HISTORICAL_POLICIES)
    assert all(r.get("is_fallback") is True for r in fb)


def test_policy_parse_raises_no_when_live_empty():
    """If the live page is empty, parse() returns the curated corpus
    (does NOT raise) so the DBT mart always has rows to consume."""
    s = PolicyCrawler()
    out = s.parse([{"_url": MOHURD_INDEX, "_html": ""}])
    assert len(out) == len(_HISTORICAL_POLICIES)


def test_policy_fetch_failure_triggers_fallback(monkeypatch):
    s = PolicyCrawler()
    async def _raise():
        raise httpx.HTTPError("blocked in test")
    monkeypatch.setattr(s, "fetch", _raise)
    result = asyncio.run(s.run())
    assert result.status == "degraded"
    assert result.used_fallback is True
    assert result.rows == len(_HISTORICAL_POLICIES)


# ─────────────────────────────────────────────────────────────────────────
# 25-29) HTTP router
# ─────────────────────────────────────────────────────────────────────────


def test_http_list_scrapers_has_three(client_with_auth):
    r = client_with_auth.get("/api/scrapers")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    ids = {s["source_id"] for s in data}
    assert {"nbs_house_price", "lianjia_deals", "policy_crawler"}.issubset(ids)


def test_http_get_scraper_detail(client_with_auth):
    r = client_with_auth.get("/api/scrapers/nbs_house_price")
    assert r.status_code == 200
    data = r.json()
    assert data["source_id"] == "nbs_house_price"
    assert "history" in data
    assert isinstance(data["history"], list)


def test_http_run_one_scraper(client_with_auth, monkeypatch):
    """Run returns rows ≥ 0 even when DB is unavailable (persist is best-effort).

    We patch each scraper's ``fetch`` to raise so the fallback path is
    exercised deterministically (no real network).
    """
    # Patch fetch on every bundled scraper to raise immediately.
    from app.services.scrapers.scrapers import nbs_house_price as nbs_mod
    from app.services.scrapers.scrapers import lianjia_deals as lianjia_mod
    from app.services.scrapers.scrapers import policy_crawler as policy_mod
    import httpx as _httpx

    def _raise(*a, **kw):
        raise _httpx.HTTPError("blocked in test")

    monkeypatch.setattr(nbs_mod, "http_get", _raise)
    monkeypatch.setattr(lianjia_mod, "http_get", _raise)
    monkeypatch.setattr(policy_mod, "http_get", _raise)

    r = client_with_auth.post("/api/scrapers/nbs_house_price/run")
    assert r.status_code == 200
    data = r.json()
    assert data["source_id"] == "nbs_house_price"
    # Either we get mock fallback rows (status=degraded) or a real fetch (status=ok).
    assert data["status"] in {"ok", "degraded", "error"}
    assert data["rows"] >= 0


def test_http_run_all_scrapers(client_with_auth, monkeypatch):
    from app.services.scrapers.scrapers import nbs_house_price as nbs_mod
    from app.services.scrapers.scrapers import lianjia_deals as lianjia_mod
    from app.services.scrapers.scrapers import policy_crawler as policy_mod
    import httpx as _httpx

    def _raise(*a, **kw):
        raise _httpx.HTTPError("blocked in test")

    monkeypatch.setattr(nbs_mod, "http_get", _raise)
    monkeypatch.setattr(lianjia_mod, "http_get", _raise)
    monkeypatch.setattr(policy_mod, "http_get", _raise)

    r = client_with_auth.post("/api/scrapers/run-all")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    assert len(data["results"]) >= 3
    for item in data["results"]:
        assert "source_id" in item
        assert "rows" in item


def test_http_unknown_scraper_returns_404(client_with_auth):
    r = client_with_auth.get("/api/scrapers/does_not_exist_xyz")
    assert r.status_code == 404
    r2 = client_with_auth.post("/api/scrapers/does_not_exist_xyz/run")
    assert r2.status_code == 404
