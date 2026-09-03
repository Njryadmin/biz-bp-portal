"""
business_lines/retail-leasing/api/router.py

FastAPI router for the 零售租赁与市场报告 (retail-leasing) business line.

Endpoints (mounted at /api/lines/retail-leasing):
    GET /indicators        -> 8 indicator definitions
    GET /properties        -> mock retail properties with deal-level data
    GET /market-benchmark  -> comparable benchmark rents per property
    GET /vacancy-alerts    -> owner-level vacancy risk alerts

All data is loaded once at module import from data/seed/properties.json.
This is a mock / demo implementation; in production these would hit a warehouse
(mart_retail_leasing.fct_retail_leasing).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

# ---------------------------------------------------------------------------
# Seed data loader (idempotent, runs once at import)
# ---------------------------------------------------------------------------

_SEED_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "seed"
    / "properties.json"
)


def _load_seed() -> list[dict[str, Any]]:
    """Load retail-leasing seed properties with headline fields.

    Each property is enriched with a derived `headline_kpis` block so the Web
    layer can render UniversalKpiCards without re-running calculations.
    """
    if not _SEED_PATH.exists():
        return []
    raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    enriched: list[dict[str, Any]] = []
    for p in raw:
        gla_sqm = float(p["gla_sqm"])
        deal_rent = float(p["deal_rent_yuan_per_sqm_per_month"])
        benchmark = float(p["benchmark_rent_yuan_per_sqm_per_month"])
        # 基准对标差 = (成交 - 基准) / 基准
        benchmark_gap = (deal_rent - benchmark) / benchmark if benchmark > 0 else 0.0
        # 推断出租率: 1 - vacancy_rate
        occupancy = 1.0 - float(p["vacancy_rate"])
        enriched.append(
            {
                "property_id": p["property_id"],
                "name": p["name"],
                "city": p["city"],
                "city_tier": p.get("city_tier", ""),
                "area_district": p.get("area_district", ""),
                "gla_sqm": gla_sqm,
                "deal_rent_yuan_per_sqm_per_month": deal_rent,
                "benchmark_rent_yuan_per_sqm_per_month": benchmark,
                "vacancy_rate": float(p["vacancy_rate"]),
                "owner": p["owner"],
                "tenant": p.get("tenant", ""),
                "owner_vacancy_days": int(p["owner_vacancy_days"]),
                "quarterly_reports_published": int(p.get("quarterly_reports_published", 0)),
                "brand_entry_rate": float(p.get("brand_entry_rate", 0.0)),
                "renewal_rate": float(p.get("renewal_rate", 0.0)),
                "commission_revenue_wan": float(p.get("commission_revenue_wan", 0.0)),
                "headline_kpis": {
                    "occupancy_rate": round(occupancy, 4),
                    "avg_deal_rent": deal_rent,
                    "benchmark_gap_pct": round(benchmark_gap, 4),
                    "owner_vacancy_days": int(p["owner_vacancy_days"]),
                    "quarterly_market_reports": int(
                        p.get("quarterly_reports_published", 0)
                    ),
                    "brand_entry_rate": float(p.get("brand_entry_rate", 0.0)),
                    "renewal_rate": float(p.get("renewal_rate", 0.0)),
                    "commission_revenue": float(
                        p.get("commission_revenue_wan", 0.0)
                    ),
                },
                "comparables": p.get("comparables", []),
            }
        )
    return enriched


PROPERTIES: list[dict[str, Any]] = _load_seed()
PROPERTY_INDEX: dict[str, dict[str, Any]] = {
    p["property_id"]: p for p in PROPERTIES
}


# ---------------------------------------------------------------------------
# Indicator catalog (mirrors indicators.yaml; embedded for fast lookup)
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {
        "id": "occupancy_rate",
        "title": "商铺出租率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "已出租商铺面积占总可租面积的比例;反映租赁市场吸纳能力。",
    },
    {
        "id": "avg_deal_rent",
        "title": "平均成交租金",
        "unit": "元/㎡/月",
        "format": "currency",
        "aggregation": "avg",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "样本期内新签租约的月租金加权均值(元/㎡/月)。",
    },
    {
        "id": "benchmark_gap_pct",
        "title": "竞品基准租金对标差",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "实际成交租金相对同地段竞品基准的偏差;正=高于基准,负=低于基准。",
    },
    {
        "id": "owner_vacancy_days",
        "title": "业主空置期",
        "unit": "天",
        "format": "number",
        "aggregation": "avg",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "业主从上一个租约结束到新租约签约之间的空置天数。",
    },
    {
        "id": "quarterly_market_reports",
        "title": "季度市场报告",
        "unit": "份",
        "format": "number",
        "aggregation": "sum",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "当期发布的零售租赁季度市场报告数。",
    },
    {
        "id": "brand_entry_rate",
        "title": "品牌入驻率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "新签租约中品牌客户占比,反映商业体招商吸引力。",
    },
    {
        "id": "renewal_rate",
        "title": "续约率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "到期租约中选择续约的比例,反映租户满意度与物业粘性。",
    },
    {
        "id": "commission_revenue",
        "title": "佣金收入",
        "unit": "万元",
        "format": "currency",
        "aggregation": "sum",
        "source": "mart_retail_leasing.fct_retail_leasing",
        "description": "本期租赁交易撮合产生的佣金收入合计(万元)。",
    },
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    """Health endpoint."""
    return {
        "status": "ok",
        "line": "retail-leasing",
        "properties_loaded": len(PROPERTIES),
    }


def _require_property(property_id: str) -> dict[str, Any]:
    if property_id not in PROPERTY_INDEX:
        raise HTTPException(
            status_code=404, detail=f"unknown property_id: {property_id}"
        )
    return PROPERTY_INDEX[property_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    """Return the indicator catalog for this line."""
    return {
        "line_id": "retail-leasing",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/properties")
async def list_properties(
    city: str | None = Query(default=None, description="按城市过滤,例如 上海/北京"),
    owner: str | None = Query(default=None, description="按业主过滤"),
) -> dict[str, Any]:
    """List all retail-leasing properties with deal-level headline KPIs.

    Optional filters: ?city=上海  ?owner=xxx
    """
    items: list[dict[str, Any]] = []
    for p in PROPERTIES:
        if city and p["city"] != city:
            continue
        if owner and p["owner"] != owner:
            continue
        # Strip the comparables (returned by /market-benchmark) from the public
        # response to keep this endpoint lightweight.
        items.append({k: v for k, v in p.items() if k != "comparables"})
    return {
        "line_id": "retail-leasing",
        "count": len(items),
        "items": items,
    }


@router.get("/market-benchmark")
async def market_benchmark(
    property_id: str | None = Query(
        default=None,
        description="可选: 限定单个物业,否则返回所有物业的基准对标",
    ),
) -> dict[str, Any]:
    """Return comparable benchmark rents per property (or single property).

    Each item includes the property, its own deal rent, the median of its
    3-5 comparables, and the per-comparable breakdown. The benchmark_gap_pct
    field is the headline deviation.
    """
    items: list[dict[str, Any]] = []
    for p in PROPERTIES:
        if property_id and p["property_id"] != property_id:
            continue
        comparables = p.get("comparables", [])
        comp_rents = [float(c["rent_yuan_per_sqm_per_month"]) for c in comparables]
        median_benchmark = (
            sorted(comp_rents)[len(comp_rents) // 2] if comp_rents else 0.0
        )
        items.append(
            {
                "property_id": p["property_id"],
                "property_name": p["name"],
                "city": p["city"],
                "deal_rent": p["deal_rent_yuan_per_sqm_per_month"],
                "internal_benchmark": p[
                    "benchmark_rent_yuan_per_sqm_per_month"
                ],
                "comparable_median": median_benchmark,
                "benchmark_gap_pct": p["headline_kpis"]["benchmark_gap_pct"],
                "comparable_count": len(comparables),
                "comparables": comparables,
            }
        )
    return {
        "line_id": "retail-leasing",
        "property_id": property_id,
        "count": len(items),
        "items": items,
        "as_of": "2025-Q4",
    }


@router.get("/vacancy-alerts")
async def vacancy_alerts(
    threshold_days: int = Query(
        default=60,
        ge=0,
        le=365,
        description="空置期超过此天数的物业会被预警",
    ),
) -> dict[str, Any]:
    """Return owner-level vacancy risk alerts.

    Aggregates per-owner the worst vacancy event and the most recent deal.
    Owners with `owner_vacancy_days > threshold_days` are flagged.
    """
    by_owner: dict[str, dict[str, Any]] = {}
    for p in PROPERTIES:
        owner = p["owner"]
        slot = by_owner.setdefault(
            owner,
            {
                "owner": owner,
                "properties": [],
                "max_vacancy_days": 0,
                "worst_property": None,
            },
        )
        slot["properties"].append(
            {
                "property_id": p["property_id"],
                "property_name": p["name"],
                "city": p["city"],
                "owner_vacancy_days": p["owner_vacancy_days"],
                "deal_rent": p["deal_rent_yuan_per_sqm_per_month"],
            }
        )
        if p["owner_vacancy_days"] > slot["max_vacancy_days"]:
            slot["max_vacancy_days"] = p["owner_vacancy_days"]
            slot["worst_property"] = p["name"]

    alerts: list[dict[str, Any]] = []
    for owner, slot in by_owner.items():
        severity = (
            "high"
            if slot["max_vacancy_days"] >= threshold_days * 1.5
            else "medium"
            if slot["max_vacancy_days"] >= threshold_days
            else "low"
        )
        if slot["max_vacancy_days"] >= threshold_days:
            alerts.append(
                {
                    "owner": owner,
                    "severity": severity,
                    "max_vacancy_days": slot["max_vacancy_days"],
                    "worst_property": slot["worst_property"],
                    "property_count": len(slot["properties"]),
                    "properties": slot["properties"],
                }
            )

    # Highest vacancy first
    alerts.sort(key=lambda a: a["max_vacancy_days"], reverse=True)
    return {
        "line_id": "retail-leasing",
        "threshold_days": threshold_days,
        "alert_count": len(alerts),
        "alerts": alerts,
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/properties/{property_id}")
async def get_property(property_id: str) -> dict[str, Any]:
    """Return a single property with all enriched fields (including comparables)."""
    p = _require_property(property_id)
    return {
        "line_id": "retail-leasing",
        "property": p,
    }
