"""
business_lines/investment/api/router.py

FastAPI router for the 地产投资部 (investment) business line.

Endpoints (mounted at /api/lines/investment):
    GET /ping                       -> health
    GET /indicators                 -> 10 indicator definitions
    GET /funds                      -> 8 mock funds + headline KPIs
    GET /funds/{id}/irr-attribution -> IRR attribution by strategy
    GET /portfolio                  -> aggregate portfolio rollup
    GET /exits                      -> exit ledger
    GET /mgmt-fees                  -> mgmt fee revenue by fund

All data is loaded once at module import from data/seed/funds.json.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "funds.json"


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        return []
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _enrich(f: dict[str, Any]) -> dict[str, Any]:
    aum = float(f["aum_yi"])
    committed = float(f["committed_yi"])
    called = float(f["called_yi"])
    distributed = float(f["distributed_yi"])
    nav = float(f["nav_yi"])
    dry = float(f["dry_powder_yi"])
    # TVPI = (NAV + Distributed) / Called
    tvpi = (nav + distributed) / called if called > 0 else 0
    # DPI = Distributed / Called
    dpi = distributed / called if called > 0 else 0
    # Capital called rate = Called / Committed
    cap_called = called / committed if committed > 0 else 0
    # Mgmt fee revenue (annual) = aum * rate
    mgmt_fee_yi = round(aum * float(f["mgmt_fee_rate"]), 3)
    return {
        **f,
        "tvpi": round(tvpi, 4),
        "dpi": round(dpi, 4),
        "capital_called_rate": round(cap_called, 4),
        "mgmt_fee_revenue_yi": mgmt_fee_yi,
    }


FUNDS_RAW: list[dict[str, Any]] = _load_seed()
FUNDS: list[dict[str, Any]] = [_enrich(f) for f in FUNDS_RAW]
FUND_INDEX: dict[str, dict[str, Any]] = {f["fund_id"]: f for f in FUNDS}


# ---------------------------------------------------------------------------
# Indicator catalog
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {"id": "aum", "title": "AUM", "unit": "亿元", "format": "currency", "aggregation": "sum",
     "source": "mart_investment.mart_fund_kpis", "description": "在管资产总规模。"},
    {"id": "aum_growth", "title": "AUM 同比增速", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_investment.mart_fund_kpis", "description": "本期 vs 上年同期增速。"},
    {"id": "mgmt_fee_rate", "title": "管理费率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_investment.mart_fund_kpis", "description": "管理费/AUM 年化。"},
    {"id": "project_irr", "title": "项目 IRR", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_investment.mart_fund_kpis", "description": "项目级 IRR。"},
    {"id": "realized_return", "title": "已实现收益(DPI)", "unit": "亿元", "format": "currency", "aggregation": "sum",
     "source": "mart_investment.mart_fund_kpis", "description": "已分配给 LP 的现金。"},
    {"id": "unrealized_gain", "title": "未实现收益", "unit": "亿元", "format": "currency", "aggregation": "sum",
     "source": "mart_investment.mart_fund_kpis", "description": "持仓浮盈。"},
    {"id": "dry_powder", "title": "待投金额", "unit": "亿元", "format": "currency", "aggregation": "sum",
     "source": "mart_investment.mart_fund_kpis", "description": "已募集尚未投出。"},
    {"id": "capital_called", "title": "实缴比例", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_investment.mart_fund_kpis", "description": "实缴/认缴。"},
    {"id": "portfolio_count", "title": "组合项目数", "unit": "个", "format": "number", "aggregation": "sum",
     "source": "mart_investment.mart_fund_kpis", "description": "在管项目数。"},
    {"id": "avg_hold_period", "title": "平均持有期", "unit": "年", "format": "ratio", "aggregation": "avg",
     "source": "mart_investment.mart_fund_kpis", "description": "投资到退出的平均年限。"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "line": "investment", "funds_loaded": len(FUNDS)}


def _require_fund(fund_id: str) -> dict[str, Any]:
    if fund_id not in FUND_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown fund_id: {fund_id}")
    return FUND_INDEX[fund_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    return {
        "line_id": "investment",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/funds")
async def list_funds(
    strategy: str | None = Query(default=None, description="核心/增值/机会/REITs/债类"),
    vintage: int | None = Query(default=None),
) -> dict[str, Any]:
    items = []
    for f in FUNDS:
        if strategy and f["strategy"] != strategy:
            continue
        if vintage and f["vintage"] != vintage:
            continue
        items.append(f)
    return {"line_id": "investment", "count": len(items), "items": items}


@router.get("/funds/{fund_id}/irr-attribution")
async def fund_irr_attribution(fund_id: str) -> dict[str, Any]:
    """IRR attribution: 75% from operations, 20% from cap structure, 5% from exit timing."""
    f = _require_fund(fund_id)
    irr = f["weighted_irr"]
    return {
        "fund_id": fund_id,
        "fund_name": f["fund_name"],
        "strategy": f["strategy"],
        "weighted_irr": irr,
        "attribution": [
            {"factor": "运营增值 (NOI 增长 + 招商调改)", "share": 0.55, "contribution": round(irr * 0.55, 4)},
            {"factor": "财务杠杆 (低息资本)", "share": 0.20, "contribution": round(irr * 0.20, 4)},
            {"factor": "资本结构 (GP/LP 杠杆)", "share": 0.10, "contribution": round(irr * 0.10, 4)},
            {"factor": "退出时机 (资本市场窗口)", "share": 0.10, "contribution": round(irr * 0.10, 4)},
            {"factor": "其他", "share": 0.05, "contribution": round(irr * 0.05, 4)},
        ],
        "tvpi": f["tvpi"],
        "dpi": f["dpi"],
    }


@router.get("/portfolio")
async def portfolio_rollup() -> dict[str, Any]:
    """Aggregate portfolio rollup across all funds."""
    total_aum = sum(f["aum_yi"] for f in FUNDS)
    total_committed = sum(f["committed_yi"] for f in FUNDS)
    total_called = sum(f["called_yi"] for f in FUNDS)
    total_distributed = sum(f["distributed_yi"] for f in FUNDS)
    total_nav = sum(f["nav_yi"] for f in FUNDS)
    total_dry = sum(f["dry_powder_yi"] for f in FUNDS)
    total_projects = sum(f["project_count"] for f in FUNDS)
    total_exits = sum(f["exit_count"] for f in FUNDS)
    weighted_irr = sum(f["weighted_irr"] * f["called_yi"] for f in FUNDS) / total_called if total_called > 0 else 0
    weighted_mgmt_fee = sum(f["mgmt_fee_rate"] * f["aum_yi"] for f in FUNDS) / total_aum if total_aum > 0 else 0
    return {
        "line_id": "investment",
        "total_aum_yi": round(total_aum, 1),
        "total_committed_yi": round(total_committed, 1),
        "total_called_yi": round(total_called, 1),
        "total_distributed_yi": round(total_distributed, 1),
        "total_nav_yi": round(total_nav, 1),
        "total_dry_powder_yi": round(total_dry, 1),
        "total_projects": total_projects,
        "total_exits": total_exits,
        "weighted_irr": round(weighted_irr, 4),
        "weighted_mgmt_fee_rate": round(weighted_mgmt_fee, 4),
        "tvpi": round((total_nav + total_distributed) / total_called, 4) if total_called > 0 else 0,
        "capital_called_rate": round(total_called / total_committed, 4) if total_committed > 0 else 0,
    }


@router.get("/exits")
async def list_exits() -> dict[str, Any]:
    """Mock exit ledger: derive per-fund exit records."""
    items = []
    for f in FUNDS:
        if f["exit_count"] == 0:
            continue
        # Distribute realized return across exits
        per_exit = f["distributed_yi"] / f["exit_count"]
        for i in range(f["exit_count"]):
            exit_type = ["协议转让", "REITs 上市", "IPO", "到期处置"][i % 4]
            items.append({
                "fund_id": f["fund_id"],
                "fund_name": f["fund_name"],
                "exit_no": i + 1,
                "exit_type": exit_type,
                "exit_amount_yi": round(per_exit, 2),
                "hold_years": round(f["avg_hold_years"], 1),
                "exit_irr": round(f["weighted_irr"], 4),
            })
    return {"line_id": "investment", "count": len(items), "items": items}


@router.get("/mgmt-fees")
async def mgmt_fees() -> dict[str, Any]:
    """Per-fund mgmt fee revenue + rate."""
    items = []
    for f in FUNDS:
        items.append({
            "fund_id": f["fund_id"],
            "fund_name": f["fund_name"],
            "strategy": f["strategy"],
            "aum_yi": f["aum_yi"],
            "mgmt_fee_rate": f["mgmt_fee_rate"],
            "mgmt_fee_revenue_yi": f["mgmt_fee_revenue_yi"],
        })
    items.sort(key=lambda x: x["mgmt_fee_revenue_yi"], reverse=True)
    total = round(sum(it["mgmt_fee_revenue_yi"] for it in items), 3)
    return {"line_id": "investment", "count": len(items), "total_yi": total, "items": items}
