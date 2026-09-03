"""
apps/api/app/services/sensitivity_engine.py

Universal Sensitivity Engine for the fin-bp-portal.

CONTRACT
========

A business line provides a `sensitivity.yaml` in its own directory, e.g.
`business_lines/<line_id>/sensitivity.yaml`. The file describes:

- `inputs`:  knobs the user can turn (price, cost, vacancy, ...). Each
  input has a `default_range` (relative perturbation, e.g. [-0.10, +0.10])
  and a `default_step` (relative increment for the sweep).
- `outputs`: KPIs to watch (IRR, NOI, occupancy, ...). Each output has
  `coefficients` mapping input_id -> absolute delta in the output's
  unit per +1.0 relative change in the input.

Given an `SensitivityRequest`, the engine returns:

- a 1D / 2D matrix of recomputed output values across the input sweep(s)
- a tornado ranking of inputs by absolute impact on the chosen output
- three canonical scenarios (worst / base / best)

The engine is INTENTIONALLY GENERIC:

- It never imports `business_lines/*`. It only reads
  `business_lines/<line>/sensitivity.yaml`.
- Adding a 5th business line requires only writing its own YAML.
- Coefficients are linear: `output = base + sum(coef * delta_input)`.
  This is an approximation but is fast, deterministic, and easy to audit.

BASE VALUES
===========

Base values for inputs and outputs are looked up at request time:

- `input.base_value_ref`  like "project.avg_price_per_sqm"
- `output.base_value_ref` like "kpi.dynamic_irr"

The ref is `table.field`. Supported tables: `project`, `property`, `kpi`.
The engine resolves these by calling the corresponding business-line API
endpoint over HTTP. If the line is not running, base_value falls back
to 0.0 (a clear failure signal — the matrix still computes correctly
around zero).

Clients can also pass `base_overrides: {output_id: float, ...}` to
manually pin base values for what-if analysis.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.logging import get_logger
from ..core.registry import get_project_root

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Pydantic DTOs
# ─────────────────────────────────────────────────────────────────────────


class SensitivityInput(BaseModel):
    """One input factor in a sensitivity profile."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    unit: str = ""
    base_value_ref: str = ""
    default_range: list[float] = Field(default_factory=lambda: [-0.10, 0.10])
    default_step: float = 0.02
    description: str = ""

    @field_validator("default_range")
    @classmethod
    def _check_range(cls, v: list[float]) -> list[float]:
        if len(v) != 2:
            raise ValueError("default_range must be a 2-element list [min, max]")
        if v[0] > v[1]:
            raise ValueError(f"default_range[0] must be <= default_range[1], got {v}")
        return v


class SensitivityOutput(BaseModel):
    """One output KPI in a sensitivity profile."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    unit: str = ""
    base_value_ref: str = ""
    coefficients: dict[str, float] = Field(default_factory=dict)


class SensitivityProfile(BaseModel):
    """The full sensitivity profile for a single business line."""

    model_config = ConfigDict(extra="allow")

    line_id: str
    line_name: str = ""
    inputs: list[SensitivityInput] = Field(default_factory=list)
    outputs: list[SensitivityOutput] = Field(default_factory=list)

    def input_ids(self) -> list[str]:
        return [i.id for i in self.inputs]

    def output_ids(self) -> list[str]:
        return [o.id for o in self.outputs]

    def find_input(self, input_id: str) -> SensitivityInput:
        for i in self.inputs:
            if i.id == input_id:
                return i
        raise KeyError(input_id)

    def find_output(self, output_id: str) -> SensitivityOutput:
        for o in self.outputs:
            if o.id == output_id:
                return o
        raise KeyError(output_id)


class SensitivityRequest(BaseModel):
    """POST body for /api/sensitivity/analyze."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    output_id: str
    input1_id: str
    # input2_id: optional. If absent, we compute a 1D tornado (rows are
    # single-element axes, but we still return a 2D-shaped matrix where
    # matrix[i][j] = output when input1 is at the j-th sweep position.
    input2_id: str | None = None
    input1_range: list[float] = Field(default_factory=lambda: [-0.10, 0.10])
    input2_range: list[float | None] = Field(default_factory=lambda: [None, None])
    input1_step: float = 0.02
    input2_step: float = 0.05
    # Optional manual overrides of base values, by OUTPUT id.
    # Useful for "what if our IRR target is 20% instead of 15%" scenarios.
    base_overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("input1_range")
    @classmethod
    def _check_input1_range(cls, v: list[float]) -> list[float]:
        if len(v) != 2 or v[0] > v[1]:
            raise ValueError("input1_range must be [min, max] with min <= max")
        return v


