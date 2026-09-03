"""
business_lines/advisory/api/router.py

FastAPI router for the 地产顾问部 (advisory) business line.

Endpoints (mounted at /api/lines/advisory):
    GET /ping                                -> health
    GET /indicators                          -> 10 indicator definitions
    GET /projects                            -> 8 mock projects + headline KPIs
    GET /projects/{id}/outcome               -> project outcome & renewal analysis
    GET /consultants                         -> per-consultant productivity
    GET /clients/industry-mix                -> client industry diversity index

All data is loaded once at module import from data/seed/advisory_projects.json.
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

# ---------------------------------------------------------------------------
# Seed data loader
# ---------------------------------------------------------------------------

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "advisory_projects.json"


def _load_seed() -> list[dict[str, Any]]:
    if not _SEED_PATH.exists():
        return []
    return json.loads(_SEED_PATH.read_text(encoding="utf-8"))


def _enrich(p: dict[str, Any]) -> dict[str, Any]:
    sign = datetime.fromisoformat(p["sign_date"])
    delivery_due = datetime.fromisoformat(p["delivery_date"])
    actual = p.get("actual_delivery_date")
    actual_dt = datetime.fromisoformat(actual) if actual else None
    duration_days = (delivery_due - sign).days
    late_days = max(0, (actual_dt - delivery_due).days) if actual_dt else (datetime.utcnow().date() - delivery_due.date()).days if datetime.fromisoformat(p["sign_date"]) < datetime.utcnow() else 0
    is_adopted = p["outcome"] == "采纳"
    return {
        **p,
        "expected_duration_days": duration_days,
        "actual_delivery_date": actual,
        "late_days": late_days if actual_dt else 0,
        "is_adopted": is_adopted,
        "on_time": actual_dt is not None and actual_dt <= delivery_due,
    }


PROJECTS_RAW: list[dict[str, Any]] = _load_seed()
PROJECTS: list[dict[str, Any]] = [_enrich(p) for p in PROJECTS_RAW]
PROJECT_INDEX: dict[str, dict[str, Any]] = {p["project_id"]: p for p in PROJECTS}


# ---------------------------------------------------------------------------
# Indicator catalog
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {"id": "project_count", "title": "顾问项目数", "unit": "个", "format": "number", "aggregation": "sum",
     "source": "mart_advisory.mart_project_kpis", "description": "在执行 + 交付的项目数。"},
    {"id": "contract_amount", "title": "合同金额", "unit": "万元", "format": "currency", "aggregation": "sum",
     "source": "mart_advisory.mart_project_kpis", "description": "新签 + 在执行合同总金额。"},
    {"id": "avg_contract", "title": "合同均价", "unit": "万元/个", "format": "currency", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "单项目平均合同金额。"},
    {"id": "renewal_rate", "title": "续约率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "到期客户续约比例。"},
    {"id": "per_consultant_output", "title": "人均产能", "unit": "万元/人/月", "format": "currency", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "顾问月人均创收。"},
    {"id": "client_industry_diversity", "title": "客户行业多样性", "unit": "0-1", "format": "ratio", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "Shannon 熵归一化。"},
    {"id": "project_success_rate", "title": "项目成功率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "顾问建议被采纳且落地的比例。"},
    {"id": "avg_project_duration", "title": "平均项目周期", "unit": "天", "format": "number", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "签约到交付的平均天数。"},
    {"id": "client_nps", "title": "客户 NPS", "unit": "-100~100", "format": "number", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "客户净推荐值。"},
    {"id": "on_time_delivery_rate", "title": "准时交付率", "unit": "%", "format": "percent", "aggregation": "avg",
     "source": "mart_advisory.mart_project_kpis", "description": "按合同日交付的占比。"},
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    return {"status": "ok", "line": "advisory", "projects_loaded": len(PROJECTS)}


def _require_project(project_id: str) -> dict[str, Any]:
    if project_id not in PROJECT_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown project_id: {project_id}")
    return PROJECT_INDEX[project_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    return {
        "line_id": "advisory",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/projects")
async def list_projects(
    service_type: str | None = Query(default=None, description="可研/拿地顾问/投资顾问/再融资顾问"),
    industry: str | None = Query(default=None, description="央企/民企/外资/AMC/政府平台"),
    consultant: str | None = Query(default=None, description="按主顾问过滤"),
) -> dict[str, Any]:
    items = []
    for p in PROJECTS:
        if service_type and p["service_type"] != service_type:
            continue
        if industry and p["industry"] != industry:
            continue
        if consultant and p["lead_consultant"] != consultant:
            continue
        items.append(p)
    return {"line_id": "advisory", "count": len(items), "items": items}


@router.get("/projects/{project_id}/outcome")
async def project_outcome(project_id: str) -> dict[str, Any]:
    """Project outcome + renewal analysis."""
    p = _require_project(project_id)
    # Compare NPS to peer average
    same_type = [x for x in PROJECTS if x["service_type"] == p["service_type"]]
    peer_nps = sum(x["nps"] for x in same_type) / len(same_type) if same_type else 0
    return {
        "project_id": project_id,
        "service_type": p["service_type"],
        "industry": p["industry"],
        "contract_amount_wan": p["contract_amount_wan"],
        "outcome": p["outcome"],
        "is_adopted": p["is_adopted"],
        "renewed": p["renewed"],
        "nps": p["nps"],
        "peer_avg_nps": round(peer_nps, 1),
        "nps_vs_peer": round(p["nps"] - peer_nps, 1),
        "late_days": p["late_days"],
        "on_time": p["on_time"],
        "team_size": p["team_size"],
        "lead_consultant": p["lead_consultant"],
    }


@router.get("/consultants")
async def list_consultants() -> dict[str, Any]:
    """Per-consultant productivity rollup."""
    by_c: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in PROJECTS:
        by_c[p["lead_consultant"]].append(p)
    items = []
    for name, recs in by_c.items():
        total = sum(r["contract_amount_wan"] for r in recs)
        team_size = recs[0]["team_size"]
        # Per-capita: total / (team_size * project_count) — rough per-month proxy
        # Use months as the duration from sign to expected delivery (avg)
        per_capita = round(total / team_size / len(recs) * 1.0, 1)
        items.append({
            "consultant": name,
            "project_count": len(recs),
            "team_size": team_size,
            "total_contract_wan": total,
            "per_consultant_output_wan_per_month": per_capita,
            "avg_nps": round(sum(r["nps"] for r in recs) / len(recs), 1),
            "adopted_rate": round(sum(1 for r in recs if r["is_adopted"]) / len(recs), 4),
        })
    items.sort(key=lambda x: x["per_consultant_output_wan_per_month"], reverse=True)
    return {"line_id": "advisory", "count": len(items), "items": items}


@router.get("/clients/industry-mix")
async def client_industry_mix() -> dict[str, Any]:
    """Client industry distribution + Shannon diversity index."""
    counts: Counter = Counter()
    for p in PROJECTS:
        counts[p["industry"]] += 1
    total = sum(counts.values()) or 1
    items = [
        {"industry": k, "project_count": v, "share": round(v / total, 4)}
        for k, v in counts.most_common()
    ]
    # Shannon entropy, normalized to 0-1
    n = len(items)
    if n > 1:
        h = -sum(it["share"] * math.log(it["share"]) for it in items if it["share"] > 0)
        h_max = math.log(n)
        diversity = h / h_max
    else:
        diversity = 0.0
    return {
        "line_id": "advisory",
        "industries": items,
        "diversity_index": round(diversity, 4),
        "concentration_top1": items[0]["share"] if items else 0.0,
    }
