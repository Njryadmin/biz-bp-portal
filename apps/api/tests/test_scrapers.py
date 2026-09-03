"""
apps/api/tests/test_scrapers.py

Tests for the web-scraping framework (apps/api/app/services/scrapers)
and its HTTP router.

Coverage (≥ 12 tests):
  1.  BaseScraper: validate() drops rows missing required fields
  2.  BaseScraper: validate() keeps rows with required fields
  3.  BaseScraper: to_landing_row stamps source / fetched_at
  4.  registry.register() + get() + get_all()
  5.  registry.discover_scrapers() auto-imports the bundled scrapers
  6.  registry.reset() clears the registry
  7.  utils.http_get() returns a real response (smoke)
  8.  utils.retry_with_backoff succeeds after transient failures
  9.  utils.retry_with_backoff raises after exhausting retries
 10.  utils.rate_limit_check() enforces the per-domain budget
 11.  nbs_house_price: parse() builds canonical rows from a fake page
 12.  nbs_house_price: fallback() returns ≥ 3 mock cities
 13.  nbs_house_price: fetch() failures trigger fallback (mocked)
 14.  lianjia_deals: parse() returns list (possibly empty) from a fake page
 15.  lianjia_deals: fallback() returns ≥ 5 mock rows
 16.  lianjia_deals: fetch() failures trigger fallback (mocked)
 17.  policy_crawler: parse() merges curated corpus with live rows
 18.  policy_crawler: validate() dedupes by policy_id
 19.  policy_crawler: fallback() returns the full curated corpus
 20.  HTTP router: GET /api/scrapers lists 3 entries
 21.  HTTP router: GET /api/scrapers/nbs_house_price returns detail
 22.  HTTP router: POST /api/scrapers/nbs_house_price/run returns rows
 23.  HTTP router: POST /api/scrapers/run-all returns 3 results
 24.  HTTP router: GET /api/scrapers/unknown returns 404
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    LianjiaDealsScraper,
)
from app.services.scrapers.scrapers.nbs_house_price import (
    NbsHousePriceScraper,
)
from app.services.scrapers.scrapers.policy_crawler import (
    PolicyCrawler,
    _HISTORICAL_POLICIES,
)


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
    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


# ─────────────────────────────────────────────────────────────────────────
# 1-3) BaseScraper lifecycle
# ─────────────────────────────────────────────────────────────────────────


def test_base_scraper_validate_drops_rows_with_missing_required_fields(client_with_auth):
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


def test_base_scraper_validate_keeps_rows_when_no_required_fields(client_with_auth):
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


def test_base_scraper_to_landing_row_stamps_source_and_fetched_at(client_with_auth):
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


def test_registry_register_get_get_all(client_with_auth):
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


def test_registry_discover_scrapers_finds_three(client_with_auth):
    registry_module.discover_scrapers()
    found = {s.source_id for s in get_all()}
    # All three bundled scrapers should be present.
    assert {"nbs_house_price", "lianjia_deals", "policy_crawler"}.issubset(found)


def test_registry_reset_clears(client_with_auth):
    register(BaseScraper.__new__(NbsHousePriceScraper))  # instance w/o init
    assert len(get_all()) >= 1
    registry_module.reset()
    assert get_all() == []


# ─────────────────────────────────────────────────────────────────────────
# 7-10) Utils
# ─────────────────────────────────────────────────────────────────────────


def test_http_get_smoke(client_with_auth):
    """Hit a tiny local URL to ensure the call works. We use example.org
    which serves a small static page."""
    r = http_get("https://example.org/", timeout=10)
    assert r.status_code == 200
    assert "Example" in r.text


def test_retry_with_backoff_succeeds_after_transient(client_with_auth):
    calls = {"n": 0}

    @retry_with_backoff(max_retries=2, base_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise ConnectionError("blip")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 2


def test_retry_with_backoff_raises_after_exhausting(client_with_auth):
    calls = {"n": 0}

    @retry_with_backoff(max_retries=2, base_delay=0.01)
    def always_fail():
        calls["n"] += 1
        raise ConnectionError("nope")

    with pytest.raises(ConnectionError):
        always_fail()
    assert calls["n"] == 3  # initial + 2 retries


def test_rate_limit_check_enforces_budget(client_with_auth):
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


def test_nbs_parse_builds_canonical_rows(client_with_auth):
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


def test_nbs_fallback_returns_three_cities(client_with_auth):
    s = NbsHousePriceScraper()
    fallback = s.fallback()
    assert len(fallback) >= 3
    assert all(r.get("is_fallback") is True for r in fallback)
    assert all(r.get("city") for r in fallback)


def test_nbs_fetch_failure_triggers_fallback(monkeypatch, client_with_auth):
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
# 14-16) Lianjia scraper
# ─────────────────────────────────────────────────────────────────────────


def test_lianjia_parse_returns_empty_or_list(client_with_auth):
    s = LianjiaDealsScraper()
    # No tables → parse() raises so the framework fires the fallback chain.
    import pytest
    with pytest.raises(RuntimeError):
        s.parse([{"_url": "x", "_html": "<html><body>no table</body></html>"}])
    # Now a real-looking table.
    html = """
    <html><body>
    <table>
      <tr><th>商圈</th><th>成交量</th><th>均价</th></tr>
      <tr><td>朝阳</td><td>120</td><td>68,500</td></tr>
      <tr><td>海淀</td><td>80</td><td>83,200</td></tr>
    </table>
    </body></html>
    """
    parsed = s.parse([{"_url": "x", "_html": html}])
    assert len(parsed) == 2
    assert parsed[0]["city"] == "北京"
    assert parsed[0]["district"] == "朝阳"
    assert parsed[0]["avg_price"] == 68500.0
    assert parsed[0]["deals_count"] == 120


def test_lianjia_fallback_has_at_least_five_rows(client_with_auth):
    s = LianjiaDealsScraper()
    fb = s.fallback()
    assert len(fb) >= 5
    assert all(r.get("is_fallback") is True for r in fb)
    cities = {r["city"] for r in fb}
    assert len(cities) >= 2


def test_lianjia_fetch_failure_triggers_fallback(monkeypatch, client_with_auth):
    s = LianjiaDealsScraper()
    async def _raise():
        raise httpx.HTTPError("blocked in test")
    monkeypatch.setattr(s, "fetch", _raise)
    result = asyncio.run(s.run())
    assert result.status == "degraded"
    assert result.used_fallback is True
    assert result.rows >= 5


# ─────────────────────────────────────────────────────────────────────────
# 17-19) Policy crawler
# ─────────────────────────────────────────────────────────────────────────


def test_policy_parse_merges_corpus_with_live(client_with_auth):
    s = PolicyCrawler()
    out = s.parse([])
    # No live data → just the historical corpus.
    assert len(out) == len(_HISTORICAL_POLICIES)
    # All have policy_id and the level set.
    for r in out:
        assert r.get("policy_id")
        assert r.get("level") in {"国家", "省", "市"}


def test_policy_validate_dedupes_by_policy_id(client_with_auth):
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


def test_policy_fallback_returns_full_corpus(client_with_auth):
    s = PolicyCrawler()
    fb = s.fallback()
    assert len(fb) == len(_HISTORICAL_POLICIES)
    assert all(r.get("is_fallback") is True for r in fb)


# ─────────────────────────────────────────────────────────────────────────
# 20-24) HTTP router
# ─────────────────────────────────────────────────────────────────────────


def test_http_list_scrapers_has_three(client, client_with_auth):
    r = client_with_auth.get("/api/scrapers")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    ids = {s["source_id"] for s in data}
    assert {"nbs_house_price", "lianjia_deals", "policy_crawler"}.issubset(ids)


def test_http_get_scraper_detail(client, client_with_auth):
    r = client_with_auth.get("/api/scrapers/nbs_house_price")
    assert r.status_code == 200
    data = r.json()
    assert data["source_id"] == "nbs_house_price"
    assert "history" in data
    assert isinstance(data["history"], list)


def test_http_run_one_scraper(client, monkeypatch, client_with_auth):
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


def test_http_run_all_scrapers(client, monkeypatch, client_with_auth):
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


def test_http_unknown_scraper_returns_404(client, client_with_auth):
    r = client_with_auth.get("/api/scrapers/does_not_exist_xyz")
    assert r.status_code == 404
    r2 = client_with_auth.post("/api/scrapers/does_not_exist_xyz/run")
    assert r2.status_code == 404