class TornadoBar(BaseModel):
    """One bar in the tornado chart."""

    input_id: str
    input_name: str
    value_at_min: float
    value_at_max: float
    span: float  # |value_at_max - value_at_min|
    direction: str  # "up" | "down" | "flat"


class Scenario(BaseModel):
    """A canned scenario (worst / base / best)."""

    name: str
    label: str
    input1_delta: float
    input2_delta: float
    output_value: float
    delta_from_base: float
    delta_pct: float  # % change relative to base (0 if base is 0)


class MatrixLabels(BaseModel):
    """Axis labels and names for the 2D matrix."""

    col_name: str  # input1 name
    row_name: str  # input2 name (or input1 again for 1D)
    col_labels: list[str]  # x-axis tick labels (formatted)
    row_labels: list[str]  # y-axis tick labels (formatted)
    col_var: str  # input1 id
    row_var: str  # input2 id (or input1 id for 1D)


class SensitivityResult(BaseModel):
    """Full result of one /analyze call."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    output_id: str
    output_name: str
    output_unit: str
    base_value: float
    base_inputs: dict[str, float] = Field(default_factory=dict)
    matrix: list[list[float]]
    matrix_labels: MatrixLabels
    tornado: list[TornadoBar]
    scenarios: list[Scenario]
    # Echoed metadata for downstream rendering.
    input_definitions: list[dict[str, Any]] = Field(default_factory=list)
    output_definitions: list[dict[str, Any]] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Profile loading
# ─────────────────────────────────────────────────────────────────────────


def _profile_path(line_id: str) -> Path:
    root = get_project_root()
    return root / "business_lines" / line_id / "sensitivity.yaml"


@lru_cache(maxsize=32)
def _load_profile_cached(line_id: str) -> SensitivityProfile:
    """Load a single profile, cached in process for fast repeated calls."""
    return _load_profile_uncached(line_id)


def _load_profile_uncached(line_id: str) -> SensitivityProfile:
    """Read `business_lines/<line_id>/sensitivity.yaml` and validate."""
    path = _profile_path(line_id)
    if not path.exists():
        raise FileNotFoundError(
            f"sensitivity profile not found for line_id={line_id!r} "
            f"(expected at {path}). Add a sensitivity.yaml under "
            f"business_lines/{line_id}/ to enable sensitivity analysis."
        )
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"sensitivity.yaml for {line_id} must be a mapping, got {type(raw)}")
    # If line_id is missing in the YAML, fill it from the path's directory name.
    raw.setdefault("line_id", line_id)
    return SensitivityProfile.model_validate(raw)


def load_profile(line_id: str) -> SensitivityProfile:
    """Public load entry point. Cached per-process via lru_cache."""
    return _load_profile_cached(line_id)


def list_profiles() -> list[str]:
    """Return the line ids of all business lines that have a sensitivity.yaml."""
    root = get_project_root()
    bl = root / "business_lines"
    if not bl.exists():
        return []
    out: list[str] = []
    for d in sorted(bl.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        if (d / "sensitivity.yaml").exists():
            out.append(d.name)
    return out


def clear_profile_cache() -> None:
    """Test helper: clear the lru_cache so changes to sensitivity.yaml
    on disk are picked up without restarting the process."""
    _load_profile_cached.cache_clear()


# ─────────────────────────────────────────────────────────────────────────
# Base value resolution
# ─────────────────────────────────────────────────────────────────────────


def _resolve_base_value(ref: str, line_id: str) -> float:
    """Best-effort fetch of a base value from the line's API.

    The ref format is `<table>.<field>`. Supported tables:
      - `kpi.<id>`       → `GET /api/lines/<line>/indicators`, find by id
      - `project.<field>`→ `GET /api/lines/<line>/projects`, average
      - `property.<field>`→ `GET /api/lines/<line>/properties`, average

    If anything fails (line not running, no data, ...), returns 0.0.
    The function NEVER raises — sensitivity analysis must degrade
    gracefully because the engine is universal and shouldn't know
    what every line's API looks like.
    """
    if not ref or "." not in ref:
        return 0.0
    table, _, field = ref.partition(".")
    base = os.environ.get("FIN_BP_API_BASE", "http://localhost:8769")
    try:
        if table == "kpi":
            url = f"{base}/api/lines/{line_id}/indicators"
        elif table == "project":
            url = f"{base}/api/lines/{line_id}/projects"
        elif table == "property":
            url = f"{base}/api/lines/{line_id}/properties"
        else:
            return 0.0
        # Use urllib instead of httpx to keep the engine import-light.
        # Short timeout — sensitivity analysis must degrade gracefully
        # when the line's API isn't running.
        import json
        import urllib.request

        with urllib.request.urlopen(url, timeout=0.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if table == "kpi":
            for it in data.get("indicators", []):
                if it.get("indicator_id") == field or it.get("id") == field:
                    v = it.get("value")
                    return float(v) if v is not None else 0.0
            return 0.0
        # project / property: average the field across the list.
        items = data.get("projects") or data.get("items") or data.get("properties") or []
        vals: list[float] = []
        for it in items:
            v = it.get(field)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        if not vals:
            return 0.0
        return sum(vals) / len(vals)
    except Exception as exc:  # noqa: BLE001 — universal engine never raises
        logger.debug("base_value lookup for %s.%s on line %s failed: %s", table, field, line_id, exc)
        return 0.0


# ─────────────────────────────────────────────────────────────────────────
# Math helpers
# ─────────────────────────────────────────────────────────────────────────


def _linspace(lo: float, hi: float, step: float) -> list[float]:
    """Inclusive linspace with a positive step. Guarantees at least 2 points."""
    if step <= 0:
        return [lo, hi]
    n = max(2, int(round((hi - lo) / step)) + 1)
    if n < 2:
        return [lo, hi]
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def _fmt_pct(x: float) -> str:
    """Format a relative perturbation as a label (e.g. '+10%', '-5%')."""
    return f"{x * 100:+.0f}%"


# ─────────────────────────────────────────────────────────────────────────
# Core compute
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _AnalyzeContext:
    profile: SensitivityProfile
    output: SensitivityOutput
    input1: SensitivityInput
    input2: SensitivityInput
    input1_range: tuple[float, float]
    input2_range: tuple[float, float]
    input1_step: float
    input2_step: float
    base_value: float
    base_inputs: dict[str, float]


def analyze(profile: SensitivityProfile, req: SensitivityRequest) -> SensitivityResult:
    """Run the full sensitivity analysis. Pure compute, no I/O beyond the
    base_value lookup done by the caller (see `analyze_with_base`).
    """
    if req.output_id not in profile.output_ids():
        raise KeyError(f"output_id not in profile: {req.output_id}")
    if req.input1_id not in profile.input_ids():
        raise KeyError(f"input1_id not in profile: {req.input1_id}")
    input2_id = req.input2_id or req.input1_id
    if input2_id not in profile.input_ids():
        raise KeyError(f"input2_id not in profile: {input2_id}")

    out = profile.find_output(req.output_id)
    in1 = profile.find_input(req.input1_id)
    in2 = profile.find_input(input2_id)

    # Resolve ranges: request overrides default; if absent, use profile default.
    def _resolve_range(
        override: list[float] | tuple[float, float] | None,
        default: list[float],
    ) -> tuple[float, float]:
        if override is not None and len(override) == 2:
            lo, hi = float(override[0]), float(override[1])
        else:
            lo, hi = float(default[0]), float(default[1])
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi

    r1 = _resolve_range(req.input1_range, in1.default_range)
    r2 = _resolve_range(
        list(req.input2_range) if req.input2_range and req.input2_range[0] is not None else None,
        in2.default_range,
    )

    # Base value: client override > YAML ref > 0.0
    base_value: float
    if req.output_id in req.base_overrides:
        base_value = float(req.base_overrides[req.output_id])
    else:
        base_value = _resolve_base_value(out.base_value_ref, profile.line_id)

    # base_inputs is informational only (it does NOT feed the matrix or
    # the scenarios). We deliberately do NOT resolve every input's base
    # value over HTTP here — the engine is universal and shouldn't pay
    # the cost of N HTTP calls per analyze. Frontend can call
    # /api/sensitivity/profiles/{line_id} to read the references and
    # resolve them against the line's API on demand.
    base_inputs: dict[str, float] = {inp.id: 0.0 for inp in profile.inputs}

    ctx = _AnalyzeContext(
        profile=profile,
        output=out,
        input1=in1,
        input2=in2,
        input1_range=r1,
        input2_range=r2,
        input1_step=req.input1_step if req.input1_step > 0 else in1.default_step,
        input2_step=req.input2_step if req.input2_step > 0 else in2.default_step,
        base_value=base_value,
        base_inputs=base_inputs,
    )

    return _build_result(ctx)


def _output_at(ctx: _AnalyzeContext, delta1: float, delta2: float) -> float:
    """Linear recompute: base + sum(coef * delta)."""
    total = ctx.base_value
    coefs = ctx.output.coefficients
    # input1 contribution
    c1 = float(coefs.get(ctx.input1.id, 0.0))
    total += c1 * delta1
    # input2 contribution (only if it's a distinct input)
    if ctx.input2.id != ctx.input1.id:
        c2 = float(coefs.get(ctx.input2.id, 0.0))
        total += c2 * delta2
    return total


def _build_result(ctx: _AnalyzeContext) -> SensitivityResult:
    is_1d = ctx.input2.id == ctx.input1.id

    # Build sweep axes
    sweep1 = _linspace(ctx.input1_range[0], ctx.input1_range[1], ctx.input1_step)
    if is_1d:
        sweep2 = [0.0]  # one-row matrix
    else:
        sweep2 = _linspace(ctx.input2_range[0], ctx.input2_range[1], ctx.input2_step)

    # Matrix: rows=input2, cols=input1
    matrix: list[list[float]] = []
    for d2 in sweep2:
        row: list[float] = []
        for d1 in sweep1:
            row.append(round(_output_at(ctx, d1, d2), 6))
        matrix.append(row)

    # Tornado: for each input OTHER than the two we sweep, perturb it to
    # both ends of its default range and measure the swing on the output.
    # For 1D mode we also include the swept input1 to show its own impact.
    tornado: list[TornadoBar] = []
    in_sweep = {ctx.input1.id}
    if not is_1d:
        in_sweep.add(ctx.input2.id)

    for inp in ctx.profile.inputs:
        if inp.id in in_sweep and is_1d:
            # include the swept input itself in 1D tornado
            pass
        if is_1d and inp.id != ctx.input1.id and inp.id in in_sweep:
            continue

        lo, hi = inp.default_range[0], inp.default_range[1]
        coef = float(ctx.output.coefficients.get(inp.id, 0.0))
        # "value_at_min" = base + coef * lo  (relative perturbation)
        v_min = ctx.base_value + coef * lo
        v_max = ctx.base_value + coef * hi
        # For 1D: only count inputs OTHER than the swept one (the swept
        # one's impact is shown in the matrix already).
        if is_1d and inp.id == ctx.input1.id:
            continue
        span = abs(v_max - v_min)
        if v_max > v_min:
            direction = "up"
        elif v_max < v_min:
            direction = "down"
        else:
            direction = "flat"
        tornado.append(
            TornadoBar(
                input_id=inp.id,
                input_name=inp.name,
                value_at_min=round(v_min, 6),
                value_at_max=round(v_max, 6),
                span=round(span, 6),
                direction=direction,
            )
        )

    # Sort tornado by span desc; deterministic tie-break on input id.
    tornado.sort(key=lambda b: (-b.span, b.input_id))

    # Scenarios: worst, base, best
    r1_lo, r1_hi = ctx.input1_range
    r2_lo, r2_hi = ctx.input2_range

    def _scenario(name: str, label: str, d1: float, d2: float) -> Scenario:
        v = round(_output_at(ctx, d1, d2), 6)
        delta = round(v - ctx.base_value, 6)
        pct = round((delta / ctx.base_value * 100.0), 4) if ctx.base_value != 0 else 0.0
        return Scenario(
            name=name,
            label=label,
            input1_delta=round(d1, 6),
            input2_delta=round(d2, 6),
            output_value=v,
            delta_from_base=delta,
            delta_pct=pct,
        )

    scenarios: list[Scenario] = []
    if is_1d:
        scenarios = [
            _scenario("worst", "最坏", r1_lo, 0.0),
            _scenario("base", "基准", 0.0, 0.0),
            _scenario("best", "最好", r1_hi, 0.0),
        ]
    else:
        # 2D scenarios: corners of the sweep rectangle.
        scenarios = [
            _scenario("worst", "最坏 (双低)", r1_lo, r2_lo),
            _scenario("worst_x1", "最坏 (X 低)", r1_lo, 0.0),
            _scenario("worst_x2", "最坏 (Y 低)", 0.0, r2_lo),
            _scenario("base", "基准", 0.0, 0.0),
            _scenario("best_x1", "最好 (X 高)", r1_hi, 0.0),
            _scenario("best_x2", "最好 (Y 高)", 0.0, r2_hi),
            _scenario("best", "最好 (双高)", r1_hi, r2_hi),
        ]

    return SensitivityResult(
        line_id=ctx.profile.line_id,
        output_id=ctx.output.id,
        output_name=ctx.output.name,
        output_unit=ctx.output.unit,
        base_value=round(ctx.base_value, 6),
        base_inputs={k: round(v, 6) for k, v in ctx.base_inputs.items()},
        matrix=matrix,
        matrix_labels=MatrixLabels(
            col_name=ctx.input1.name,
            row_name=ctx.input2.name,
            col_labels=[_fmt_pct(x) for x in sweep1],
            row_labels=[_fmt_pct(x) for x in sweep2],
            col_var=ctx.input1.id,
            row_var=ctx.input2.id,
        ),
        tornado=tornado,
        scenarios=scenarios,
        input_definitions=[i.model_dump() for i in ctx.profile.inputs],
        output_definitions=[o.model_dump() for o in ctx.profile.outputs],
    )


# ─────────────────────────────────────────────────────────────────────────
# Convenience: full HTTP-coupling analyze (loads base values)
# ─────────────────────────────────────────────────────────────────────────


def analyze_request(req: SensitivityRequest) -> SensitivityResult:
    """Load profile, then call analyze. Raises KeyError / FileNotFoundError
    on bad input. The HTTP layer translates these to 400 / 404."""
    profile = load_profile(req.line_id)
    return analyze(profile, req)
