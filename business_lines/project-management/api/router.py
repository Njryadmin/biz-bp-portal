"""
business_lines/project-management/api/router.py

FastAPI router for the 地产项目管理部 (project-management) business line.

Endpoints (mounted at /api/lines/project-management):
    GET /ping                              -> health
    GET /indicators                        -> 10 indicator definitions
    GET /projects                          -> 8 mock projects + headline KPIs
    GET /projects/{id}/deviation           -> progress + cost deviation detail
    GET /pms                               -> per-PM workload & productivity
    GET /milestones                        -> milestone ledger

All data is loaded once at module import from data/seed/managed_projects.json.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "managed_projects.json"


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        return []
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _enrich(p: dict[str, Any]) -> dict[str, Any]:
    actual = float(p["actual_progress_pct"])
    planned = float(p["planned_progress_pct"])
    progress_dev = actual - planned  # positive = ahead
    actual_cost = float(p["actual_cost_wan"])
    budget = float(p["budgeted_cost_wan"])
    cost_dev = (actual_cost - budget) / budget if budget > 0 else 0
    on_time = p["milestones_on_time"] / p["milestones_total"] if p["milestones_total"] > 0 else 0
    return {
        **p,
        "progress_deviation": round(progress_dev, 4),
        "cost_deviation": round(cost_dev, 4),
        "on_time_milestone_rate": round(on_time, 4),
    }


PROJECTS_RAW: list[dict[str, Any]] = _load_seed()
PROJECTS: list[dict[str, Any]] = [_enrich(p) for p in PROJECTS_RAW]
PROJECT_INDEX: dict[str, dict[str, Any]] = {p["project_id"]: p for p in PROJECTS}


# ---------------------------------------------------------------------------
# Indicator catalog
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {"id": "project_count", "title": "在管项目数", "unit": "个", "format": "number", "aggregation": "sum",
     "source": "mart_pm.mart_pm_kpis", "description": "当月在管项目数。"},
    {"id": "contract_value", "title": "代建合同额", "unit": "亿元", "format": "currency", "aggregation": "sum",
     "source": "mart_pm.mart_pm_kpis", "description": "所有在管项目合同金额合计。"},
    {"id": "progress_deviation", "title": "进度偏差率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "实际 vs 计划进度偏差。"},
    {"id": "cost_deviation", "title": "预算偏差率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "实际 vs 预算偏差。"},
    {"id": "on_time_milestone_rate", "title": "里程碑准时率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "里程碑准时率。"},
    {"id": "quality_defect_rate", "title": "质量缺陷率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "质量缺陷率。"},
    {"id": "safety_incidents", "title": "安全事故数", "unit": "起", "format": "number", "aggregation": "sum",
     "source": "mart_pm.mart_pm_kpis", "description": "安全事故数。"},
    {"id": "client_satisfaction", "title": "客户满意度", "unit": "0-100", "format": "number", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "客户评分。"},
    {"id": "renewal_rate", "title": "续约率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "续约比例。"},
    {"id": "per_pm_output", "title": "PM 人均产能", "unit": "万元/人/月", "format": "currency", "aggregation": "avg",
     "source": "mart_pm.mart_pm_kpis", "description": "PM 月人均管理合同额。"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "line": "project-management", "projects_loaded": len(PROJECTS)}


def _require_project(project_id: str) -> dict[str, Any]:
    if project_id not in PROJECT_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    return PROJECT_INDEX[project_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    return {
        "line_id": "project-management",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/projects")
async def list_projects(
    project_type: str | None = Query(default=None, description="住宅/写字楼/产业园/综合体"),
    lead_pm: str | None = Query(default=None),
) -> dict[str, Any]:
    items = []
    for p in PROJECTS:
        if project_type and p["project_type"] != project_type:
            continue
        if lead_pm and p["lead_pm"] != lead_pm:
            continue
        items.append(p)
    return {"line_id": "project-management", "count": len(items), "items": items}


@router.get("/projects/{project_id}/deviation")
async def project_deviation(project_id: str) -> dict[str, Any]:
    """Progress + cost deviation detail."""
    p = _require_project(project_id)
    # Band classification
    progress_band = (
        "ahead>10%" if p["progress_deviation"] > 0.10 else
        "ahead 5-10%" if p["progress_deviation"] > 0.05 else
        "on_track" if p["progress_deviation"] > -0.05 else
        "lag 5-10%" if p["progress_deviation"] > -0.10 else "lag>10%"
    )
    cost_band = (
        "under>5%" if p["cost_deviation"] < -0.05 else
        "under 3-5%" if p["cost_deviation"] < -0.03 else
        "on_budget" if p["cost_deviation"] < 0.03 else
        "over 3-5%" if p["cost_deviation"] < 0.05 else "over>5%"
    )
    return {
        "project_id": project_id,
        "project_name": p["project_name"],
        "actual_progress_pct": p["actual_progress_pct"],
        "planned_progress_pct": p["planned_progress_pct"],
        "progress_deviation": p["progress_deviation"],
        "progress_band": progress_band,
        "actual_cost_wan": p["actual_cost_wan"],
        "budgeted_cost_wan": p["budgeted_cost_wan"],
        "cost_deviation": p["cost_deviation"],
        "cost_band": cost_band,
        "milestones_total": p["milestones_total"],
        "milestones_on_time": p["milestones_on_time"],
        "on_time_milestone_rate": p["on_time_milestone_rate"],
        "quality_defects": p["quality_defects"],
        "safety_incidents": p["safety_incidents"],
        "client_score": p["client_score"],
    }


@router.get("/pms")
async def list_pms() -> dict[str, Any]:
    """Per-PM workload and productivity."""
    by_pm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in PROJECTS:
        by_pm[p["lead_pm"]].append(p)
    items = []
    for name, recs in by_pm.items():
        total_contract = sum(r["contract_value_yi"] for r in recs)
        total_team = sum(r["pm_team_size"] for r in recs)
        avg_progress = sum(r["progress_deviation"] for r in recs) / len(recs)
        avg_cost = sum(r["cost_deviation"] for r in recs) / len(recs)
        avg_score = sum(r["client_score"] for r in recs) / len(recs)
        # per_pm per month: contract_yi * 10000 / pm_team_size / 12
        per_pm = round(total_contract * 10000.0 / total_team / 12.0, 0)
        items.append({
            "pm": name,
            "project_count": len(recs),
            "total_team_size": total_team,
            "total_contract_yi": round(total_contract, 2),
            "per_pm_output_wan_per_month": per_pm,
            "avg_progress_deviation": round(avg_progress, 4),
            "avg_cost_deviation": round(avg_cost, 4),
            "avg_client_score": round(avg_score, 1),
        })
    items.sort(key=lambda x: x["per_pm_output_wan_per_month"], reverse=True)
    return {"line_id": "project-management", "count": len(items), "items": items}


@router.get("/milestones")
async def list_milestones() -> dict[str, Any]:
    """Mock milestone ledger: aggregate per project."""
    items = []
    for p in PROJECTS:
        on_time = p["milestones_on_time"]
        delayed = p["milestones_total"] - on_time
        items.append({
            "project_id": p["project_id"],
            "project_name": p["project_name"],
            "lead_pm": p["lead_pm"],
            "milestones_total": p["milestones_total"],
            "milestones_on_time": on_time,
            "milestones_delayed": delayed,
            "on_time_rate": p["on_time_milestone_rate"],
        })
    return {"line_id": "project-management", "count": len(items), "items": items}
