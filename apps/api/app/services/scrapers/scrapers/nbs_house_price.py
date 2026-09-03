"""
apps/api/app/services/scrapers/scrapers/nbs_house_price.py

National Bureau of Statistics 70-city house-price index (国家统计局
70个大中城市新建商品住宅/二手住宅价格指数).

Source: http://www.stats.gov.cn (the actual monthly URL rotates
monthly; we hit the home page and search for the latest issue).

Real-fetch strategy:
    1. GET the NBS news index page.
    2. Look for a link whose text contains "70个大中城市" and a year
       marker. Click it.
    3. Parse the table inside the page (新建商品住宅 + 二手住宅).

Fallback (the realistic case from inside a CI box):
    * The site blocks foreign / unknown UAs, returns 403, or its
      HTML changes month to month. We hand-roll ~3 cities for the
      current month so the pipeline still has data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..base import BaseScraper
from ..registry import register
from ..utils import http_get, rate_limit_check, retry_with_backoff
from ._html import parse_html, table_to_dicts


SOURCE_ID = "nbs_house_price"
NBS_INDEX = "http://www.stats.gov.cn/sj/zxfb/"
NBS_DOMAIN = "stats.gov.cn"


class NbsHousePriceScraper(BaseScraper):
    source_id = SOURCE_ID
    name = "国家统计局 70 城房价指数"
    schedule = "0 9 16 * *"
    enabled = True
    required_fields = ("city", "period")

    # ---- fetch --------------------------------------------------------

    async def fetch(self) -> list[dict[str, Any]]:
        if not rate_limit_check(NBS_DOMAIN, max_per_minute=4):
            raise RuntimeError(f"rate-limit hit for {NBS_DOMAIN}; skipping live fetch")

        # Try the index first.
        @retry_with_backoff(max_retries=2, base_delay=1.0)
        def _get_index() -> str:
            r = http_get(NBS_INDEX, timeout=15)
            r.raise_for_status()
            return r.text

        index_html = _get_index()
        soup = parse_html(index_html)
        link = None
        for a in soup.find_all("a"):
            text = a.get_text(strip=True)
            href = a.get("href", "")
            if "70个大中城市" in text and ("住宅" in text or "房价" in text):
                link = href
                break

        if not link:
            raise RuntimeError("could not find latest 70-city report link on NBS index")

        # Resolve to absolute URL.
        if link.startswith("/"):
            url = "http://www.stats.gov.cn" + link
        elif link.startswith("http"):
            url = link
        else:
            url = NBS_INDEX.rstrip("/") + "/" + link.lstrip("/")

        @retry_with_backoff(max_retries=2, base_delay=1.0)
        def _get_page() -> str:
            r = http_get(url, timeout=20)
            r.raise_for_status()
            return r.text

        page_html = _get_page()
        return [{"_url": url, "_html": page_html}]

    # ---- parse --------------------------------------------------------

    def parse(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not raw:
            return out
        page = raw[0]
        html = page.get("_html", "")
        if not html:
            return out
        soup = parse_html(html)

        # The NBS page typically has 2 tables:
        #   1. 新建商品住宅价格指数 (new homes)
        #   2. 二手住宅价格指数 (second-hand)
        tables = soup.find_all("table")
        if not tables:
            return out

        period = _infer_period(html)

        # Walk tables and try to match by surrounding text.
        for tbl in tables:
            preceding_text = ""
            prev = tbl.find_previous(["p", "div", "h2", "h3"])
            if prev is not None:
                preceding_text = prev.get_text(strip=True)
            tbl_lower = preceding_text + " " + tbl.get_text()[:200]
            is_new_home = "新建" in tbl_lower or "新建商品" in tbl_lower
            is_second = "二手" in tbl_lower
            if not (is_new_home or is_second):
                # Default: treat first table as new-home, second as second-hand.
                is_new_home = tbl is tables[0]
                is_second = tbl is tables[-1] and len(tables) > 1 and not is_new_home

            rows = table_to_dicts(tbl)
            for r in rows:
                city = r.get("城市") or r.get("city") or next(iter(r.values()), "")
                if not city or city in ("城市", "City", ""):
                    continue
                record = {
                    "city": str(city).strip(),
                    "period": period,
                    "new_home_index_yoy": _to_float(r.get("同比")),
                    "new_home_index_mom": _to_float(r.get("环比")),
                    "second_hand_index_yoy": _to_float(r.get("二手同比") or r.get("二手住宅同比")),
                    "second_hand_index_mom": _to_float(r.get("二手环比") or r.get("二手住宅环比")),
                    "source": self.source_id,
                }
                # If this table is the second-hand one, swap columns.
                if is_second and not is_new_home:
                    record["new_home_index_yoy"] = None
                    record["new_home_index_mom"] = None
                    record["second_hand_index_yoy"] = _to_float(r.get("同比"))
                    record["second_hand_index_mom"] = _to_float(r.get("环比"))
                elif is_new_home:
                    record["second_hand_index_yoy"] = None
                    record["second_hand_index_mom"] = None
                out.append(record)
        return out

    # ---- validate -----------------------------------------------------

    def validate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        kept: list[dict[str, Any]] = []
        for r in rows:
            city = r.get("city")
            period = r.get("period")
            if not city or not period:
                continue
            # At least one index must be numeric.
            has_value = any(
                isinstance(r.get(k), (int, float))
                for k in (
                    "new_home_index_yoy",
                    "new_home_index_mom",
                    "second_hand_index_yoy",
                    "second_hand_index_mom",
                )
            )
            if not has_value:
                continue
            kept.append(r)
        return kept

    # ---- fallback -----------------------------------------------------

    def fallback(self) -> list[dict[str, Any]]:
        """Mock 3 cities for the current month when NBS is unreachable."""
        today = datetime.now(timezone.utc)
        period = today.strftime("%Y-%m")
        return [
            {
                "city": "北京",
                "period": period,
                "new_home_index_yoy": -1.2,
                "new_home_index_mom": -0.3,
                "second_hand_index_yoy": -4.5,
                "second_hand_index_mom": -0.7,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "上海",
                "period": period,
                "new_home_index_yoy": 2.1,
                "new_home_index_mom": 0.4,
                "second_hand_index_yoy": -2.3,
                "second_hand_index_mom": -0.2,
                "source": self.source_id,
                "is_fallback": True,
            },
            {
                "city": "深圳",
                "period": period,
                "new_home_index_yoy": -0.8,
                "new_home_index_mom": -0.1,
                "second_hand_index_yoy": -3.1,
                "second_hand_index_mom": -0.5,
                "source": self.source_id,
                "is_fallback": True,
            },
        ]


# ─────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s or s in {"—", "-", "－", "N/A"}:
        return None
    s = s.replace("%", "").replace("％", "")
    try:
        return float(s)
    except ValueError:
        return None


def _infer_period(html: str) -> str:
    """Extract ``YYYY-MM`` from the page; default to current UTC month."""
    import re

    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", html)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return datetime.now(timezone.utc).strftime("%Y-%m")


# Register at import time so auto-discovery picks us up.
register(NbsHousePriceScraper())
