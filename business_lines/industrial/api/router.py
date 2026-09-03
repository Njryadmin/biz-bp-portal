"""
business_lines/industrial/api/router.py

FastAPI router for the 工业地产部 (industrial) business line.

Endpoints (mounted at /api/lines/industrial):
    GET /ping                            -> health
    GET /indicators                      -> 10 indicator definitions
    GET /properties                      -> 7 mock properties + headline KPIs
    GET /properties/{id}/occupancy       -> occupancy detail + tenant mix
    GET /tenants/industry-mix            -> cross-property tenant industry distribution
    GET /key-clients                     -> top key clients by area

All data is loaded once at module import from data/seed/industrial_properties.json.
"""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "industrial_properties.json"


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        return []
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _enrich(p: dict[str, Any]) -> dict[str, Any]:
    total = float(p["total_area_sqm"])
    leased = float(p["leased_area_sqm"])
    occ = leased / total if total > 0 else 0
    tenants = p.get("tenants", [])
    # Aggregate stats
    new_key_count = sum(1 for t in tenants if t.get("is_key_client") and t.get("is_new"))
    avg_term = (
        sum(t.get("term_years", 0) for t in tenants) / len(tenants) if tenants else 0
    )
    return {
        **p,
        "occupancy_rate": round(occ, 4),
        "new_key_clients": new_key_count,
        "avg_lease_term": round(avg_term, 1),
    }


PROPS_RAW: list[dict[str, Any]] = _load_seed()
PROPS: list[dict[str, Any]] = [_enrich(p) for p in PROPS_RAW]
PROP_INDEX: dict[str, dict[str, Any]] = {p["property_id"]: p for p in PROPS}


