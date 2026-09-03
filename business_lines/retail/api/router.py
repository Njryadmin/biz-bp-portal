"""
business_lines/retail/api/router.py

FastAPI router for the 零售分析 (retail) business line.

Endpoints (mounted at /api/lines/retail):
    GET /indicators                                  -> 12 indicator definitions
    GET /properties                                  -> 8 mock properties + headline KPIs
    GET /properties/{id}/noi-waterfall               -> NOI waterfall (potential -> effective -> NOI)
    GET /properties/{id}/brand-mix                   -> brand composition + diversity index
    GET /properties/{id}/renovation-npv              -> maintain vs renovate NPV comparison
    GET /properties/{id}/collection-rate             -> current rate + 12-month trend

All data is loaded once at module import from data/seed/properties.json.
This is a mock / demo implementation; in production these would hit a warehouse
(mart_retail.mart_property_kpis, mart_brand_mix, mart_renovation_npv).
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

# ---------------------------------------------------------------------------
# Seed data loader (idempotent, runs once at import)
# ---------------------------------------------------------------------------

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed" / "properties.json"


def _load_seed() -> list[dict[str, Any]]:
    """Load and lightly enrich the seed properties with derived headline metrics."""
    if not _SEED_PATH.exists():
        return []
    raw = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
    enriched: list[dict[str, Any]] = []
    for p in raw:
        gla = float(p["gla_wan_sqm"]) * 10_000  # 万㎡ -> ㎡
        noi = float(p["noi_wan"]) * 10_000  # 万元 -> 元
        gross = float(p["gross_rent_wan"]) * 10_000
        opex = float(p["opex_wan"]) * 10_000
        foot = float(p["foot_traffic_wan_per_month"]) * 10_000
        # 坪效 = NOI / 面积 / 12 (元/㎡/月)
        efficiency = round(noi / gla / 12, 2) if gla > 0 else 0.0
        # 客流坪效 = 月客流 / 面积 / 30 (人/㎡/日)
        foot_eff = round(foot / gla / 30, 4) if gla > 0 else 0.0
        # Effective rent = NOI + OpEx
        effective_rent = round(noi + opex, 0)
        # Vacancy implied from gross vs effective: (gross - effective) / gross
        implied_vacancy = (gross - effective_rent) / gross if gross > 0 else 0.0

        enriched.append(
            {
                "property_id": p["property_id"],
                "name": p["name"],
                "name_en": p.get("name_en", ""),
                "city": p["city"],
                "city_tier": p.get("city_tier", ""),
                "format": p["format"],
                "format_en": p.get("format_en", ""),
                "gla_wan_sqm": float(p["gla_wan_sqm"]),
                "noi_wan": float(p["noi_wan"]),
                "gross_rent_wan": float(p["gross_rent_wan"]),
                "opex_wan": float(p["opex_wan"]),
                "vacancy_rate": float(p["vacancy_rate"]),
                "collection_rate": float(p["collection_rate"]),
                "rent_escalation_rate": float(p["rent_escalation_rate"]),
                "foot_traffic_wan_per_month": float(p["foot_traffic_wan_per_month"]),
                "total_brands": int(p["total_brands"]),
                "weighted_lease_remaining_years": float(p["weighted_lease_remaining_years"]),
                "headline_kpis": {
                    "noi": float(p["noi_wan"]),
                    "efficiency": efficiency,
                    "foot_traffic_efficiency": foot_eff,
                    "vacancy_rate": float(p["vacancy_rate"]),
                    "collection_rate": float(p["collection_rate"]),
                    "rent_escalation": float(p["rent_escalation_rate"]),
                    "wault": float(p["weighted_lease_remaining_years"]),
                },
                "_internal": {
                    "gla_sqm": gla,
                    "noi_yuan": noi,
                    "gross_rent_yuan": gross,
                    "opex_yuan": opex,
                    "effective_rent_yuan": effective_rent,
                    "implied_vacancy": round(implied_vacancy, 4),
                    "leases": p.get("leases", []),
                    "top_brands": p.get("top_brands", []),
                    "foot_traffic_per_month": foot,
                },
            }
        )
    return enriched


PROPERTIES: list[dict[str, Any]] = _load_seed()
PROPERTY_INDEX: dict[str, dict[str, Any]] = {p["property_id"]: p for p in PROPERTIES}


# ---------------------------------------------------------------------------
# Indicator catalog (mirrors indicators.yaml; embedded for fast lookup)
# ---------------------------------------------------------------------------

INDICATORS: list[dict[str, Any]] = [
    {
        "id": "noi",
        "title": "NOI (净营业收入)",
        "unit": "万元",
        "format": "currency",
        "aggregation": "sum",
        "source": "mart_retail.mart_property_kpis",
        "description": "有效毛收入扣除运营成本后的净营业收入;零售资管的核心指标。",
    },
    {
        "id": "efficiency",
        "title": "坪效",
        "unit": "元/㎡/月",
        "format": "ratio",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "单位可租赁面积每月产生的 NOI,衡量单平米盈利能力。",
    },
    {
        "id": "rent_to_sales",
        "title": "租售比",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "租金占商户销售额的比重;过高预示商户经营压力,过低预示租金有提升空间。",
    },
    {
        "id": "collection_rate",
        "title": "收缴率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "当期应收租金中实际收回的比例,反映租户履约能力。",
    },
    {
        "id": "renovation_npv",
        "title": "调改 NPV",
        "unit": "万元",
        "format": "currency",
        "aggregation": "sum",
        "source": "mart_retail.mart_renovation_npv",
        "description": "调改方案在持有期内的净现值;与维持现状方案做差额对比。",
    },
    {
        "id": "foot_traffic_efficiency",
        "title": "客流坪效",
        "unit": "人/㎡/日",
        "format": "ratio",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "单位面积日均接待客流,衡量商场人气与引流效率。",
    },
    {
        "id": "brand_diversity",
        "title": "品牌多样性指数",
        "unit": "0-1",
        "format": "ratio",
        "aggregation": "avg",
        "source": "mart_retail.mart_brand_mix",
        "description": "基于 Shannon 熵归一化的业态多样性;越接近 1 越多元,降低单一业态风险。",
    },
    {
        "id": "rent_escalation",
        "title": "租金递增率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "租约约定的年化租金递增幅度,加权平均。",
    },
    {
        "id": "vacancy_rate",
        "title": "空置率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "空置可租面积占总可租面积的比例;3-5% 为健康,8%+ 需警惕。",
    },
    {
        "id": "wault",
        "title": "租约剩余年限加权 (WAULT)",
        "unit": "年",
        "format": "ratio",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "按面积加权的租约剩余年限。",
    },
    {
        "id": "sales_per_sqm",
        "title": "坪效销售额",
        "unit": "元/㎡/月",
        "format": "ratio",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "单位可租面积每月商户销售额。",
    },
    {
        "id": "tenant_churn",
        "title": "租户流失率",
        "unit": "%",
        "format": "percent",
        "aggregation": "avg",
        "source": "mart_retail.mart_property_kpis",
        "description": "当期到期未续约或中途解约的租户面积占比。",
    },
]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/ping")
async def ping() -> dict[str, Any]:
    """Health endpoint."""
    return {"status": "ok", "line": "retail", "properties_loaded": len(PROPERTIES)}


def _require_property(property_id: str) -> dict[str, Any]:
    if property_id not in PROPERTY_INDEX:
        raise HTTPException(status_code=404, detail=f"unknown property_id: {property_id}")
    return PROPERTY_INDEX[property_id]


@router.get("/indicators")
async def list_indicators() -> dict[str, Any]:
    """Return the indicator catalog for this line."""
    return {
        "line_id": "retail",
        "indicators": INDICATORS,
        "count": len(INDICATORS),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


@router.get("/properties")
async def list_properties(
    city: str | None = Query(default=None, description="按城市过滤,例如 上海/北京"),
    format: str | None = Query(default=None, description="按业态过滤: 购物中心/街铺"),
) -> dict[str, Any]:
    """List all retail properties with headline KPIs.

    Optional filters: ?city=上海  ?format=购物中心
    """
    items = []
    for p in PROPERTIES:
        if city and p["city"] != city:
            continue
        if format and p["format"] != format:
            continue
        # Strip the internal computation fields from the public response.
        items.append({k: v for k, v in p.items() if not k.startswith("_")})
    return {
        "line_id": "retail",
        "count": len(items),
        "items": items,
    }


@router.get("/properties/{property_id}/noi-waterfall")
async def noi_waterfall(property_id: str) -> dict[str, Any]:
    """Return the NOI waterfall: Potential Gross -> Vacancy -> EGR -> OpEx -> NOI.

    All values are in 万元 for direct display.
    """
    p = _require_property(property_id)
    gross = p["gross_rent_wan"]
    opex = p["opex_wan"]
    noi = p["noi_wan"]
    egr = noi + opex  # effective gross rent = NOI + OpEx
    vacancy_loss = gross - egr

    items = [
        {"step": "Potential Gross Rent", "value_wan": round(gross, 0), "type": "start"},
        {"step": "Vacancy & Credit Loss", "value_wan": round(-vacancy_loss, 0), "type": "subtract"},
        {"step": "Effective Gross Rent", "value_wan": round(egr, 0), "type": "subtotal"},
        {"step": "Operating Expenses", "value_wan": round(-opex, 0), "type": "subtract"},
        {"step": "NOI", "value_wan": round(noi, 0), "type": "end"},
    ]
    return {
        "property_id": property_id,
        "property_name": p["name"],
        "period": "2025-Q4",
        "items": items,
        "noi_margin": round(noi / egr, 4) if egr > 0 else 0.0,
        "implied_vacancy": round(vacancy_loss / gross, 4) if gross > 0 else 0.0,
    }


@router.get("/properties/{property_id}/brand-mix")
async def brand_mix(property_id: str) -> dict[str, Any]:
    """Return brand composition by category with diversity index.

    Computes Shannon entropy normalized to 0-1 over the lease sample, plus
    category-level aggregate stats (brand count, area share, avg rent).
    """
    p = _require_property(property_id)
    leases = p["_internal"]["leases"]
    total_area = sum(l["area_sqm"] for l in leases) or 1

    # Aggregate by category
    by_cat: dict[str, dict[str, Any]] = {}
    for l in leases:
        cat = l["category"]
        slot = by_cat.setdefault(
            cat,
            {
                "category": cat,
                "brand_count": 0,
                "area_sqm": 0,
                "weighted_rent": 0.0,
            },
        )
        slot["brand_count"] += 1
        slot["area_sqm"] += l["area_sqm"]
        slot["weighted_rent"] += l["area_sqm"] * l["monthly_rent_yuan_per_sqm"]

    categories = []
    for cat, slot in by_cat.items():
        share = slot["area_sqm"] / total_area
        avg_rent = slot["weighted_rent"] / slot["area_sqm"] if slot["area_sqm"] > 0 else 0.0
        categories.append(
            {
                "category": cat,
                "brand_count": slot["brand_count"],
                "area_share": round(share, 4),
                "avg_rent_yuan_per_sqm_per_month": round(avg_rent, 2),
            }
        )
    categories.sort(key=lambda c: c["area_share"], reverse=True)

    # Shannon entropy, normalized to 0-1.
    # H = -sum(p * ln p); H_max = ln(n_categories).
    n = len(categories)
    if n > 1:
        h = -sum(c["area_share"] * math.log(c["area_share"]) for c in categories if c["area_share"] > 0)
        h_max = math.log(n)
        diversity = h / h_max
    else:
        diversity = 0.0

    return {
        "property_id": property_id,
        "property_name": p["name"],
        "total_brands": p["total_brands"],
        "sampled_leases": len(leases),
        "categories": categories,
        "diversity_index": round(diversity, 4),
        "top_brands": p["_internal"]["top_brands"][:15],
    }


def _npv(cash_flows: list[float], rate: float) -> float:
    return sum(cf / ((1.0 + rate) ** t) for t, cf in enumerate(cash_flows))


def _irr(cash_flows: list[float], max_iter: int = 200, tol: float = 1e-6) -> float | None:
    """Bisection IRR over [-0.5, 10.0]. Returns None if no sign change / no convergence.

    The wide upper bound (1000% IRR) covers real estate deals where capex is a
    small fraction of NOI, which produces very high unleveraged IRRs.
    """
    if all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        return None
    lo, hi = -0.5, 10.0
    f_lo = sum(cf / ((1.0 + lo) ** t) for t, cf in enumerate(cash_flows))
    f_hi = sum(cf / ((1.0 + hi) ** t) for t, cf in enumerate(cash_flows))
    if f_lo * f_hi > 0:
        return None
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = sum(cf / ((1.0 + mid) ** t) for t, cf in enumerate(cash_flows))
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


@router.get("/properties/{property_id}/renovation-npv")
async def renovation_npv(
    property_id: str,
    horizon: int = Query(default=10, ge=3, le=20, description="持有期(年)"),
    discount_rate: float = Query(default=0.08, ge=0.0, le=0.30, description="折现率"),
) -> dict[str, Any]:
    """Return maintain-vs-renovate NPV comparison.

    Maintain: capex=0, NOI grows at rent_escalation_rate (slow).
    Renovate: capex paid in year 0, NOI lifts by uplift and grows faster,
              terminal cap rate applied to year-{horizon} NOI.
    """
    p = _require_property(property_id)
    base_noi = p["noi_wan"]  # 万元
    base_escalation = p["rent_escalation_rate"]
    # 调改升级: 一次性资本支出 = 总建筑面积 × 600元/㎡.
    # gla_wan_sqm 单位是 万㎡ (e.g. 20 表示 20万㎡ = 200,000 ㎡).
    # 600元/㎡ × 200,000 ㎡ = 1.2 亿元 = 12,000 万元.
    # 公式: gla_wan × 10000 ㎡/万㎡ × 600 元/㎡ / 10000 元/万元 = gla_wan × 600 万元.
    gla_wan = p["gla_wan_sqm"]
    renovate_capex_wan = round(gla_wan * 600.0, 0)  # e.g. 20万㎡ -> 12000万
    # 调改后第一年 NOI 提升 12%, 之后递增率提到 6%
    renovate_uplift_year1 = 0.12
    renovate_escalation = round(base_escalation + 0.015, 4)
    terminal_cap_rate = 0.055  # 资本化率

    # Maintain scenario: NOI grows at base escalation, terminal cap.
    maintain_cf: list[float] = []
    for t in range(horizon):
        noi_t = base_noi * ((1.0 + base_escalation) ** t)
        if t == horizon - 1:
            noi_t += noi_t / terminal_cap_rate  # terminal value at end
        maintain_cf.append(noi_t)
    maintain_npv = _npv(maintain_cf, discount_rate)
    maintain_irr = _irr(maintain_cf)

    # Renovate scenario: capex in year 0, then NOI uplifts in year 1+.
    renovate_cf: list[float] = [-renovate_capex_wan]
    for t in range(1, horizon + 1):
        # Year 1 = uplift then grow from there.
        noi_t = base_noi * (1.0 + renovate_uplift_year1) * ((1.0 + renovate_escalation) ** (t - 1))
        cf_t = noi_t
        if t == horizon:
            cf_t += noi_t / terminal_cap_rate  # terminal
        renovate_cf.append(cf_t)
    renovate_npv_val = _npv(renovate_cf, discount_rate)
    renovate_irr_val = _irr(renovate_cf)

    return {
        "property_id": property_id,
        "property_name": p["name"],
        "horizon_years": horizon,
        "discount_rate": discount_rate,
        "terminal_cap_rate": terminal_cap_rate,
        "maintain": {
            "scenario": "maintain",
            "capex_wan": 0,
            "annual_noi_year1_wan": round(base_noi, 0),
            "noi_growth": base_escalation,
            "npv_wan": round(maintain_npv, 0),
            "irr": round(maintain_irr, 4) if maintain_irr is not None else None,
        },
        "renovate": {
            "scenario": "renovate",
            "capex_wan": round(renovate_capex_wan, 0),
            "annual_noi_year1_wan": round(base_noi * (1.0 + renovate_uplift_year1), 0),
            "noi_growth": renovate_escalation,
            "npv_wan": round(renovate_npv_val, 0),
            "irr": round(renovate_irr_val, 4) if renovate_irr_val is not None else None,
        },
        "delta": {
            "npv_wan": round(renovate_npv_val - maintain_npv, 0),
            "delta_label": (
                "调改 NPV 更高,建议调改"
                if renovate_npv_val > maintain_npv
                else "调改不经济,建议维持"
            ),
        },
    }


@router.get("/properties/{property_id}/collection-rate")
async def collection_rate(property_id: str) -> dict[str, Any]:
    """Return current collection rate + 12-month synthetic trend.

    The seed has a single current_rate value; we synthesize a believable 12-month
    trend by walking a low-amplitude random walk anchored at the current value.
    """
    p = _require_property(property_id)
    current = p["collection_rate"]
    # Deterministic pseudo-random based on property_id so the demo is stable.
    seed = sum(ord(c) for c in property_id)
    months = []
    value = current
    # Walk backward from current month; clamp to [0.85, 1.0].
    for i in range(12):
        # pseudo noise in [-0.01, +0.01]
        noise = ((seed * (i + 1)) % 200 - 100) / 10000.0
        value = max(0.85, min(1.0, value - noise))
        # 12 months ago is month index 11; current is index 0.
        month_offset = 11 - i
        year = 2025
        month = 12 - month_offset
        if month <= 0:
            month += 12
            year -= 1
        months.append({"month": f"{year:04d}-{month:02d}", "rate": round(value, 4)})
    months.reverse()
    # The last entry should equal the current rate.
    months[-1]["rate"] = round(current, 4)

    # Aggregate stats
    avg = sum(m["rate"] for m in months) / len(months)
    worst = min(months, key=lambda m: m["rate"])
    best = max(months, key=lambda m: m["rate"])

    return {
        "property_id": property_id,
        "property_name": p["name"],
        "current_rate": round(current, 4),
        "average_rate_12m": round(avg, 4),
        "worst_month": worst,
        "best_month": best,
        "trend": months,
    }
