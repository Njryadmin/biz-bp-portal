"""
apps/api/app/services/llm/mock_helpers.py

HTTP + data-fetch helpers for the mock LLM backend. Each `dispatch_*`
function implements one intent template: it hits the relevant business-
line API endpoint, picks the top-N / thresholded subset, and returns a
MockAnswer with answer + citations + chart_data.

The mock does NOT import `business_lines/*`. It goes through the Python
HTTP layer against the live API. This is what makes it universal: a new
business line that exposes `/indicators` and `/projects` (or
`/properties`) gets Copilot support for free, no code changes.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

from .mock import MockAnswer

# Per-intent confidence floor. The engine clamps to [0, 1].
INTENT_CONFIDENCE: dict[str, float] = {
    "irr_top": 0.85,
    "noi_top": 0.85,
    "renovation": 0.80,
    "collection": 0.80,
    "vacancy": 0.80,
    "benchmark": 0.80,
    "redlines": 0.80,
    "payment_low": 0.75,
    "dedup_low": 0.75,
    "cross_overview": 0.75,
    "sensitivity": 0.70,
    "line_indicators": 0.70,
    "compare": 0.60,
    "fallback_unknown": 0.30,
}

API_BASE = os.environ.get("FIN_BP_API_BASE", "http://127.0.0.1:8769")
HTTP_TIMEOUT = float(os.environ.get("FIN_BP_COPILOT_HTTP_TIMEOUT", "2.0"))


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_json(path: str, base: str | None = None) -> dict[str, Any] | None:
    """GET `base+path` and parse JSON. Return None on any error.

    Short timeout — copilot should degrade gracefully if a line API is
    down. Errors are logged at debug level by the engine caller.
    """
    base = base or API_BASE
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _resolve_line_id(line: str | None) -> str | None:
    """If `line` is None, return None. Otherwise return the line_id as-is."""
    return line


# Display name lookup for known business lines. Lets intent handlers say
# "在估价部下" instead of hardcoded "在住宅部下". Kept here (not in registry)
# to avoid a hard dependency on registry at import time.
_LINE_DISPLAY_NAMES: dict[str, str] = {
    "residential": "住宅",
    "retail": "零售",
    "retail-leasing": "零售租赁",
    "valuation": "估价",
    "advisory": "顾问",
    "office-leasing": "写字楼租赁",
    "investment": "投资",
    "project-management": "项目管理",
    "industrial": "工业地产",
    "my-line": "测试",
}


def _line_label(line: str) -> str:
    """Return a Chinese display name for the line, or the slug if unknown."""
    return _LINE_DISPLAY_NAMES.get(line, line)


def _c(
    *,
    source: str,
    title: str,
    snippet: str,
    url: str | None = None,
) -> dict[str, Any]:
    """Build one citation dict matching the Citation model."""
    return {
        "source": source,
        "title": title,
        "snippet": snippet,
        "url": url,
    }


def _confidence(intent: str) -> float:
    return INTENT_CONFIDENCE.get(intent, 0.5)


# ---------------------------------------------------------------------------
# Intents — residential
# ---------------------------------------------------------------------------


def intent_residential_irr_top(line: str, top_n: int, **_: Any) -> MockAnswer:
    """IRR top N — works for any line that has a /projects or /<plural> endpoint."""
    if not line:
        line = "residential"  # 唯一保留的 default：没指定业务线时按住宅处理
    data = _http_json(f"/api/lines/{line}/projects")
    if not data or not data.get("projects"):
        return MockAnswer(
            answer=f"未能从{_line_label(line)}线 /projects 端点获取项目数据。请确认 API 正在运行。",
            intent="irr_top",
            confidence=_confidence("irr_top") * 0.5,
        )
    projects = data["projects"]
    # Compute IRR for each project by calling its dynamic-pl endpoint
    rows: list[dict[str, Any]] = []
    for p in projects:
        pid = p.get("project_id")
        if not pid:
            continue
        pl = _http_json(f"/api/lines/{line}/projects/{pid}/dynamic-pl")
        if not pl:
            continue
        rows.append(
            {
                "project_id": pid,
                "name": pl.get("project_name") or p.get("name") or pid,
                "city": p.get("city", ""),
                "irr": pl.get("irr"),
                "net_margin": pl.get("net_margin"),
            }
        )
    rows.sort(key=lambda r: (r.get("irr") or -1), reverse=True)
    top = rows[:top_n]
    if not top:
        return MockAnswer(
            answer=f"{_line_label(line)}线 /projects 端点返回了项目,但每个项目的 dynamic-pl 都失败了。",
            intent="irr_top",
            confidence=_confidence("irr_top") * 0.5,
        )
    best = top[0]
    avg_irr = sum((r.get("irr") or 0) for r in top) / len(top)
    summary = f"在{_line_label(line)}线下,IRR 最高的 {len(top)} 个项目平均 IRR 为 {avg_irr*100:.1f}%。"
    summary += f"其中最高的是 {best['name']} ({best['city']}),IRR = {(best.get('irr') or 0)*100:.1f}%。"
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in top:
        irr_pct = (r.get("irr") or 0) * 100
        bullets.append(
            f"- {r['name']} ({r['city']}): IRR = {irr_pct:.1f}%, 净利率 {(r.get('net_margin') or 0)*100:.1f}%"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /projects/{r['project_id']}/dynamic-pl",
                title=f"{r['project_id']} {r['name']}",
                snippet=f"IRR={irr_pct:.2f}%, 净利率={(r.get('net_margin') or 0)*100:.2f}%",
                url=f"/{line}/dynamic-pl?focus={r['project_id']}",
            )
        )
    answer = summary + "\n" + "\n".join(bullets)
    chart = {
        "type": "bar",
        "title": f"{_line_label(line)} IRR Top {len(top)}",
        "categories": [r["name"] for r in top],
        "values": [round((r.get("irr") or 0) * 100, 2) for r in top],
        "yAxisLabel": "IRR (%)",
    }
    return MockAnswer(
        answer=answer,
        intent="irr_top",
        confidence=_confidence("irr_top"),
        citations=citations,
        chart_data=chart,
    )


def intent_residential_payment_low(line: str, top_n: int, **_: Any) -> MockAnswer:
    """回款完成率低的 N 个项目（适用于任何业务线）。"""
    if not line:
        line = "residential"
    data = _http_json(f"/api/lines/{line}/projects")
    if not data or not data.get("projects"):
        return MockAnswer(
            answer=f"未能从{_line_label(line)}线 /projects 端点获取项目数据。",
            intent="payment_low",
            confidence=_confidence("payment_low") * 0.5,
        )
    projects = data["projects"]
    rows: list[dict[str, Any]] = []
    for p in projects:
        pid = p.get("project_id")
        if not pid:
            continue
        pay = _http_json(f"/api/lines/{line}/projects/{pid}/payment")
        if not pay:
            continue
        rows.append(
            {
                "project_id": pid,
                "name": pay.get("project_name") or p.get("name") or pid,
                "city": p.get("city", ""),
                "payment_completion": pay.get("payment_completion"),
                "monthly_vs_plan": pay.get("monthly_payment_vs_plan"),
            }
        )
    rows.sort(key=lambda r: r.get("payment_completion") or 1.0)
    bottom = rows[:top_n]
    if not bottom:
        return MockAnswer(
            answer=f"{_line_label(line)}线 /projects 返回了项目,但无法获取 payment 详情。",
            intent="payment_low",
            confidence=_confidence("payment_low") * 0.5,
        )
    worst = bottom[0]
    summary = (
        f"在{_line_label(line)}线下,回款完成率最低的 {len(bottom)} 个项目是:\n"
    )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in bottom:
        pc = (r.get("payment_completion") or 0) * 100
        mv = (r.get("monthly_vs_plan") or 0) * 100
        bullets.append(
            f"- {r['name']} ({r['city']}): 回款完成率 {pc:.1f}%, 当月回款/计划 {mv:.1f}%"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /projects/{r['project_id']}/payment",
                title=f"{r['project_id']} {r['name']}",
                snippet=f"回款完成率={pc:.2f}%, 当月回款/计划={(r.get('monthly_vs_plan') or 0)*100:.2f}%",
                url=f"/{line}/payment?focus={r['project_id']}",
            )
        )
    answer = summary + "\n".join(bullets) + (
        f"\n其中回款最差的是 {worst['name']},回款完成率仅 {(worst.get('payment_completion') or 0)*100:.1f}%,"
        f"建议核查其当月签约情况与回款节奏。"
    )
    chart = {
        "type": "bar",
        "title": "住宅回款完成率 (低 → 高)",
        "categories": [r["name"] for r in bottom],
        "values": [round((r.get("payment_completion") or 0) * 100, 2) for r in bottom],
        "yAxisLabel": "回款完成率 (%)",
    }
    return MockAnswer(
        answer=answer,
        intent="payment_low",
        confidence=_confidence("payment_low"),
        citations=citations,
        chart_data=chart,
    )


def intent_residential_redlines(line: str, top_n: int, **_: Any) -> MockAnswer:
    """三道红线触发情况（适用于任何业务线）。"""
    if not line:
        line = "residential"
    data = _http_json(f"/api/lines/{line}/projects")
    if not data or not data.get("projects"):
        return MockAnswer(
            answer=f"未能从{_line_label(line)}线 /projects 端点获取项目数据。",
            intent="redlines",
            confidence=_confidence("redlines") * 0.5,
        )
    projects = data["projects"]
    rows: list[dict[str, Any]] = []
    for p in projects:
        pid = p.get("project_id")
        if not pid:
            continue
        rd = _http_json(f"/api/lines/{line}/projects/{pid}/redlines")
        if not rd:
            continue
        rows.append(
            {
                "project_id": pid,
                "name": rd.get("project_name") or p.get("name") or pid,
                "city": p.get("city", ""),
                "alr": rd.get("asset_liability_ratio"),
                "ndr": rd.get("net_debt_ratio"),
                "csd": rd.get("cash_to_short_debt"),
                "status": rd.get("status", {}),
            }
        )
    # Triggered = at least one threshold in status is "red"
    def _triggered(r: dict[str, Any]) -> int:
        st = r.get("status") or {}
        return sum(1 for v in st.values() if v == "red")

    rows.sort(key=_triggered, reverse=True)
    flagged = [r for r in rows if _triggered(r) > 0]
    chosen = flagged[:top_n] if flagged else rows[:top_n]
    if not chosen:
        return MockAnswer(
            answer="住宅项目均无三道红线数据。",
            intent="redlines",
            confidence=_confidence("redlines") * 0.5,
        )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in chosen:
        st = r.get("status") or {}
        flags = [k for k, v in st.items() if v == "red"]
        flag_str = ",".join(flags) if flags else "全部绿档"
        bullets.append(
            f"- {r['name']} ({r['city']}): 资产/净负债/短债现金比 "
            f"{(r.get('alr') or 0)*100:.1f}% / {(r.get('ndr') or 0)*100:.1f}% / {r.get('csd') or 0:.2f}x "
            f"——{flag_str}"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /projects/{r['project_id']}/redlines",
                title=f"{r['project_id']} {r['name']}",
                snippet=(
                    f"alr={r.get('alr')}, ndr={r.get('ndr')}, csd={r.get('csd')}, "
                    f"status={r.get('status')}"
                ),
                url=f"/{line}/redlines?focus={r['project_id']}",
            )
        )
    if flagged:
        answer = (
            f"{_line_label(line)}线下,有 {len(flagged)} 个项目触发了至少一道三道红线阈值。\n"
            + "\n".join(bullets)
            + "\n\n按监管阈值:资产/净负债/现金短债比 应 <70% / <100% / ≥1.0x。"
        )
    else:
        answer = (
            f"{_line_label(line)}线下,所有项目三道红线均处于绿档。\n"
            + "\n".join(bullets)
        )
    chart = {
        "type": "bar",
        "title": "住宅项目三道红线触发数 (高 → 低)",
        "categories": [r["name"] for r in chosen],
        "values": [_triggered(r) for r in chosen],
        "yAxisLabel": "触发红线数",
    }
    return MockAnswer(
        answer=answer,
        intent="redlines",
        confidence=_confidence("redlines"),
        citations=citations,
        chart_data=chart,
    )


def intent_residential_dedup_low(line: str, top_n: int, **_: Any) -> MockAnswer:
    """去化速度（月度去化率）最低的 N 个项目（适用于任何业务线）。"""
    if not line:
        line = "residential"
    data = _http_json(f"/api/lines/{line}/projects")
    if not data or not data.get("projects"):
        return MockAnswer(
            answer=f"未能从{_line_label(line)}线 /projects 端点获取项目数据。",
            intent="dedup_low",
            confidence=_confidence("dedup_low") * 0.5,
        )
    projects = data["projects"]
    rows: list[dict[str, Any]] = []
    for p in projects:
        pid = p.get("project_id")
        if not pid:
            continue
        pl = _http_json(f"/api/lines/{line}/projects/{pid}/dynamic-pl")
        if not pl:
            continue
        rows.append(
            {
                "project_id": pid,
                "name": pl.get("project_name") or p.get("name") or pid,
                "city": p.get("city", ""),
                "monthly_dedup_rate": pl.get("monthly_dedup_rate"),
            }
        )
    rows.sort(key=lambda r: r.get("monthly_dedup_rate") or 1.0)
    bottom = rows[:top_n]
    if not bottom:
        return MockAnswer(
            answer=f"{_line_label(line)}线 /projects 返回了项目,但 /dynamic-pl 全部失败。",
            intent="dedup_low",
            confidence=_confidence("dedup_low") * 0.5,
        )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in bottom:
        d = r.get("monthly_dedup_rate") or 0
        bullets.append(
            f"- {r['name']} ({r['city']}): 月度去化率 {d*100:.1f}%"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /projects/{r['project_id']}/dynamic-pl",
                title=f"{r['project_id']} {r['name']}",
                snippet=f"monthly_dedup_rate={d}",
                url=f"/{line}/dedup-forecast?focus={r['project_id']}",
            )
        )
    answer = (
        f"{_line_label(line)}线下,月度去化率最低的 {len(bottom)} 个项目是:\n"
        + "\n".join(bullets)
        + "\n\n去化率持续走低可能预示现金流回款节奏放缓,建议结合敏感性 Lab 做去化速度扰动分析。"
    )
    chart = {
        "type": "bar",
        "title": "住宅月度去化率 (低 → 高)",
        "categories": [r["name"] for r in bottom],
        "values": [round((r.get("monthly_dedup_rate") or 0) * 100, 2) for r in bottom],
        "yAxisLabel": "月度去化率 (%)",
    }
    return MockAnswer(
        answer=answer,
        intent="dedup_low",
        confidence=_confidence("dedup_low"),
        citations=citations,
        chart_data=chart,
    )


# ---------------------------------------------------------------------------
# Intents — retail
# ---------------------------------------------------------------------------


def intent_retail_noi_top(line: str, top_n: int, **_: Any) -> MockAnswer:
    """零售 NOI top N 物业。"""
    if line != "retail":
        line = "retail"
    data = _http_json(f"/api/lines/{line}/properties")
    if not data or not data.get("items"):
        return MockAnswer(
            answer="未能从零售线 /properties 端点获取物业数据。",
            intent="noi_top",
            confidence=_confidence("noi_top") * 0.5,
        )
    items = data["items"]
    rows = [
        {
            "property_id": p.get("property_id"),
            "name": p.get("name") or p.get("property_id"),
            "city": p.get("city", ""),
            "noi": p.get("noi_wan"),
            "efficiency": (p.get("headline_kpis") or {}).get("efficiency"),
            "vacancy": p.get("vacancy_rate"),
        }
        for p in items
    ]
    rows.sort(key=lambda r: r.get("noi") or 0, reverse=True)
    top = rows[:top_n]
    if not top:
        return MockAnswer(
            answer="零售 /properties 返回空。",
            intent="noi_top",
            confidence=_confidence("noi_top") * 0.5,
        )
    best = top[0]
    summary = (
        f"在零售线下,NOI 最高的 {len(top)} 个物业是:"
    )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in top:
        noi = r.get("noi") or 0
        eff = r.get("efficiency") or 0
        bullets.append(
            f"- {r['name']} ({r['city']}): NOI {noi:.0f} 万元, 坪效 {eff:.2f} 元/㎡/月, "
            f"空置率 {(r.get('vacancy') or 0)*100:.1f}%"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /properties/{r['property_id']}/noi-waterfall",
                title=f"{r['property_id']} {r['name']}",
                snippet=f"NOI={noi} 万元, 坪效={eff} 元/㎡/月",
                url=f"/{line}/noi?focus={r['property_id']}",
            )
        )
    answer = (
        f"{summary}\n" + "\n".join(bullets)
        + f"\n\n其中 NOI 最高的是 {best['name']},NOI 达 {(best.get('noi') or 0):.0f} 万元。"
    )
    chart = {
        "type": "bar",
        "title": f"零售 NOI Top {len(top)}",
        "categories": [r["name"] for r in top],
        "values": [round(r.get("noi") or 0, 0) for r in top],
        "yAxisLabel": "NOI (万元)",
    }
    return MockAnswer(
        answer=answer,
        intent="noi_top",
        confidence=_confidence("noi_top"),
        citations=citations,
        chart_data=chart,
    )


def intent_retail_renovation(line: str, top_n: int, **_: Any) -> MockAnswer:
    """零售调改 NPV 为正的项目。"""
    if line != "retail":
        line = "retail"
    data = _http_json(f"/api/lines/{line}/properties")
    if not data or not data.get("items"):
        return MockAnswer(
            answer="未能从零售线 /properties 端点获取物业数据。",
            intent="renovation",
            confidence=_confidence("renovation") * 0.5,
        )
    items = data["items"]
    rows: list[dict[str, Any]] = []
    for p in items:
        pid = p.get("property_id")
        if not pid:
            continue
        rn = _http_json(f"/api/lines/{line}/properties/{pid}/renovation-npv")
        if not rn:
            continue
        delta = (rn.get("delta") or {}).get("npv_wan")
        rows.append(
            {
                "property_id": pid,
                "name": rn.get("property_name") or p.get("name") or pid,
                "city": p.get("city", ""),
                "delta_npv": delta,
                "renovate_npv": (rn.get("renovate") or {}).get("npv_wan"),
                "renovate_irr": (rn.get("renovate") or {}).get("irr"),
            }
        )
    # Keep positive deltas first, sorted desc
    rows.sort(key=lambda r: r.get("delta_npv") if r.get("delta_npv") is not None else -1e18, reverse=True)
    positive = [r for r in rows if (r.get("delta_npv") or 0) > 0]
    chosen = positive[:top_n] if positive else rows[:top_n]
    if not chosen:
        return MockAnswer(
            answer="零售 /properties 端点返回了物业,但 renovation-npv 都失败了。",
            intent="renovation",
            confidence=_confidence("renovation") * 0.5,
        )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in chosen:
        d = r.get("delta_npv") or 0
        rnpv = r.get("renovate_npv") or 0
        rirr = r.get("renovate_irr")
        rirr_s = f"{rirr*100:.1f}%" if isinstance(rirr, (int, float)) else "N/A"
        bullets.append(
            f"- {r['name']} ({r['city']}): 调改 NPV {rnpv:.0f} 万元 (vs 维持,差额 {d:+.0f}), 调改 IRR {rirr_s}"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /properties/{r['property_id']}/renovation-npv",
                title=f"{r['property_id']} {r['name']}",
                snippet=f"调改 NPV={rnpv}, 差额 NPV={d}, 调改 IRR={rirr}",
                url=f"/{line}/renovation-npv?focus={r['property_id']}",
            )
        )
    if positive:
        answer = (
            f"零售线下,有 {len(positive)} 个物业的调改 NPV 高于维持方案。\n"
            + "\n".join(bullets)
            + "\n\n建议资本性决策时优先考虑这些项目。"
        )
    else:
        answer = (
            "零售线下,所有物业的调改 NPV 均不高于维持方案,建议暂缓调改。\n"
            + "\n".join(bullets)
        )
    chart = {
        "type": "bar",
        "title": "零售调改 vs 维持 NPV 差额",
        "categories": [r["name"] for r in chosen],
        "values": [round(r.get("delta_npv") or 0, 0) for r in chosen],
        "yAxisLabel": "差额 NPV (万元)",
    }
    return MockAnswer(
        answer=answer,
        intent="renovation",
        confidence=_confidence("renovation"),
        citations=citations,
        chart_data=chart,
    )


def intent_retail_collection(line: str, top_n: int, threshold: float | None, **_: Any) -> MockAnswer:
    """零售收缴率 < threshold 的物业。默认阈值 95%。"""
    if line != "retail":
        line = "retail"
    th = threshold if threshold is not None else 0.95
    data = _http_json(f"/api/lines/{line}/properties")
    if not data or not data.get("items"):
        return MockAnswer(
            answer="未能从零售线 /properties 端点获取物业数据。",
            intent="collection",
            confidence=_confidence("collection") * 0.5,
        )
    items = data["items"]
    rows = [
        {
            "property_id": p.get("property_id"),
            "name": p.get("name") or p.get("property_id"),
            "city": p.get("city", ""),
            "collection_rate": p.get("collection_rate"),
        }
        for p in items
    ]
    rows.sort(key=lambda r: r.get("collection_rate") or 1.0)
    below = [r for r in rows if (r.get("collection_rate") or 1.0) < th]
    chosen = below[:top_n] if below else rows[:top_n]
    if not chosen:
        return MockAnswer(
            answer=f"零售线下所有物业收缴率均 ≥ {th*100:.0f}%,运营良好。",
            intent="collection",
            confidence=_confidence("collection"),
        )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in chosen:
        cr = (r.get("collection_rate") or 0) * 100
        bullets.append(
            f"- {r['name']} ({r['city']}): 收缴率 {cr:.1f}% (阈值 {th*100:.0f}%)"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /properties/{r['property_id']}/collection-rate",
                title=f"{r['property_id']} {r['name']}",
                snippet=f"当前收缴率={cr:.2f}%, 阈值={th*100:.0f}%",
                url=f"/{line}/collection?focus={r['property_id']}",
            )
        )
    answer = (
        f"零售线下,有 {len(below)} 个物业的收缴率 < {th*100:.0f}% (阈值)。"
        f"按降序展示前 {len(chosen)} 个:\n"
        + "\n".join(bullets)
        + "\n\n建议:对收缴率持续走低的物业启动催收流程或续约谈判。"
    )
    chart = {
        "type": "bar",
        "title": "零售收缴率 (< 阈值)",
        "categories": [r["name"] for r in chosen],
        "values": [round((r.get("collection_rate") or 0) * 100, 2) for r in chosen],
        "yAxisLabel": "收缴率 (%)",
    }
    return MockAnswer(
        answer=answer,
        intent="collection",
        confidence=_confidence("collection"),
        citations=citations,
        chart_data=chart,
    )


# ---------------------------------------------------------------------------
# Intents — retail-leasing
# ---------------------------------------------------------------------------


def intent_leasing_vacancy(line: str, top_n: int, **_: Any) -> MockAnswer:
    """零售租赁空置期 top N (空置最长的业主)。"""
    if line != "retail-leasing":
        line = "retail-leasing"
    data = _http_json(f"/api/lines/{line}/properties")
    if not data or not data.get("items"):
        return MockAnswer(
            answer="未能从零售租赁线 /properties 端点获取物业数据。",
            intent="vacancy",
            confidence=_confidence("vacancy") * 0.5,
        )
    items = data["items"]
    rows = [
        {
            "property_id": p.get("property_id"),
            "name": p.get("name") or p.get("property_id"),
            "owner": p.get("owner", ""),
            "owner_vacancy_days": p.get("owner_vacancy_days"),
            "city": p.get("city", ""),
        }
        for p in items
    ]
    rows.sort(key=lambda r: r.get("owner_vacancy_days") or 0, reverse=True)
    top = rows[:top_n]
    if not top:
        return MockAnswer(
            answer="零售租赁 /properties 返回空。",
            intent="vacancy",
            confidence=_confidence("vacancy") * 0.5,
        )
    worst = top[0]
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in top:
        d = r.get("owner_vacancy_days") or 0
        bullets.append(
            f"- {r['name']} ({r['city']}, 业主 {r['owner']}): 空置期 {d} 天"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /properties",
                title=f"{r['property_id']} {r['name']}",
                snippet=f"owner={r['owner']}, owner_vacancy_days={d}",
                url=f"/{line}/vacancy-alert",
            )
        )
    answer = (
        f"零售租赁线下,业主空置期最长的 {len(top)} 个物业是:\n"
        + "\n".join(bullets)
        + f"\n\n其中 {worst['name']} (业主 {worst['owner']}) 空置期达 {worst.get('owner_vacancy_days')} 天,建议优先关注。"
    )
    chart = {
        "type": "bar",
        "title": "零售租赁空置期 Top N",
        "categories": [r["name"] for r in top],
        "values": [r.get("owner_vacancy_days") or 0 for r in top],
        "yAxisLabel": "空置期 (天)",
    }
    return MockAnswer(
        answer=answer,
        intent="vacancy",
        confidence=_confidence("vacancy"),
        citations=citations,
        chart_data=chart,
    )


def intent_leasing_benchmark(line: str, top_n: int, **_: Any) -> MockAnswer:
    """零售租赁竞品基准差最大的 N 个物业 (按绝对值)。"""
    if line != "retail-leasing":
        line = "retail-leasing"
    data = _http_json(f"/api/lines/{line}/market-benchmark")
    if not data or not data.get("items"):
        return MockAnswer(
            answer="未能从零售租赁线 /market-benchmark 端点获取对标数据。",
            intent="benchmark",
            confidence=_confidence("benchmark") * 0.5,
        )
    items = data["items"]
    rows = [
        {
            "property_id": p.get("property_id"),
            "name": p.get("property_name") or p.get("property_id"),
            "city": p.get("city", ""),
            "gap_pct": p.get("benchmark_gap_pct"),
            "deal_rent": p.get("deal_rent"),
            "benchmark": p.get("comparable_median"),
        }
        for p in items
    ]
    rows.sort(key=lambda r: abs(r.get("gap_pct") or 0), reverse=True)
    top = rows[:top_n]
    if not top:
        return MockAnswer(
            answer="零售租赁 /market-benchmark 返回空。",
            intent="benchmark",
            confidence=_confidence("benchmark") * 0.5,
        )
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for r in top:
        gap = (r.get("gap_pct") or 0) * 100
        bullets.append(
            f"- {r['name']} ({r['city']}): 基准差 {gap:+.1f}% (成交 {r.get('deal_rent')} vs 基准 {r.get('benchmark')})"
        )
        citations.append(
            _c(
                source=f"business_lines/{line}/api/router.py:GET /market-benchmark",
                title=f"{r['property_id']} {r['name']}",
                snippet=f"benchmark_gap_pct={gap:.2f}%, deal_rent={r.get('deal_rent')}, benchmark={r.get('benchmark')}",
                url=f"/{line}/market-report?focus={r['property_id']}",
            )
        )
    answer = (
        f"零售租赁线下,相对竞品基准偏离最大的 {len(top)} 个物业是:\n"
        + "\n".join(bullets)
        + "\n\n正值=成交价高于基准,负值=低于基准。绝对值越大,议价空间越显著。"
    )
    chart = {
        "type": "bar",
        "title": "零售租赁基准差 (|gap|)",
        "categories": [r["name"] for r in top],
        "values": [round((r.get("gap_pct") or 0) * 100, 2) for r in top],
        "yAxisLabel": "基准差 (%)",
    }
    return MockAnswer(
        answer=answer,
        intent="benchmark",
        confidence=_confidence("benchmark"),
        citations=citations,
        chart_data=chart,
    )


# ---------------------------------------------------------------------------
# Intents — cross-line & generic
# ---------------------------------------------------------------------------


def intent_cross_overview(line: str | None, top_n: int, **_: Any) -> MockAnswer:
    """三业务线 KPI 概览对比。"""
    # Pull registry to know which lines exist
    reg = _http_json("/api/registry/lines")
    if not reg or not reg.get("lines"):
        return MockAnswer(
            answer="未能从 /api/registry/lines 端点获取业务线清单。",
            intent="cross_overview",
            confidence=_confidence("cross_overview") * 0.5,
        )
    lines = [l for l in reg["lines"] if l.get("id") not in ("my-line",)]
    bullets: list[str] = []
    citations: list[dict[str, Any]] = []
    for l in lines:
        lid = l.get("id")
        api_prefix = l.get("api_prefix", f"/api/lines/{lid}")
        ind = _http_json(f"{api_prefix}/indicators")
        if not ind:
            bullets.append(f"- {l.get('name') or lid}: 无 indicators 数据")
            continue
        # Pick the first 2 indicators as a teaser. The /indicators response
        # may or may not include a `value` field (residential aggregates
        # values; retail/retail-leasing return definitions only). Handle
        # both shapes.
        items = (ind.get("indicators") or [])[:2]
        teaser_parts: list[str] = []
        for it in items:
            name = it.get("title") or it.get("id")
            unit = it.get("unit") or ""
            val = it.get("value")
            if val is None:
                teaser_parts.append(f"{name} ({unit or '—'})")
            else:
                teaser_parts.append(f"{name}: {val} {unit}".strip())
        teaser = "; ".join(teaser_parts) if teaser_parts else "无指标"
        bullets.append(f"- {l.get('name') or lid}: {teaser}")
        citations.append(
            _c(
                source=f"business_lines/{lid}/api/router.py:GET /indicators",
                title=f"{lid} indicators",
                snippet=f"{len(ind.get('indicators') or [])} 项指标",
                url=f"/{lid}",
            )
        )
    answer = "三业务线 KPI 概览(每线取前 2 项指标):\n" + "\n".join(bullets)
    return MockAnswer(
        answer=answer,
        intent="cross_overview",
        confidence=_confidence("cross_overview"),
        citations=citations,
    )


def intent_line_indicators(line: str | None, top_n: int, **_: Any) -> MockAnswer:
    """指定业务线的指标库。"""
    if not line:
        return MockAnswer(
            answer="请告诉我您想看哪个业务线的指标(住宅 / 零售 / 零售租赁)。",
            intent="line_indicators",
            confidence=_confidence("line_indicators") * 0.6,
        )
    data = _http_json(f"/api/lines/{line}/indicators")
    if not data:
        return MockAnswer(
            answer=f"未能从 {line} 线 /indicators 端点获取数据。",
            intent="line_indicators",
            confidence=_confidence("line_indicators") * 0.5,
        )
    items = data.get("indicators") or []
    if not items:
        return MockAnswer(
            answer=f"{line} 线 /indicators 端点返回了 0 个指标。",
            intent="line_indicators",
            confidence=_confidence("line_indicators") * 0.5,
        )
    bullets = [
        f"- {it.get('title') or it.get('id')}: 单位 {it.get('unit') or '—'}, "
        f"format={it.get('format') or 'number'}, source={it.get('source') or '—'}"
        for it in items
    ]
    return MockAnswer(
        answer=(
            f"{line} 线的指标库共 {len(items)} 项:\n"
            + "\n".join(bullets)
        ),
        intent="line_indicators",
        confidence=_confidence("line_indicators"),
        citations=[
            _c(
                source=f"business_lines/{line}/api/router.py:GET /indicators",
                title=f"{line} indicators",
                snippet=f"{len(items)} 项指标",
                url=f"/{line}",
            )
        ],
    )


def intent_sensitivity(line: str | None, top_n: int, **_: Any) -> MockAnswer:
    """做一份敏感性分析。"""
    target = line or "residential"
    prof = _http_json(f"/api/sensitivity/profiles/{target}")
    if not prof:
        return MockAnswer(
            answer=(
                f"{target} 暂无 sensitivity.yaml。请在 "
                f"`business_lines/{target}/sensitivity.yaml` 添加 inputs/outputs 后重试。"
            ),
            intent="sensitivity",
            confidence=_confidence("sensitivity") * 0.5,
        )
    inputs = prof.get("inputs") or []
    outputs = prof.get("outputs") or []
    if not inputs or not outputs:
        return MockAnswer(
            answer=f"{target} 的 sensitivity profile 缺少 inputs 或 outputs。",
            intent="sensitivity",
            confidence=_confidence("sensitivity") * 0.5,
        )
    in1 = inputs[0]
    out = outputs[0]
    # Run a quick 1D analysis with default range
    body = {
        "line_id": target,
        "output_id": out["id"],
        "input1_id": in1["id"],
        "input2_id": None,
        "input1_range": in1.get("default_range") or [-0.10, 0.10],
        "input1_step": in1.get("default_step") or 0.02,
    }
    # The mock backend uses urllib not httpx, so we can't reuse _http_json
    # for POST. Do a quick raw POST.
    try:
        req = urllib.request.Request(
            f"{API_BASE}/api/sensitivity/analyze",
            data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return MockAnswer(
            answer=(
                f"已为 {target} 找到 sensitivity profile "
                f"({len(inputs)} inputs × {len(outputs)} outputs),"
                f"但调用 /api/sensitivity/analyze 失败: {exc}。"
            ),
            intent="sensitivity",
            confidence=_confidence("sensitivity") * 0.6,
        )
    base = result.get("base_value")
    matrix = result.get("matrix") or []
    label = out.get("name") or out["id"]
    answer = (
        f"已为 {target} 的 {label} 做了一份 1D 敏感性分析,"
        f"扫描变量 {in1.get('name') or in1['id']} "
        f"在 [{in1.get('default_range', [-0.10, 0.10])[0]*100:+.0f}%, "
        f"{in1.get('default_range', [-0.10, 0.10])[1]*100:+.0f}%] 区间。\n"
        f"基准值 = {base:.4f}。"
    )
    if matrix:
        flat = matrix[0]
        col_labels = (result.get("matrix_labels") or {}).get("col_labels") or []
        if col_labels and len(col_labels) == len(flat):
            lo = min(flat)
            hi = max(flat)
            lo_lbl = col_labels[flat.index(lo)]
            hi_lbl = col_labels[flat.index(hi)]
            answer += (
                f"\n最坏情形 {lo_lbl} → {label} = {lo:.4f};"
                f"最佳情形 {hi_lbl} → {label} = {hi:.4f}。"
                f"建议打开 /sensitivity 页面交互调整输入区间。"
            )
    return MockAnswer(
        answer=answer,
        intent="sensitivity",
        confidence=_confidence("sensitivity"),
        citations=[
            _c(
                source="apps/api/app/services/sensitivity_engine.py:analyze",
                title=f"{target} sensitivity profile",
                snippet=f"{len(inputs)} inputs, {len(outputs)} outputs",
                url=f"/sensitivity?line={target}",
            )
        ],
    )


def intent_fallback(line: str | None, top_n: int, raw_question: str, **_: Any) -> MockAnswer:
    """不理解的提问:返回友好提示 + 推荐问题。"""
    suggestions = [
        "住宅 IRR 最高的 3 个项目",
        "零售 NOI top 3",
        "三道红线触发的住宅项目",
        "收缴率低于 95% 的零售物业",
        "空置期最长的零售租赁业主",
        "做一份敏感性分析",
    ]
    answer = (
        f"抱歉,我没能完全理解 \"{raw_question}\" 的意图。\n"
        "您可以试试以下问题:\n"
        + "\n".join(f"  · {s}" for s in suggestions)
    )
    if line:
        answer += f"\n\n(检测到业务线: {line},已自动限定搜索范围)"
    return MockAnswer(
        answer=answer,
        intent="fallback_unknown",
        confidence=_confidence("fallback_unknown"),
        citations=[
            _c(
                source="apps/api/app/services/copilot_engine.py:fallback",
                title="推荐问题",
                snippet="; ".join(suggestions[:3]),
                url=None,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


_DISPATCH: dict[str, Callable[..., MockAnswer]] = {
    "irr_top": intent_residential_irr_top,
    "payment_low": intent_residential_payment_low,
    "redlines": intent_residential_redlines,
    "dedup_low": intent_residential_dedup_low,
    "noi_top": intent_retail_noi_top,
    "renovation": intent_retail_renovation,
    "collection": intent_retail_collection,
    "vacancy": intent_leasing_vacancy,
    "benchmark": intent_leasing_benchmark,
    "cross_overview": intent_cross_overview,
    "line_indicators": intent_line_indicators,
    "sensitivity": intent_sensitivity,
    "compare": intent_cross_overview,
}


def dispatch(
    *,
    intent: str,
    line: str | None,
    top_n: int,
    threshold: float | None,
    raw_question: str,
) -> MockAnswer:
    """Dispatch to the right intent function.

    Unknown intents fall back to the friendly "didn't understand" handler.
    `line` may be empty string when the parser found no line; we
    normalize to None so handlers see a consistent shape.
    """
    fn = _DISPATCH.get(intent)
    normalized_line = line if line else None
    if fn is None:
        return intent_fallback(line=normalized_line, top_n=top_n, raw_question=raw_question)
    return fn(line=normalized_line or "", top_n=top_n, threshold=threshold, raw_question=raw_question)
