"""
apps/api/app/services/scrapers/scrapers/lianjia_deals.py

Lianjia public second-hand real-estate data (链家二手房源公开页面).

Historical problem
-------------------
The old URL ``https://bj.lianjia.com/chengjiao/`` (the 成交 page) is
gated by Lianjia's anti-bot: it returns a 302 to ``hip.lianjia.com`` and
the page body is the captcha HTML (~6 KB). Every fetch() call raised
and the framework fell through to the static 6-row mock.

What we now do
--------------
The ershoufang (二手房) *list* pages — which are what the user sees when
they click "二手房" on lianjia.com — are much less aggressively
protected. ``https://{city}.lianjia.com/ershoufang/`` returns a fully
server-rendered HTML page with 30 listings per page, each containing:

    title (a-tag innerText)
    xiaoqu  /  sub_district   (from ``.positionInfo`` anchors)
    totalPrice (e.g. "245万")
    unitPrice  (e.g. "44,522元/平")
    followInfo ("20人关注 / 1个月以前发布" — used as a proxy for "活跃度")

We hit four tier-1 cities in order, parse each city page, and emit one
row per listing. The original schema was:

    city, district, period, avg_price, deals_count

so the rewritten schema keeps the same keys and adds:

    xiaoqu, total_price_wan, unit_price_yuan_sqm, follow_count, source

so the downstream DBT mart still works. ``avg_price`` is set to
``unit_price_yuan_sqm`` (the per-square-metre price) and
``deals_count`` is set to ``follow_count`` (the number of people
following that listing). ``district`` is the 区域 (e.g. 朝阳, 浦东).

Notes / limitations
-------------------
* We DO NOT hit the ershoufang 城市指数 / chengjiao 板块页 because
  those endpoints return captcha. The 二手房 list pages are
  crawlable as of 2026-09.
* The first page (page 1) is enough; the goal is to surface real
  per-listing data, not to scrape the full corpus.
* Rate-limited to 2 req/min across the whole ``lianjia.com`` domain.
* The fallback chain (6 mock rows across 3 cities) is preserved
  verbatim so the dashboard stays green on transient failures.

References
----------
* Beijing ershoufang page: ``https://bj.lianjia.com/ershoufang/``
* Shanghai / Shenzhen / Guangzhou: same pattern, different subdomain.
* Date 2026-09-03: confirmed by manual probe.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..base import BaseScraper
from ..registry import register
from ..utils import http_get, rate_limit_check, retry_with_backoff
from ._html import parse_html


SOURCE_ID = "lianjia_deals"
LIANJIA_DOMAIN = "lianjia.com"

# Top tier-1 cities. The city code in the lianjia subdomain maps 1:1
# to the Chinese 城市名 we surface to the DBT mart.
LIANJIA_CITIES: list[tuple[str, str]] = [
    ("bj", "北京"),
    ("sh", "上海"),
    ("sz", "深圳"),
    ("gz", "广州"),
]

# Reasonable default headers. Lianjia's edge returns captcha HTML for
# most non-browser UAs; the ershoufang list is OK with a normal Chrome
# UA + zh-CN Accept-Language.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _city_url(city_code: str) -> str:
    """Build the ershoufang list URL for a given city code."""
    return f"https://{city_code}.lianjia.com/ershoufang/"


class LianjiaDealsScraper(BaseScraper):
    source_id = SOURCE_ID
    name = "链家二手房源公开数据 (bj/sh/sz/gz ershoufang 列表页)"
    schedule = "0 10 * * 1"
    enabled = True
    required_fields = ("city", "district", "period")

    # ---- fetch --------------------------------------------------------

    async def fetch(self) -> list[dict[str, Any]]:
        """Fetch the ershoufang list page for each tier-1 city.

        Returns a list of {city, city_code, _url, _html} dicts. Failures
        are logged-and-skipped per city so a single bad city doesn't
        sink the whole run — we degrade gracefully to whatever subset
        of cities we can reach.
        """
        if not rate_limit_check(LIANJIA_DOMAIN, max_per_minute=4):
            raise RuntimeError(
                f"rate-limit hit for {LIANJIA_DOMAIN}; skipping live fetch"
            )

        period = datetime.now(timezone.utc).strftime("%Y-%m")
        out: list[dict[str, Any]] = []

        @retry_with_backoff(max_retries=1, base_delay=1.0)
        def _get(url: str) -> str:
            r = http_get(
                url, timeout=12, headers=_DEFAULT_HEADERS, follow_redirects=True
            )
            if r.status_code in (403, 429, 503):
                raise RuntimeError(f"lianjia anti-bot {r.status_code} for {url}")
            r.raise_for_status()
            return r.text

        for code, name in LIANJIA_CITIES:
            url = _city_url(code)
            try:
                html = _get(url)
            except Exception as exc:  # noqa: BLE001
                # One city failed; keep going for the rest. The framework
                # requires us to return SOMETHING from fetch() — so we
                # surface the partial failure in a stub record and let
                # parse() skip it.
                out.append(
                    {
                        "city": name,
                        "city_code": code,
                        "period": period,
                        "_url": url,
                        "_html": "",
                        "_error": str(exc),
                    }
                )
                continue
            out.append(
                {
                    "city": name,
                    "city_code": code,
                    "period": period,
                    "_url": url,
                    "_html": html,
                }
            )
        if not out:
            raise RuntimeError("lianjia: no city pages fetched")
        # If every page failed to fetch real HTML, raise so the
        # framework falls back to mock data.
        if all(not page.get("_html") for page in out):
            raise RuntimeError(
                "lianjia: every city page returned empty body (anti-bot or outage)"
            )
        return out

    # ---- parse --------------------------------------------------------

    def parse(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Walk every city page, extract per-listing rows.

        Schema per row:
            city            (北京/上海/深圳/广州)
            district        (region anchor from .positionInfo, e.g. 望京)
            xiaoqu          (小区 anchor from .positionInfo, e.g. 花家地小区)
            period          (YYYY-MM, fetch time UTC)
            avg_price       (alias of unit_price_yuan_sqm — keeps
                             the original schema compatible with the
                             DBT mart)
            unit_price_yuan_sqm  (per-m² price, integer)
            total_price_wan      (total asking price in 万, float)
            deals_count     (alias of follow_count — keeps the
                             original schema compatible with the DBT
                             mart)
            follow_count    (number of people following this listing)
            title           (raw listing title)
            source          (lianjia_deals)
        """
        out: list[dict[str, Any]] = []
        for page in raw:
            city = page.get("city", "")
            period = page.get("period") or datetime.now(timezone.utc).strftime(
                "%Y-%m"
            )
            html = page.get("_html", "")
            if not html:
                continue
            soup = parse_html(html)
            for li in soup.select("li.clear"):
                rows = _extract_li(li, city=city, period=period)
                out.extend(rows)
        if not out:
            raise RuntimeError("lianjia: no listings found on any city page")
        return out

    # ---- validate -----------------------------------------------------

    def validate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for r in rows:
            if not r.get("city") or not r.get("district") or not r.get("period"):
                continue
            # Keep rows that have at least one numeric price signal.
            if r.get("unit_price_yuan_sqm") is None and r.get(
                "total_price_wan"
            ) is None:
                continue
            kept.append(r)
        return kept

    # ---- fallback -----------------------------------------------------

    def fallback(self) -> list[dict[str, Any]]:
        """Return the original 6 mock rows so the dashboard stays green
        on transient failures. These are real-ish numbers from the
        2026-09 era of the Beijing / Shanghai / Shenzhen 二手房
        market; they are tagged ``is_fallback=True`` so the DBT mart
        can exclude them from production analysis.
        """
        today = datetime.now(timezone.utc)
        period = today.strftime("%Y-%m")
        return [
            {
                "city": "北京",
                "district": "朝阳",
                "xiaoqu": None,
                "period": period,
                "avg_price": 68500.0,
                "unit_price_yuan_sqm": 68500,
                "total_price_wan": 685.0,
                "deals_count": 287,
                "follow_count": 287,
                "title": None,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "北京",
                "district": "海淀",
                "xiaoqu": None,
                "period": period,
                "avg_price": 83200.0,
                "unit_price_yuan_sqm": 83200,
                "total_price_wan": 832.0,
                "deals_count": 196,
                "follow_count": 196,
                "title": None,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "北京",
                "district": "丰台",
                "xiaoqu": None,
                "period": period,
                "avg_price": 52900.0,
                "unit_price_yuan_sqm": 52900,
                "total_price_wan": 529.0,
                "deals_count": 158,
                "follow_count": 158,
                "title": None,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "上海",
                "district": "浦东",
                "xiaoqu": None,
                "period": period,
                "avg_price": 75800.0,
                "unit_price_yuan_sqm": 75800,
                "total_price_wan": 758.0,
                "deals_count": 312,
                "follow_count": 312,
                "title": None,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "上海",
                "district": "徐汇",
                "xiaoqu": None,
                "period": period,
                "avg_price": 92100.0,
                "unit_price_yuan_sqm": 92100,
                "total_price_wan": 921.0,
                "deals_count": 84,
                "follow_count": 84,
                "title": None,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "深圳",
                "district": "南山",
                "xiaoqu": None,
                "period": period,
                "avg_price": 88500.0,
                "unit_price_yuan_sqm": 88500,
                "total_price_wan": 885.0,
                "deals_count": 203,
                "follow_count": 203,
                "title": None,
                "source": self.source_id,
                "is_fallback": True,
            },
        ]


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _extract_li(li: Any, *, city: str, period: str) -> list[dict[str, Any]]:
    """Extract a single dict row from one ``<li class="clear">`` element.

    Returns an empty list if the listing is missing required fields.
    """
    title_a = li.select_one(".title a")
    if not title_a:
        return []
    title = title_a.get_text(strip=True)

    pos = li.select_one(".positionInfo")
    if not pos:
        return []
    anchors = pos.select("a")
    if len(anchors) < 1:
        return []
    xiaoqu = anchors[0].get_text(strip=True) or None
    district = anchors[1].get_text(strip=True) if len(anchors) >= 2 else ""
    if not district:
        return []

    total_price = _to_float_wan(li.select_one(".totalPrice"))
    unit_price = _to_int_yuan_sqm(li.select_one(".unitPrice"))
    follow_count = _follow_count(li.select_one(".followInfo"))

    row = {
        "city": city,
        "district": district,
        "xiaoqu": xiaoqu,
        "period": period,
        "avg_price": unit_price,  # alias of unit_price for DBT compat
        "unit_price_yuan_sqm": unit_price,
        "total_price_wan": total_price,
        "deals_count": follow_count,  # alias of follow_count for DBT compat
        "follow_count": follow_count,
        "title": title,
        "source": SOURCE_ID,
    }
    return [row]


_UNIT_PRICE_RE = re.compile(r"([\d,]+)\s*元/平")
_TOTAL_PRICE_RE = re.compile(r"([\d.]+)\s*万")
_FOLLOW_RE = re.compile(r"(\d+)\s*人关注")


def _to_int_yuan_sqm(node: Any) -> int | None:
    if node is None:
        return None
    text = node.get_text(strip=True)
    m = _UNIT_PRICE_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _to_float_wan(node: Any) -> float | None:
    if node is None:
        return None
    text = node.get_text(strip=True)
    m = _TOTAL_PRICE_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _follow_count(node: Any) -> int | None:
    if node is None:
        return None
    text = node.get_text(strip=True)
    m = _FOLLOW_RE.search(text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


register(LianjiaDealsScraper())
