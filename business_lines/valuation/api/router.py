"""
business_lines/valuation/api/router.py

FastAPI router for the 估价部 (valuation) business line.

Endpoints (mounted at /api/lines/valuation):
    GET /ping                                    -> health
    GET /indicators                              -> 10 indicator definitions
    GET /reports                                 -> 8 mock reports + headline KPIs
    GET /reports/{id}/accuracy                   -> revaluation bias analysis
    GET /reports/{id}/timeline                   -> delivery + collection timeline
    GET /appraisers                              -> per-appraiser productivity
    GET /appraisers/{id}/workload                -> appraiser workload + bias

All data is loaded once at module import from data/seed/valuation_reports.json.
Mock / demo; in production these would hit mart_valuation.mart_report_kpis.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

# ---------------------------------------------------------------------------
# Seed data loader
# ---------------------------------------------------------------------------

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "valuation_reports.json"


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        return []
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _enrich_report(r: dict[str, Any]) -> dict[str, Any]:
    """Compute derived fields: bias rate, collection days, etc."""
    val = float(r["valuation_amount_wan"])
    reval = float(r.get("revaluation_amount_wan") or val)
    # Bias: |reval - val| / val; positive means reval is below val (under-estimated)
    bias = abs(reval - val) / val if val > 0 else 0.0
    # Collection days
    issue = datetime.fromisoformat(r["issue_date"])
    coll = datetime.fromisoformat(r["collection_date"])
    coll_days = (coll - issue).days
    # On-time / late
    due = datetime.fromisoformat(r["due_date"])
    actual = datetime.fromisoformat(r["actual_delivery_date"])
    late_days = max(0, (actual - due).days)
    return {
        **r,
        "valuation_amount_wan": val,
        "revaluation_amount_wan": reval,
        "valuation_bias_rate": round(bias, 4),
        "collection_days": coll_days,
        "late_days": late_days,
    }


REPORTS_RAW: list[dict[str, Any]] = _load_seed()
REPORTS: list[dict[str, Any]] = [_enrich_report(r) for r in REPORTS_RAW]
REPORT_INDEX: dict[str, dict[str, Any]] = {r["report_id"]: r for r in REPORTS}


# ---------------------------------------------------------------------------
# Indicator catalog (mirrors indicators.yaml)
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {"id": "report_count", "title": "估价报告数", "unit": "份", "format": "number", "aggregation": "sum",
     "source": "mart_valuation.mart_report_kpis", "description": "当期出具的估价报告份数。"},
    {"id": "valuation_amount", "title": "估价总额", "unit": "万元", "format": "currency", "aggregation": "sum",
     "source": "mart_valuation.mart_report_kpis", "description": "当期报告所对应的评估总价合计。"},
    {"id": "avg_report_size", "title": "单报告均价", "unit": "元/份", "format": "currency", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "每份报告的平均收费。"},
    {"id": "valuation_bias_rate", "title": "重估偏差率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "复估/实际成交价 vs 估价的偏差。"},
    {"id": "collection_days", "title": "回款周期", "unit": "天", "format": "number", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "从交付到回款的平均天数。"},
    {"id": "on_time_delivery_rate", "title": "准时交付率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "合同约定日 ±2 工作日内完成的比例。"},
    {"id": "report_revision_rate", "title": "退改率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "被退回修改的份数占比。"},
    {"id": "per_capita_output", "title": "人均产值", "unit": "万元/人/月", "format": "currency", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "估价师当月人均创收。"},
    {"id": "client_satisfaction", "title": "客户满意度", "unit": "0-100", "format": "number", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "客户打分(0-100)。"},
    {"id": "repeat_client_rate", "title": "复购率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_valuation.mart_report_kpis", "description": "老客户占比。"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "line": "valuation", "reports_loaded": len(REPORTS)}


def _require_report(report_id: str) -> dict[str, Any]:
    if report_id not in REPORT_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown report_id: {report_id}")
    return REPORT_INDEX[report_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    return {
        "line_id": "valuation",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/reports")
async def list_reports(
    purpose: str | None = Query(default=None, description="按估价目的过滤: 抵押/交易/司法/征收/课税"),
    city: str | None = Query(default=None, description="按城市过滤"),
    appraiser: str | None = Query(default=None, description="按估价师过滤"),
) -> dict[str, Any]:
    items = []
    for r in REPORTS:
        if purpose and r["purpose"] != purpose:
            continue
        if city and r["city"] != city:
            continue
        if appraiser and r["appraiser"] != appraiser:
            continue
        items.append(r)
    return {"line_id": "valuation", "count": len(items), "items": items}


@router.get("/reports/{report_id}/accuracy")
async def report_accuracy(report_id: str) -> dict[str, Any]:
    """Return bias analysis: original vs revaluation, deviation, peer band."""
    r = _require_report(report_id)
    val = r["valuation_amount_wan"]
    reval = r["revaluation_amount_wan"]
    delta = reval - val
    delta_pct = delta / val if val > 0 else 0.0
    # Peer band: average bias of reports with same purpose
    same_purpose = [x for x in REPORTS if x["purpose"] == r["purpose"]]
    peer_avg_bias = (
        sum(x["valuation_bias_rate"] for x in same_purpose) / len(same_purpose)
        if same_purpose else 0.0
    )
    return {
        "report_id": report_id,
        "purpose": r["purpose"],
        "valuation_amount_wan": val,
        "revaluation_amount_wan": reval,
        "delta_wan": round(delta, 2),
        "delta_pct": round(delta_pct, 4),
        "abs_bias_rate": r["valuation_bias_rate"],
        "peer_avg_bias": round(peer_avg_bias, 4),
        "bias_band": (
            "excellent" if r["valuation_bias_rate"] < 0.015 else
            "good" if r["valuation_bias_rate"] < 0.025 else
            "warning" if r["valuation_bias_rate"] < 0.04 else "breach"
        ),
        "appraiser": r["appraiser"],
        "revaluation_count": 1,
    }


@router.get("/reports/{report_id}/timeline")
async def report_timeline(report_id: str) -> dict[str, Any]:
    """Return delivery + payment timeline with deltas."""
    r = _require_report(report_id)
    return {
        "report_id": report_id,
        "appraiser": r["appraiser"],
        "events": [
            {"step": "签约", "date": r["issue_date"], "type": "start"},
            {"step": "合同约定交付", "date": r["due_date"], "type": "milestone"},
            {"step": "实际交付", "date": r["actual_delivery_date"], "type": "delivery",
             "delta_days": r["late_days"] * -1, "on_time": r["on_time"]},
            {"step": "回款入账", "date": r["collection_date"], "type": "collection",
             "delta_days": r["collection_days"]},
        ],
        "late_days": r["late_days"],
        "collection_days": r["collection_days"],
        "revision_count": r["revision_count"],
        "client_score": r["client_score"],
    }


@router.get("/appraisers")
async def list_appraisers() -> dict[str, Any]:
    """Per-appraiser productivity + bias rollup."""
    by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in REPORTS:
        by_app[r["appraiser"]].append(r)
    items = []
    for name, recs in by_app.items():
        report_count = len(recs)
        total_fee = sum(rec["fee_yuan"] for rec in recs)
        avg_bias = sum(rec["valuation_bias_rate"] for rec in recs) / report_count
        avg_coll = sum(rec["collection_days"] for rec in recs) / report_count
        on_time_rate = sum(1 for rec in recs if rec["on_time"]) / report_count
        # Per-capita monthly output: rough estimate — 1 month period, no team split
        level = recs[0]["appraiser_level"]
        per_capita_wan = round(total_fee / 10000.0, 1)  # total fee / 1 month
        items.append({
            "appraiser": name,
            "level": level,
            "report_count": report_count,
            "total_fee_yuan": total_fee,
            "per_capita_output_wan_per_month": per_capita_wan,
            "avg_bias_rate": round(avg_bias, 4),
            "avg_collection_days": round(avg_coll, 1),
            "on_time_rate": round(on_time_rate, 4),
        })
    items.sort(key=lambda x: x["per_capita_output_wan_per_month"], reverse=True)
    return {"line_id": "valuation", "count": len(items), "items": items}


@router.get("/appraisers/{name}/workload")
async def appraiser_workload(name: str) -> dict[str, Any]:
    """Per-appraiser report list + bias histogram."""
    recs = [r for r in REPORTS if r["appraiser"] == name]
    if not recs:
        raise HTTPException(status_code=404, detail=f"unknown appraiser: {name}")
    # Bias histogram
    buckets = {"<1%": 0, "1-2%": 0, "2-3%": 0, "3-5%": 0, ">5%": 0}
    for r in recs:
        b = r["valuation_bias_rate"]
        if b < 0.01:
            buckets["<1%"] += 1
        elif b < 0.02:
            buckets["1-2%"] += 1
        elif b < 0.03:
            buckets["2-3%"] += 1
        elif b < 0.05:
            buckets["3-5%"] += 1
        else:
            buckets[">5%"] += 1
    return {
        "appraiser": name,
        "level": recs[0]["appraiser_level"],
        "report_count": len(recs),
        "total_fee_yuan": sum(r["fee_yuan"] for r in recs),
        "avg_bias_rate": round(sum(r["valuation_bias_rate"] for r in recs) / len(recs), 4),
        "bias_histogram": [{"band": k, "count": v} for k, v in buckets.items()],
        "reports": [r["report_id"] for r in recs],
    }
