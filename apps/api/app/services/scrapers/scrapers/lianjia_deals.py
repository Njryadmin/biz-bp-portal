"""
apps/api/app/services/scrapers/scrapers/lianjia_deals.py

Lianjia public deal/transaction statistics (链家成交数据).

NOTE: Lianjia actively anti-scrapes (per-IP rate limits, JS-rendered
pages, and captcha walls). This scraper is intentionally written
defensively:

* It only attempts ONE call per ``run()`` with a short timeout.
* On any HTTP error, non-2xx, or HTML that doesn't look like a real
  stats page, it raises — the framework's fallback hook takes over
  and the pipeline keeps going with mock data.
* The mock data is realistic enough for the rest of the system
  (DBT marts, frontend charts) to render meaningful output.

If you want REAL data, sign the commercial-data agreement with
Ke (the operator) and swap the fallback with a paid-API adapter.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..base import BaseScraper
from ..registry import register
from ..utils import http_get, rate_limit_check, retry_with_backoff
from ._html import parse_html, table_to_dicts


SOURCE_ID = "lianjia_deals"
LIANJIA_INDEX = "https://bj.lianjia.com/chengjiao/"
LIANJIA_DOMAIN = "lianjia.com"


class LianjiaDealsScraper(BaseScraper):
    source_id = SOURCE_ID
    name = "链家城市成交数据 (公开页面)"
    schedule = "0 10 * * 1"
    enabled = True
    required_fields = ("city", "district", "period")

    async def fetch(self) -> list[dict[str, Any]]:
        if not rate_limit_check(LIANJIA_DOMAIN, max_per_minute=2):
            raise RuntimeError(f"rate-limit hit for {LIANJIA_DOMAIN}; skipping live fetch")

        # Lianjia uses per-city hosts (bj.lianjia.com, sh.lianjia.com, ...).
        # Try Beijing; if that fails, raise and the framework falls back.
        @retry_with_backoff(max_retries=1, base_delay=2.0)
        def _get(url: str) -> str:
            r = http_get(url, timeout=10, follow_redirects=False)
            # Lianjia commonly returns 403 to non-browser UAs. Treat as
            # an unrecoverable error and let the fallback kick in.
            if r.status_code in (403, 429, 503):
                raise RuntimeError(f"lianjia anti-bot {r.status_code} for {url}")
            r.raise_for_status()
            return r.text

        html = _get(LIANJIA_INDEX)
        return [{"_url": LIANJIA_INDEX, "_html": html}]

    def parse(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not raw:
            return []
        page = raw[0]
        html = page.get("_html", "")
        if not html:
            return []
        soup = parse_html(html)

        period = datetime.now(timezone.utc).strftime("%Y-%m")
        out: list[dict[str, Any]] = []

        # Lianjia's chengjiao (成交) page exposes a per-district table on
        # some cities. We try the standard selector first.
        for tbl in soup.select("table"):
            rows = table_to_dicts(tbl)
            for r in rows:
                district = (
                    r.get("商圈")
                    or r.get("区域")
                    or r.get("district")
                    or r.get("小区")
                    or ""
                )
                deals = r.get("成交量") or r.get("成交数") or r.get("deals_count")
                price = r.get("均价") or r.get("成交均价") or r.get("avg_price")
                if not district:
                    continue
                out.append(
                    {
                        "city": "北京",
                        "district": str(district).strip(),
                        "period": period,
                        "avg_price": _to_float_yuan(price),
                        "deals_count": _to_int(deals),
                        "source": self.source_id,
                    }
                )
        # If the page didn't have a recognisable table, raise so the
        # fallback fires.
        if not out:
            raise RuntimeError("lianjia: no recognisable deal table in page")
        return out

    def validate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for r in rows:
            if not r.get("city") or not r.get("district") or not r.get("period"):
                continue
            if r.get("avg_price") is None and r.get("deals_count") is None:
                continue
            kept.append(r)
        return kept

    def fallback(self) -> list[dict[str, Any]]:
        today = datetime.now(timezone.utc)
        period = today.strftime("%Y-%m")
        return [
            {
                "city": "北京",
                "district": "朝阳",
                "period": period,
                "avg_price": 68500.0,
                "deals_count": 287,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "北京",
                "district": "海淀",
                "period": period,
                "avg_price": 83200.0,
                "deals_count": 196,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "北京",
                "district": "丰台",
                "period": period,
                "avg_price": 52900.0,
                "deals_count": 158,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "上海",
                "district": "浦东",
                "period": period,
                "avg_price": 75800.0,
                "deals_count": 312,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "上海",
                "district": "徐汇",
                "period": period,
                "avg_price": 92100.0,
                "deals_count": 84,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "深圳",
                "district": "南山",
                "period": period,
                "avg_price": 88500.0,
                "deals_count": 203,
                "source": self.source_id,
                "is_fallback": True,
            },
        ]


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _to_float_yuan(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").replace("元", "").replace(" ", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


register(LianjiaDealsScraper())
