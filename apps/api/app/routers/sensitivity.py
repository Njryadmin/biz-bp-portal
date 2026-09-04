"""
apps/api/app/routers/sensitivity.py

Cross-business-line Sensitivity Lab router.

This router is NOT mounted via the business-line auto-discovery path
(see `app.routers.registry`); it is mounted at the API root by
`app.main` because the sensitivity engine is universal — it spans all
business lines.

Endpoints (mounted under /api/sensitivity by `app.main`):

* GET  /profiles                       — list all line profiles (summary)
                                        filtered by accessible_lines.
* GET  /profiles/{line_id}             — full profile for one line (RBAC)
* POST /analyze                        — run the analysis; returns
                                         SensitivityResult (RBAC: line
                                         access required)
* GET  /scenarios/{line_id}            — preset scenario examples (RBAC)

The engine itself lives in `app.services.sensitivity_engine`.

v1 → v2 升级 (2026-09-04): 用 ``check_domain_access(FINANCE, PROJECT)``
替代 v1 ``require_business_line`` (后者只判断 line 范围,不区分数据域).
敏感性分析涉及财务+项目指标,这两个域中任一允许即可 (any-of 语义).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.auth_v2 import CurrentUserV2, get_current_user_v2
from ..core.logging import get_logger
from ..core.rbac_v2 import (
    DataDomain,
    check_domain_access,
    filter_accessible_lines_v2,
)
from ..services.sensitivity_engine import (
    SensitivityProfile,
    SensitivityRequest,
    SensitivityResult,
    analyze,
    list_profiles,
    load_profile,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/sensitivity", tags=["sensitivity"])


# ─────────────────────────────────────────────────────────────────────────
# Profile endpoints
# ─────────────────────────────────────────────────────────────────────────


def _profile_summary(line_id: str) -> dict:
    p = load_profile(line_id)
    return {
        "line_id": p.line_id,
        "line_name": p.line_name or p.line_id,
        "input_count": len(p.inputs),
        "output_count": len(p.outputs),
        "inputs": [
            {
                "id": i.id,
                "name": i.name,
                "unit": i.unit,
                "default_range": i.default_range,
                "default_step": i.default_step,
                "description": i.description,
                "base_value_ref": i.base_value_ref,
            }
            for i in p.inputs
        ],
        "outputs": [
            {
                "id": o.id,
                "name": o.name,
                "unit": o.unit,
                "base_value_ref": o.base_value_ref,
            }
            for o in p.outputs
        ],
    }


@router.get(
    "/profiles",
    summary="List sensitivity profiles for business lines the user can access",
)
async def list_profiles_endpoint(
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    line_ids = list_profiles()
    allowed = filter_accessible_lines_v2(user, line_ids)
    return {
        "count": len(allowed),
        "profiles": [
            {
                "line_id": lid,
                **_profile_summary(lid),
            }
            for lid in allowed
        ],
    }


@router.get(
    "/profiles/{line_id}",
    summary="Get the full sensitivity profile for one business line (RBAC)",
)
async def get_profile_endpoint(
    line_id: str,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    # 敏感性 profile 涉及 finance + project 域,any-of 即可
    await check_domain_access(
        user, line_id, [DataDomain.FINANCE, DataDomain.PROJECT], write=False
    )
    try:
        p = load_profile(line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "line_id": p.line_id,
        "line_name": p.line_name or p.line_id,
        "inputs": [i.model_dump() for i in p.inputs],
        "outputs": [o.model_dump() for o in p.outputs],
    }


# ─────────────────────────────────────────────────────────────────────────
# Analyze endpoint
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/analyze",
    response_model=SensitivityResult,
    summary="Run a 1D or 2D sensitivity analysis (RBAC: line access required)",
)
async def analyze_endpoint(
    req: SensitivityRequest,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> SensitivityResult:
    # analyze 是写操作 (生成新分析结果),finance + project 写权限
    await check_domain_access(
        user, req.line_id, [DataDomain.FINANCE, DataDomain.PROJECT], write=True
    )
    try:
        profile = load_profile(req.line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return analyze(profile, req)
    except KeyError as exc:
        # Bad output/input id. Distinguish 400 (validation) from 404 (resource).
        msg = str(exc).strip("'")
        if msg in profile.output_ids() or msg in profile.input_ids():
            raise HTTPException(status_code=404, detail=f"unknown id in profile: {msg}") from exc
        raise HTTPException(status_code=400, detail=f"bad request: {msg}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ─────────────────────────────────────────────────────────────────────────
# Preset scenarios
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/scenarios/{line_id}",
    summary="Pre-canned scenario examples (worst / base / best) for each output",
)
async def scenarios_endpoint(
    line_id: str,
    user: CurrentUserV2 = Depends(get_current_user_v2),
) -> dict:
    # 读场景示例 — finance + project 域任一可读即可
    await check_domain_access(
        user, line_id, [DataDomain.FINANCE, DataDomain.PROJECT], write=False
    )
    try:
        p = load_profile(line_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # For each output, build a sample request using the first two inputs.
    if len(p.inputs) < 1:
        return {"line_id": line_id, "scenarios": []}
    in1 = p.inputs[0]
    in2 = p.inputs[1] if len(p.inputs) > 1 else p.inputs[0]

    out_examples: list[dict] = []
    for out in p.outputs:
        req = SensitivityRequest(
            line_id=line_id,
            output_id=out.id,
            input1_id=in1.id,
            input2_id=in2.id if in2.id != in1.id else None,
            input1_range=in1.default_range,
            input2_range=in2.default_range if in2.id != in1.id else [None, None],
            input1_step=in1.default_step,
            input2_step=in2.default_step if in2.id != in1.id else 0.05,
        )
        try:
            result = analyze(p, req)
            out_examples.append(
                {
                    "output_id": out.id,
                    "output_name": out.name,
                    "output_unit": out.unit,
                    "base_value": result.base_value,
                    "scenarios": [s.model_dump() for s in result.scenarios],
                }
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("preset scenario for %s.%s failed: %s", line_id, out.id, exc)
            out_examples.append(
                {
                    "output_id": out.id,
                    "output_name": out.name,
                    "error": str(exc),
                }
            )

    return {
        "line_id": line_id,
        "input1_id": in1.id,
        "input2_id": in2.id,
        "scenarios": out_examples,
    }