# ---------------------------------------------------------------------------
# Indicator catalog
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {"id": "deal_area", "title": "厂房/仓库成交面积", "unit": "㎡", "format": "number", "aggregation": "sum",
     "source": "mart_industrial.mart_property_kpis", "description": "当期成交面积。"},
    {"id": "occupancy_rate", "title": "出租率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "已租/总可租。"},
    {"id": "avg_rent", "title": "平均租金", "unit": "元/㎡/月", "format": "currency", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "按面积加权月租金。"},
    {"id": "tenant_industry_diversity", "title": "租户行业多样性", "unit": "0-1", "format": "ratio", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "Shannon 熵归一化。"},
    {"id": "new_key_clients", "title": "新增大客户数", "unit": "个", "format": "number", "aggregation": "sum",
     "source": "mart_industrial.mart_property_kpis", "description": "新签 >5000㎡ 客户数。"},
    {"id": "lease_renewal_rate", "title": "续租率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "续租比例。"},
    {"id": "avg_lease_term", "title": "平均租期", "unit": "年", "format": "ratio", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "新签平均年限。"},
    {"id": "warehouse_count", "title": "在管物业数", "unit": "个", "format": "number", "aggregation": "sum",
     "source": "mart_industrial.mart_property_kpis", "description": "在管物业数。"},
    {"id": "logistics_park_coverage", "title": "物流园覆盖度", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "已入驻物流园的比例。"},
    {"id": "cap_rate", "title": "资本化率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_industrial.mart_property_kpis", "description": "NOI/资产估值。"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "line": "industrial", "properties_loaded": len(PROPS)}


def _require_property(property_id: str) -> dict[str, Any]:
    if property_id not in PROP_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown property_id: {property_id}")
    return PROP_INDEX[property_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    return {
        "line_id": "industrial",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/properties")
async def list_properties(
    property_type: str | None = Query(default=None, description="仓库/厂房/冷库"),
    city: str | None = Query(default=None),
) -> dict[str, Any]:
    items = []
    for p in PROPS:
        if property_type and p["property_type"] != property_type:
            continue
        if city and p["city"] != city:
            continue
        items.append({k: v for k, v in p.items() if k != "tenants"})
    return {"line_id": "industrial", "count": len(items), "items": items}


@router.get("/properties/{property_id}/occupancy")
async def property_occupancy(property_id: str) -> dict[str, Any]:
    """Occupancy detail + tenant breakdown + industry mix."""
    p = _require_property(property_id)
    tenants = p.get("tenants", [])
    by_industry: Counter = Counter()
    for t in tenants:
        by_industry[t.get("industry", "其他")] += t.get("area_sqm", 0)
    total = sum(by_industry.values()) or 1
    industry_items = [
        {"industry": k, "area_sqm": v, "share": round(v / total, 4)}
        for k, v in by_industry.most_common()
    ]
    n = len(industry_items)
    if n > 1:
        h = -sum(it["share"] * math.log(it["share"]) for it in industry_items if it["share"] > 0)
        h_max = math.log(n)
        diversity = h / h_max
    else:
        diversity = 0.0
    occ_band = (
        "excellent" if p["occupancy_rate"] >= 0.90 else
        "good" if p["occupancy_rate"] >= 0.80 else
        "warning" if p["occupancy_rate"] >= 0.70 else "critical"
    )
    return {
        "property_id": property_id,
        "property_name": p["property_name"],
        "property_type": p["property_type"],
        "city": p["city"],
        "total_area_sqm": p["total_area_sqm"],
        "leased_area_sqm": p["leased_area_sqm"],
        "occupancy_rate": p["occupancy_rate"],
        "occupancy_band": occ_band,
        "avg_rent_yuan_per_sqm_per_month": p["avg_rent_yuan_per_sqm_per_month"],
        "tenant_count": p["tenant_count"],
        "new_key_clients": p["new_key_clients"],
        "avg_lease_term": p["avg_lease_term"],
        "cap_rate": p["cap_rate"],
        "is_in_logistics_park": p["is_in_logistics_park"],
        "industry_breakdown": industry_items,
        "industry_diversity_index": round(diversity, 4),
        "renewal_rate_12m": p["renewal_rate_12m"],
    }


@router.get("/tenants/industry-mix")
async def tenant_industry_mix() -> dict[str, Any]:
    """Cross-property tenant industry distribution."""
    by_ind: dict[str, dict[str, Any]] = defaultdict(lambda: {"area_sqm": 0, "tenant_count": 0})
    for p in PROPS:
        for t in p.get("tenants", []):
            ind = t.get("industry", "其他")
            by_ind[ind]["area_sqm"] += t.get("area_sqm", 0)
            by_ind[ind]["tenant_count"] += 1
    total = sum(v["area_sqm"] for v in by_ind.values()) or 1
    items = [
        {"industry": k, "area_sqm": v["area_sqm"], "tenant_count": v["tenant_count"],
         "share": round(v["area_sqm"] / total, 4)}
        for k, v in sorted(by_ind.items(), key=lambda x: x[1]["area_sqm"], reverse=True)
    ]
    n = len(items)
    if n > 1:
        h = -sum(it["share"] * math.log(it["share"]) for it in items if it["share"] > 0)
        h_max = math.log(n)
        diversity = h / h_max
    else:
        diversity = 0.0
    return {
        "line_id": "industrial",
        "industries": items,
        "diversity_index": round(diversity, 4),
        "total_industry_count": n,
    }


@router.get("/key-clients")
async def key_clients() -> dict[str, Any]:
    """Top key clients across all properties (by area)."""
    flat: list[dict[str, Any]] = []
    for p in PROPS:
        for t in p.get("tenants", []):
            if t.get("is_key_client"):
                flat.append({
                    "tenant_id": t.get("tenant_id"),
                    "tenant_name": t.get("tenant_name"),
                    "industry": t.get("industry"),
                    "property_name": p["property_name"],
                    "city": p["city"],
                    "area_sqm": t.get("area_sqm"),
                    "monthly_rent_yuan_per_sqm": t.get("monthly_rent_yuan_per_sqm"),
                    "term_years": t.get("term_years"),
                    "is_new": t.get("is_new", False),
                })
    flat.sort(key=lambda x: x["area_sqm"], reverse=True)
    return {"line_id": "industrial", "count": len(flat), "items": flat[:15]}
