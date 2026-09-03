"""
business_lines/residential/api/router.py

FastAPI router for the residential business line.

The loader (apps/api/app/routers/registry.py) finds this module and mounts
its `router` APIRouter under the line's api_prefix (e.g. /api/lines/residential).

Endpoints (all paths RELATIVE to the api_prefix):
    GET /ping                            health check
    GET /info                            static line info
    GET /indicators                      list of 10+ KPI indicators + line-level current values
    GET /projects                        list all mock residential projects
    GET /projects/{project_id}           get one project
    GET /projects/{project_id}/dynamic-pl
    GET /projects/{project_id}/payment
    GET /projects/{project_id}/redlines
    GET /projects/{project_id}/dedup-forecast

Data is loaded from JSON seed files under `<this file>/../data/seed/*.json`.
The router is self-contained: it does NOT relative-import any sibling module
(loader uses importlib.util.spec_from_file_location, not package import).
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

# Module-level APIRouter. The loader looks for `router` or `app` at module scope.
router = APIRouter(tags=["residential"])

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# This file lives at business_lines/residential/api/router.py
_HERE = Path(__file__).resolve().parent
_LINE_ROOT = _HERE.parent
_DATA_DIR = _LINE_ROOT / "data" / "seed"
_MANIFEST = _LINE_ROOT / "manifest.yaml"
_INDICATORS = _LINE_ROOT / "indicators.yaml"

# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_seed_projects() -> list[dict[str, Any]]:
    """Read every *.json under data/seed/, sorted by project_id."""
    if not _DATA_DIR.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(_DATA_DIR.glob("*.json")):
        with p.open("r", encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# Cache seed data on first access. Re-read is cheap but we keep it simple.
_PROJECTS_CACHE: list[dict[str, Any]] | None = None


def _projects() -> list[dict[str, Any]]:
    global _PROJECTS_CACHE
    if _PROJECTS_CACHE is None:
        _PROJECTS_CACHE = _load_seed_projects()
    return _PROJECTS_CACHE


def _by_id(project_id: str) -> dict[str, Any]:
    for p in _projects():
        if p.get("project_id") == project_id:
            return p
    raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")


# ---------------------------------------------------------------------------
# KPI computations (mock but realistic-looking)
# ---------------------------------------------------------------------------

# 12-month month labels (rolling, e.g. last 12 months ending at "current")
MONTH_LABELS_HISTORY = [
    "M-11", "M-10", "M-9", "M-8", "M-7", "M-6",
    "M-5", "M-4", "M-3", "M-2", "M-1", "M",
]
MONTH_LABELS_FORECAST = [
    "M+1", "M+2", "M+3", "M+4", "M+5", "M+6",
    "M+7", "M+8", "M+9", "M+10", "M+11", "M+12",
]


def _project_gross_sales_yi(p: dict[str, Any]) -> float:
    """Gross sales value in 亿元 = saleable_area_wan_sqm * avg_price_per_sqm / 10000."""
    return float(p["saleable_area_wan_sqm"]) * float(p["avg_price_per_sqm"]) / 1e4


def _project_dyn_pl(p: dict[str, Any]) -> dict[str, Any]:
    """Compute dynamic P&L KPIs for a single project.

    All ratios are in [0, 1]; irr/net_margin are absolute (e.g. 0.18 == 18%).
    """
    gross = _project_gross_sales_yi(p)  # 亿元
    dyn_cost = float(p["dynamic_cost_yi"])  # 亿元
    land = float(p.get("land_cost_yi", 0.0))  # 亿元
    ch_fee = float(p["channel_fee_wan"]) / 1e4  # 亿元
    comm = float(p["commission_wan"]) / 1e4  # 亿元
    tax = gross * 0.05  # 5% 税金及附加
    invested = dyn_cost + land  # 累计投入
    # 净利润 = 货值 - 动态成本 - 土地 - 渠道费 - 佣金 - 税
    net = gross - dyn_cost - land - ch_fee - comm - tax
    net_margin = net / gross if gross > 0 else 0.0
    # 简化 IRR：3 年期年化 (此处仅作 mock，真实计算需要项目级现金流)
    roi_years = 3.0
    irr = (math.pow(max(gross / max(invested, 0.01), 0.01), 1.0 / roi_years) - 1.0) * net_margin
    # 月度去化率：12 个月平均
    dedup_avg = sum(p.get("dedup_history", [])) / max(len(p.get("dedup_history", [])), 1)
    return {
        "gross_sales_yi": round(gross, 2),
        "dynamic_cost_yi": round(dyn_cost, 2),
        "land_cost_yi": round(land, 2),
        "channel_fee_yi": round(ch_fee, 4),
        "commission_yi": round(comm, 4),
        "tax_yi": round(tax, 2),
        "net_profit_yi": round(net, 2),
        "irr": round(max(min(irr, 0.6), -0.2), 4),  # 夹到 [-20%, 60%]
        "net_margin": round(max(min(net_margin, 0.5), -0.2), 4),
        "project_roi": round(net / max(invested, 0.01), 4),
        "monthly_dedup_rate": round(dedup_avg, 4),
    }


def _project_payment(p: dict[str, Any]) -> dict[str, Any]:
    plan = p.get("monthly_payment_plan_yi", [])
    actual = p.get("monthly_payment_actual_yi", [])
    months = [MONTH_LABELS_HISTORY[i] for i in range(min(len(plan), len(actual), 12))]
    plan_series = plan[: len(months)]
    actual_series = actual[: len(months)]
    cum_plan = sum(plan_series)
    cum_actual = sum(actual_series)
    # 渠道费 / 当期签约金额（粗略：monthly avg）
    monthly_sales = sum(plan_series) * 1.0  # 亿元
    ch_fee_yi = float(p["channel_fee_wan"]) / 1e4
    ch_fee_monthly = ch_fee_yi / 12.0
    ch_ratio = ch_fee_monthly / max(monthly_sales / 12.0, 0.01) if monthly_sales > 0 else 0.0
    comm_yi = float(p["commission_wan"]) / 1e4
    comm_monthly = comm_yi / 12.0
    return {
        "monthly_plan_yi": [round(x, 3) for x in plan_series],
        "monthly_actual_yi": [round(x, 3) for x in actual_series],
        "months": months,
        "cumulative_plan_yi": round(cum_plan, 3),
        "cumulative_actual_yi": round(cum_actual, 3),
        "payment_completion": round(cum_actual / cum_plan, 4) if cum_plan > 0 else 0.0,
        "monthly_payment_vs_plan": round(
            sum(actual_series) / sum(plan_series), 4
        ) if sum(plan_series) > 0 else 0.0,
        "monthly_commission_yi": round(comm_monthly, 4),
        "monthly_channel_fee_yi": round(ch_fee_monthly, 4),
        "channel_fee_ratio": round(min(max(ch_ratio, 0.0), 0.2), 4),
    }


def _project_redlines(p: dict[str, Any]) -> dict[str, Any]:
    d = p["debt_structure"]
    short_debt = float(d["short_term_debt_yi"])
    long_debt = float(d["long_term_debt_yi"])
    cash = float(d["cash_yi"])
    total_assets = float(d["total_assets_yi"])
    total_liabilities = float(d["total_liabilities_yi"])
    equity = float(d["shareholders_equity_yi"])
    alr = total_liabilities / total_assets if total_assets > 0 else 0.0
    net_debt = (short_debt + long_debt - cash) / equity if equity > 0 else 0.0
    csd = cash / short_debt if short_debt > 0 else 999.0
    return {
        "short_term_debt_yi": short_debt,
        "long_term_debt_yi": long_debt,
        "cash_yi": cash,
        "total_assets_yi": total_assets,
        "total_liabilities_yi": total_liabilities,
        "shareholders_equity_yi": equity,
        "asset_liability_ratio": round(alr, 4),
        "net_debt_ratio": round(net_debt, 4),
        "cash_to_short_debt": round(min(csd, 99.0), 4),
        "thresholds": {
            "asset_liability_ratio": 0.70,  # 三道红线阈值
            "net_debt_ratio": 1.00,
            "cash_to_short_debt": 1.00,
        },
        "status": {
            "asset_liability_ratio": "green" if alr < 0.70 else "red",
            "net_debt_ratio": "green" if net_debt < 1.00 else "red",
            "cash_to_short_debt": "green" if csd >= 1.00 else "red",
        },
    }


def _project_dedup_forecast(p: dict[str, Any]) -> dict[str, Any]:
    history = p.get("dedup_history", [])
    median = p.get("dedup_forecast_median", [])
    lower = p.get("dedup_forecast_lower", [])
    upper = p.get("dedup_forecast_upper", [])
    return {
        "history": [round(x, 4) for x in history],
        "history_months": MONTH_LABELS_HISTORY[: len(history)],
        "forecast_median": [round(x, 4) for x in median],
        "forecast_lower": [round(x, 4) for x in lower],
        "forecast_upper": [round(x, 4) for x in upper],
        "forecast_months": MONTH_LABELS_FORECAST[: len(median)],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/ping")
async def ping() -> dict:
    """Health endpoint."""
    return {"status": "ok", "line": "residential", "projects_loaded": len(_projects())}


@router.get("/info")
async def info() -> dict:
    """Static line info + manifest echo."""
    manifest = _load_yaml(_MANIFEST)
    return {
        "line_id": "residential",
        "name": manifest.get("name", "住宅分析"),
        "version": manifest.get("version", "0.0.0"),
        "icon": manifest.get("icon", "HomeOutlined"),
        "projects_loaded": len(_projects()),
    }


@router.get("/indicators")
async def indicators() -> dict:
    """Return all indicator definitions + line-level aggregate current values.

    Aggregation is the simple average across all loaded projects (area-weighted
    fallback is intentional). Real DBT-backed values would replace this later.
    """
    ind_file = _load_yaml(_INDICATORS)
    defs = ind_file.get("indicators", [])
    projects = _projects()

    # Compute per-project KPI values.
    pls = [_project_dyn_pl(p) for p in projects]
    pays = [_project_payment(p) for p in projects]
    reds = [_project_redlines(p) for p in projects]

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    agg = {
        "dynamic_irr": _avg([x["irr"] for x in pls]),
        "dynamic_net_margin": _avg([x["net_margin"] for x in pls]),
        "payment_completion": _avg([x["payment_completion"] for x in pays]),
        "channel_fee_ratio": _avg([x["channel_fee_ratio"] for x in pays]),
        "asset_liability_ratio": _avg([x["asset_liability_ratio"] for x in reds]),
        "net_debt_ratio": _avg([x["net_debt_ratio"] for x in reds]),
        "cash_to_short_debt": _avg([x["cash_to_short_debt"] for x in reds]),
        "monthly_dedup_rate": _avg([x["monthly_dedup_rate"] for x in pls]),
        "payment_vs_plan": _avg([x["monthly_payment_vs_plan"] for x in pays]),
        "project_roi": _avg([x["project_roi"] for x in pls]),
    }

    items: list[dict[str, Any]] = []
    for d in defs:
        items.append(
            {
                "indicator_id": d["id"],
                "title": d["title"],
                "unit": d.get("unit", ""),
                "format": d.get("format", "number"),
                "value": agg.get(d["id"], 0.0),
                "source": d.get("source", ""),
                "description": d.get("description", ""),
            }
        )

    return {
        "line_id": "residential",
        "indicators": items,
        "charts": ind_file.get("charts", []),
        "generated_at": "2026-09-02T14:00:00Z",
    }


@router.get("/projects")
async def list_projects() -> dict:
    """List all mock residential projects (lightweight summary)."""
    rows: list[dict[str, Any]] = []
    for p in _projects():
        rows.append(
            {
                "project_id": p["project_id"],
                "name": p["name"],
                "city": p["city"],
                "developer": p["developer"],
                "stage": p["stage"],
                "saleable_area_wan_sqm": p["saleable_area_wan_sqm"],
                "avg_price_per_sqm": p["avg_price_per_sqm"],
                "dynamic_cost_yi": p["dynamic_cost_yi"],
                "cumulative_payment_yi": p["cumulative_payment_yi"],
            }
        )
    return {"line_id": "residential", "count": len(rows), "projects": rows}


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> dict:
    """Return full record for one project."""
    return _by_id(project_id)


@router.get("/projects/{project_id}/dynamic-pl")
async def project_dynamic_pl(project_id: str) -> dict:
    """Dynamic P&L for one project, including IRR / net_margin / ROI / monthly dedup."""
    p = _by_id(project_id)
    return {
        "line_id": "residential",
        "project_id": project_id,
        "project_name": p["name"],
        **_project_dyn_pl(p),
    }


@router.get("/projects/{project_id}/payment")
async def project_payment(project_id: str) -> dict:
    """Payment / commission / channel-fee data for one project (last 12 months)."""
    p = _by_id(project_id)
    return {
        "line_id": "residential",
        "project_id": project_id,
        "project_name": p["name"],
        **_project_payment(p),
    }


@router.get("/projects/{project_id}/redlines")
async def project_redlines(project_id: str) -> dict:
    """Three red lines (三道红线) snapshot for one project."""
    p = _by_id(project_id)
    return {
        "line_id": "residential",
        "project_id": project_id,
        "project_name": p["name"],
        **_project_redlines(p),
    }


@router.get("/projects/{project_id}/dedup-forecast")
async def project_dedup_forecast(project_id: str) -> dict:
    """Historical + forecast dedup (去化) rate for one project."""
    p = _by_id(project_id)
    return {
        "line_id": "residential",
        "project_id": project_id,
        "project_name": p["name"],
        **_project_dedup_forecast(p),
    }
