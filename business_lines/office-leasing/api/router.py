"""
business_lines/office-leasing/api/router.py

FastAPI router for the 写字楼租赁部 (office-leasing) business line.

Endpoints (mounted at /api/lines/office-leasing):
    GET /ping                       -> health
    GET /indicators                 -> 10 indicator definitions
    GET /deals                      -> 8 mock deals + headline KPIs
    GET /deals/{id}/economics       -> rent, commission, payback detail
    GET /buildings                  -> building pool + vacancy
    GET /brokers                    -> per-broker productivity

All data is loaded once at module import from data/seed/office_deals.json.
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

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "office_deals.json"


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        return []
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _enrich(d: dict[str, Any]) -> dict[str, Any]:
    area = float(d["area_sqm"])
    rent = float(d["monthly_rent_yuan_per_sqm"])
    rate = float(d["commission_rate"])  # in month-rent
    term = int(d["lease_term_years"])
    # Commission = monthly_rent_yuan * months * commission_rate
    monthly_total = area * rent
    commission_yuan = monthly_total * 12 * term * rate
    # Annual rent yuan
    annual_rent_yuan = area * rent * 12
    return {
        **d,
        "monthly_rent_total_yuan": monthly_total,
        "annual_rent_yuan": annual_rent_yuan,
        "commission_yuan": round(commission_yuan, 0),
        "commission_wan": round(commission_yuan / 10000, 1),
        "total_revenue_yuan": round(annual_rent_yuan * term, 0),
    }


DEALS_RAW: list[dict[str, Any]] = _load_seed()
DEALS: list[dict[str, Any]] = [_enrich(d) for d in DEALS_RAW]
DEAL_INDEX: dict[str, dict[str, Any]] = {d["deal_id"]: d for d in DEALS}


# ---------------------------------------------------------------------------
# Indicator catalog
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {"id": "deal_area", "title": "成交面积", "unit": "㎡", "format": "number", "aggregation": "sum",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "当期成交的可租面积合计。"},
    {"id": "commission_revenue", "title": "佣金收入", "unit": "万元", "format": "currency", "aggregation": "sum",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "当期佣金合计。"},
    {"id": "avg_commission_rate", "title": "平均佣金费率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "佣金/月租。"},
    {"id": "avg_deal_cycle", "title": "平均成交周期", "unit": "天", "format": "number", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "委托到签约的平均天数。"},
    {"id": "client_mix", "title": "客户结构多样性", "unit": "0-1", "format": "ratio", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "Shannon 熵归一化。"},
    {"id": "renewal_rate", "title": "续约率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "到期租约续约比例。"},
    {"id": "cross_region_ratio", "title": "跨区成交占比", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "跨区成交比例。"},
    {"id": "broker_count", "title": "经纪人人数", "unit": "人", "format": "number", "aggregation": "sum",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "活跃经纪人人数。"},
    {"id": "per_broker_output", "title": "人均产能", "unit": "万元/人/月", "format": "currency", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "经纪人月人均佣金。"},
    {"id": "vacancy_rate", "title": "市场空置率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_office_leasing.mart_deal_kpis", "description": "重点楼宇市场空置率。"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "line": "office-leasing", "deals_loaded": len(DEALS)}


def _require_deal(deal_id: str) -> dict[str, Any]:
    if deal_id not in DEAL_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown deal_id: {deal_id}")
    return DEAL_INDEX[deal_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    return {
        "line_id": "office-leasing",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/deals")
async def list_deals(
    industry: str | None = Query(default=None, description="金融/科技/专业服务/..."),
    grade: str | None = Query(default=None, description="甲级/乙级/丙级"),
    broker: str | None = Query(default=None),
) -> dict[str, Any]:
    items = []
    for d in DEALS:
        if industry and d["tenant_industry"] != industry:
            continue
        if grade and d["building_grade"] != grade:
            continue
        if broker and d["broker"] != broker:
            continue
        items.append(d)
    return {"line_id": "office-leasing", "count": len(items), "items": items}


@router.get("/deals/{deal_id}/economics")
async def deal_economics(deal_id: str) -> dict[str, Any]:
    """Per-deal rent, commission, payback detail."""
    d = _require_deal(deal_id)
    return {
        "deal_id": deal_id,
        "building_name": d["building_name"],
        "tenant_name": d["tenant_name"],
        "area_sqm": d["area_sqm"],
        "monthly_rent_yuan_per_sqm": d["monthly_rent_yuan_per_sqm"],
        "monthly_rent_total_yuan": d["monthly_rent_total_yuan"],
        "annual_rent_yuan": d["annual_rent_yuan"],
        "lease_term_years": d["lease_term_years"],
        "commission_rate": d["commission_rate"],
        "commission_yuan": d["commission_yuan"],
        "commission_wan": d["commission_wan"],
        "total_revenue_yuan": d["total_revenue_yuan"],
        "is_renewal": d["is_renewal"],
        "is_cross_region": d["is_cross_region"],
        "deal_cycle_days": d["deal_cycle_days"],
    }


@router.get("/buildings")
async def list_buildings() -> dict[str, Any]:
    """Building pool with vacancy rollup (mock vacancy per building)."""
    by_b: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in DEALS:
        by_b[d["building_name"]].append(d)
    items = []
    # Deterministic mock vacancy per building
    for b, recs in by_b.items():
        seed = sum(ord(c) for c in b)
        mock_vacancy = round(0.08 + (seed % 18) / 100.0, 4)
        total_area = sum(r["area_sqm"] for r in recs)
        items.append({
            "building_name": b,
            "grade": recs[0]["building_grade"],
            "region": recs[0]["region"],
            "deal_count": len(recs),
            "total_deal_area_sqm": total_area,
            "avg_unit_rent_yuan_per_sqm_per_month": round(
                sum(r["monthly_rent_yuan_per_sqm"] for r in recs) / len(recs), 2
            ),
            "vacancy_rate": mock_vacancy,
            "avg_commission_rate": round(sum(r["commission_rate"] for r in recs) / len(recs), 4),
        })
    items.sort(key=lambda x: x["total_deal_area_sqm"], reverse=True)
    return {"line_id": "office-leasing", "count": len(items), "items": items}


@router.get("/brokers")
async def list_brokers() -> dict[str, Any]:
    """Per-broker productivity rollup."""
    by_b: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for d in DEALS:
        by_b[d["broker"]].append(d)
    items = []
    for name, recs in by_b.items():
        total_commission = sum(r["commission_wan"] for r in recs)
        # Per-broker-per-month: rough proxy using deal_cycle_days average
        avg_cycle = sum(r["deal_cycle_days"] for r in recs) / len(recs)
        # months_per_deal = cycle / 30
        months_per_deal = avg_cycle / 30.0
        per_broker = round(total_commission / months_per_deal, 1) if months_per_deal > 0 else 0
        items.append({
            "broker": name,
            "deal_count": len(recs),
            "total_commission_wan": round(total_commission, 1),
            "per_broker_output_wan_per_month": per_broker,
            "avg_deal_cycle_days": round(avg_cycle, 1),
            "avg_area_sqm": round(sum(r["area_sqm"] for r in recs) / len(recs), 1),
            "renewal_count": sum(1 for r in recs if r["is_renewal"]),
        })
    items.sort(key=lambda x: x["per_broker_output_wan_per_month"], reverse=True)
    return {"line_id": "office-leasing", "count": len(items), "items": items}


# Client mix: industry-level diversity
@router.get("/clients/industry-mix")
async def client_industry_mix() -> dict[str, Any]:
    counts: Counter = Counter()
    for d in DEALS:
        counts[d["tenant_industry"]] += 1
    total = sum(counts.values()) or 1
    items = [
        {"industry": k, "deal_count": v, "share": round(v / total, 4)}
        for k, v in counts.most_common()
    ]
    n = len(items)
    if n > 1:
        h = -sum(it["share"] * math.log(it["share"]) for it in items if it["share"] > 0)
        h_max = math.log(n)
        diversity = h / h_max
    else:
        diversity = 0.0
    return {
        "line_id": "office-leasing",
        "industries": items,
        "diversity_index": round(diversity, 4),
    }
