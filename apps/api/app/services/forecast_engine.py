"""
apps/api/app/services/forecast_engine.py

Universal Rolling Forecast Engine for the fin-bp-portal.

CONTRACT
========

A business line provides a `forecast.yaml` in its own directory, e.g.
`business_lines/<line_id>/forecast.yaml`. The file describes:

- `series`:    a list of KPIs to forecast. Each series has an indicator_id,
               name, frequency, method, horizon and historical_periods.
- `attribution`: factors used to explain actual vs forecast deviation.

The engine is INTENTIONALLY GENERIC:

- It never imports `business_lines/*`. It only reads
  `business_lines/<line>/forecast.yaml`.
- Adding a 5th business line requires only writing its own YAML.
- Historical data is generated as a deterministic mock (no live fetch);
  this is OK because the focus is the forecasting math, not the data layer.

METHODS
=======

- sma             simple moving average (window default 3)
- ema             exponential smoothing (alpha default 0.3)
- linear_trend    OLS linear fit; residual std → 95% CI half-width
- seasonal_naive  reuses value from the same month one year ago

For each series, the engine returns:
  - historical periods: is_actual=True, fitted value + 95% band
  - forecast periods:   is_actual=False, point forecast + 95% band
  - MAPE / bias:        metrics on the last N=6 historical periods
  - attribution:        optional — when requested, returns a 30% / 30% / 20% / 20%
                        split across the 4 attribution categories (mock).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.logging import get_logger
from ..core.registry import get_project_root

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Pydantic DTOs — profile
# ─────────────────────────────────────────────────────────────────────────


class ForecastSeriesSpec(BaseModel):
    """One KPI series declared in a line's forecast.yaml."""

    model_config = ConfigDict(extra="allow")

    indicator_id: str
    name: str = ""
    frequency: str = "monthly"  # monthly | quarterly | weekly (engine treats as month-step)
    method: str = "sma"  # sma | ema | linear_trend | seasonal_naive
    horizon_months: int = 12
    historical_periods: int = 24
    sma_window: int = 3  # only used by sma
    ema_alpha: float = 0.3  # only used by ema
    # 95% CI half-width multiplier (Z = 1.96 for normal; we use 1.96).
    ci_z: float = 1.96

    @field_validator("method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        allowed = {"sma", "ema", "linear_trend", "seasonal_naive"}
        if v not in allowed:
            raise ValueError(f"method must be one of {allowed}, got {v!r}")
        return v

    @field_validator("horizon_months")
    @classmethod
    def _check_horizon(cls, v: int) -> int:
        if v < 1 or v > 60:
            raise ValueError(f"horizon_months must be in [1, 60], got {v}")
        return v

    @field_validator("historical_periods")
    @classmethod
    def _check_historical(cls, v: int) -> int:
        if v < 4 or v > 240:
            raise ValueError(f"historical_periods must be in [4, 240], got {v}")
        return v


class ForecastAttribution(BaseModel):
    """One attribution bucket in the deviation analysis."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    drivers: list[str] = Field(default_factory=list)


class ForecastProfile(BaseModel):
    """The full forecast profile for a single business line."""

    model_config = ConfigDict(extra="allow")

    line_id: str
    line_name: str = ""
    series: list[ForecastSeriesSpec] = Field(default_factory=list)
    attribution: list[ForecastAttribution] = Field(default_factory=list)

    def find_series(self, indicator_id: str) -> ForecastSeriesSpec:
        for s in self.series:
            if s.indicator_id == indicator_id:
                return s
        raise KeyError(indicator_id)

    def series_ids(self) -> list[str]:
        return [s.indicator_id for s in self.series]


# ─────────────────────────────────────────────────────────────────────────
# Pydantic DTOs — request / response
# ─────────────────────────────────────────────────────────────────────────


class ForecastRequest(BaseModel):
    """POST body for /api/forecast/run."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    indicator_id: str
    horizon_months: int | None = None  # None → use profile default
    method: str | None = None  # None → use profile default
    historical_periods: int | None = None  # None → use profile default
    include_attribution: bool = False


class ForecastPoint(BaseModel):
    period: str  # "2026-09"
    point: float
    lower: float
    upper: float
    is_actual: bool


class ForecastAttributionRow(BaseModel):
    factor_id: str
    factor_name: str
    weight: float  # 0..1
    top_driver: str  # mock — first driver of the bucket


class ForecastResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str
    indicator_id: str
    indicator_name: str
    method: str
    frequency: str
    historical: list[ForecastPoint]
    forecast: list[ForecastPoint]
    mape: float | None
    bias: float | None
    confidence: float  # 0.95
    attribution: list[ForecastAttributionRow] | None


# ─────────────────────────────────────────────────────────────────────────
# Profile loading
# ─────────────────────────────────────────────────────────────────────────


def _profile_path(line_id: str) -> Path:
    root = get_project_root()
    return root / "business_lines" / line_id / "forecast.yaml"


@lru_cache(maxsize=32)
def _load_profile_cached(line_id: str) -> ForecastProfile:
    return _load_profile_uncached(line_id)


def _load_profile_uncached(line_id: str) -> ForecastProfile:
    path = _profile_path(line_id)
    if not path.exists():
        raise FileNotFoundError(
            f"forecast profile not found for line_id={line_id!r} "
            f"(expected at {path}). Add a forecast.yaml under "
            f"business_lines/{line_id}/ to enable rolling forecasts."
        )
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"forecast.yaml for {line_id} must be a mapping, got {type(raw)}")
    raw.setdefault("line_id", line_id)
    return ForecastProfile.model_validate(raw)


def load_profile(line_id: str) -> ForecastProfile:
    return _load_profile_cached(line_id)


def list_profiles() -> list[str]:
    root = get_project_root()
    bl = root / "business_lines"
    if not bl.exists():
        return []
    out: list[str] = []
    for d in sorted(bl.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        if (d / "forecast.yaml").exists():
            out.append(d.name)
    return out


def clear_profile_cache() -> None:
    _load_profile_cached.cache_clear()


# ─────────────────────────────────────────────────────────────────────────
# Mock historical-data generator
# ─────────────────────────────────────────────────────────────────────────


def _month_str(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _add_months(year: int, month: int, k: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + k
    return total // 12, (total % 12) + 1


def _indicator_seed(indicator_id: str) -> int:
    """Deterministic seed from indicator id (so the same series always
    produces the same history)."""
    return sum((i + 1) * ord(c) for i, c in enumerate(indicator_id)) & 0xFFFF


def _generate_history(indicator_id: str, n: int) -> list[float]:
    """Generate `n` months of mock historical data, ending at the current
    month. The series has a gentle trend + a 12-month seasonal pattern,
    which makes all 4 forecast methods behave differently (i.e. the test
    suite can distinguish them).

    Deterministic per `indicator_id` so two calls return the same series.
    """
    seed = _indicator_seed(indicator_id)
    out: list[float] = []
    for i in range(n):
        # Linear trend (varies by indicator)
        trend = 0.002 * i  # 0.2% per month
        # Seasonal: a 12-month sine
        seasonal = 0.03 * math.sin(2 * math.pi * (i % 12) / 12.0)
        # Deterministic pseudo-noise
        noise = ((seed * (i + 1) * 2654435761) & 0xFFFF) / 65535.0 - 0.5
        noise = noise * 0.02
        # Base level: 0.5 for ratios, 0.18 for IRR-like, 200 for currency-like
        if indicator_id in {"noi", "commission_revenue"}:
            base = 800.0
        elif indicator_id in {"efficiency"}:
            base = 220.0
        elif indicator_id in {"avg_deal_rent"}:
            base = 320.0
        elif indicator_id in {"owner_vacancy_days"}:
            base = 45.0
        elif indicator_id in {"dynamic_irr", "payment_completion",
                               "dedup_rate", "collection_rate",
                               "occupancy_rate", "renewal_rate",
                               "channel_fee_ratio", "vacancy_rate",
                               "benchmark_gap_pct"}:
            base = 0.55
        else:
            base = 1.0
        v = base * (1.0 + trend) + seasonal * base + noise * base
        out.append(round(v, 6))
    return out


def _months_window_ending_now(n: int) -> list[tuple[int, int]]:
    """Return the n months ending at the current month, ordered ascending."""
    today = date.today()
    y, m = today.year, today.month
    out: list[tuple[int, int]] = []
    for i in range(n - 1, -1, -1):
        yy, mm = _add_months(y, m, -i)
        out.append((yy, mm))
    return out


# ─────────────────────────────────────────────────────────────────────────
# Forecasting methods
# ─────────────────────────────────────────────────────────────────────────


def _ols_fit(ys: list[float]) -> tuple[float, float, float, list[float]]:
    """Ordinary-least-squares y = a + b*x, return (a, b, sigma, fitted)."""
    n = len(ys)
    if n < 2:
        a = ys[0] if ys else 0.0
        b = 0.0
        sigma = 0.0
        fitted = [a] * n
        return a, b, sigma, fitted
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    b = num / den if den != 0 else 0.0
    a = mean_y - b * mean_x
    fitted = [a + b * x for x in xs]
    # Residual std
    if n > 2:
        sigma = math.sqrt(sum((ys[i] - fitted[i]) ** 2 for i in range(n)) / (n - 2))
    else:
        sigma = 0.0
    return a, b, sigma, fitted


@dataclass
class _Fit:
    method: str
    history: list[float]
    fitted: list[float]
    sigma: float  # residual std; 0 for sma / ema / seasonal_naive
    params: dict[str, Any]


def _fit_method(
    method: str,
    history: list[float],
    *,
    sma_window: int = 3,
    ema_alpha: float = 0.3,
) -> _Fit:
    n = len(history)
    if method == "sma":
        w = max(1, min(sma_window, n))
        fitted: list[float] = []
        for i in range(n):
            lo = max(0, i - w + 1)
            window = history[lo : i + 1]
            fitted.append(sum(window) / len(window))
        # sigma = recent std of last w residuals
        if n >= 2:
            sigma = _std(history[-w:])
        else:
            sigma = 0.0
        return _Fit(method=method, history=history, fitted=fitted, sigma=sigma, params={"sma_window": w})
    if method == "ema":
        if not history:
            return _Fit(method=method, history=history, fitted=[], sigma=0.0, params={"ema_alpha": ema_alpha})
        fitted = [history[0]]
        for i in range(1, n):
            fitted.append(ema_alpha * history[i] + (1 - ema_alpha) * fitted[-1])
        if n >= 2:
            sigma = _std([history[i] - fitted[i] for i in range(n)])
        else:
            sigma = 0.0
        return _Fit(method=method, history=history, fitted=fitted, sigma=sigma, params={"ema_alpha": ema_alpha})
    if method == "linear_trend":
        a, b, sigma, fitted = _ols_fit(history)
        return _Fit(method=method, history=history, fitted=fitted, sigma=sigma, params={"a": a, "b": b})
    if method == "seasonal_naive":
        # Fitted: each point is the same-month value 12 months back.
        # For the first 12 points, fall back to the mean.
        mean = sum(history) / n if n else 0.0
        fitted = [mean] * min(12, n)
        for i in range(12, n):
            fitted.append(history[i - 12])
        if n >= 2:
            sigma = _std([history[i] - fitted[i] for i in range(n)])
        else:
            sigma = 0.0
        return _Fit(method=method, history=history, fitted=fitted, sigma=sigma, params={"period": 12})
    raise ValueError(f"unknown method: {method!r}")


def _std(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _forecast_method(
    fit: _Fit,
    horizon: int,
    *,
    sma_window: int = 3,
    ema_alpha: float = 0.3,
) -> list[float]:
    method = fit.method
    n = len(fit.history)
    if method == "sma":
        w = max(1, min(sma_window, n))
        # Use the most recent `w` actuals for forward prediction.
        tail = fit.history[-w:]
        base = sum(tail) / len(tail)
        # Decay the simple forecast by 0.5% per step so the line isn't
        # perfectly flat — this makes confidence bands visibly widen.
        out: list[float] = []
        for h in range(1, horizon + 1):
            out.append(base * (1.0 - 0.005 * h))
        return out
    if method == "ema":
        last = fit.fitted[-1] if fit.fitted else (fit.history[-1] if fit.history else 0.0)
        out = []
        for _ in range(horizon):
            out.append(last)
            # EMA flat-line forecast: y_{t+h} = last fitted value.
        return out
    if method == "linear_trend":
        a = fit.params["a"]
        b = fit.params["b"]
        out = [a + b * (n - 1 + h) for h in range(1, horizon + 1)]
        return out
    if method == "seasonal_naive":
        out: list[float] = []
        for h in range(1, horizon + 1):
            idx = n - 12 + ((h - 1) % 12)
            if 0 <= idx < n:
                out.append(fit.history[idx])
            else:
                out.append(fit.history[-1] if fit.history else 0.0)
        return out
    raise ValueError(f"unknown method: {method!r}")


# ─────────────────────────────────────────────────────────────────────────
# Core compute
# ─────────────────────────────────────────────────────────────────────────


def _mape(history: list[float], fitted: list[float], last_n: int = 6) -> float | None:
    if not history or not fitted:
        return None
    pairs = list(zip(history[-last_n:], fitted[-last_n:]))
    pairs = [(h, f) for h, f in pairs if h != 0]
    if not pairs:
        return None
    return sum(abs(h - f) / abs(h) for h, f in pairs) / len(pairs)


def _bias(history: list[float], fitted: list[float], last_n: int = 6) -> float | None:
    if not history or not fitted:
        return None
    pairs = list(zip(history[-last_n:], fitted[-last_n:]))
    if not pairs:
        return None
    return sum(h - f for h, f in pairs) / len(pairs)


def _build_attribution(
    profile: ForecastProfile,
) -> list[ForecastAttributionRow]:
    """Mock attribution: split weights 0.30/0.30/0.20/0.20 across the
    declared attribution buckets. If a line has fewer than 4 buckets,
    distribute evenly.
    """
    buckets = profile.attribution
    if not buckets:
        return []
    if len(buckets) == 1:
        weights = [1.0]
    elif len(buckets) == 2:
        weights = [0.6, 0.4]
    elif len(buckets) == 3:
        weights = [0.4, 0.3, 0.3]
    else:
        weights = [0.3, 0.3, 0.2, 0.2]
    rows: list[ForecastAttributionRow] = []
    for w, b in zip(weights, buckets):
        rows.append(
            ForecastAttributionRow(
                factor_id=b.id,
                factor_name=b.name or b.id,
                weight=round(w, 4),
                top_driver=b.drivers[0] if b.drivers else "",
            )
        )
    return rows


def run_forecast(profile: ForecastProfile, req: ForecastRequest) -> ForecastResult:
    """Top-level entry: validate request, generate history, fit, forecast."""
    if req.indicator_id not in profile.series_ids():
        raise KeyError(f"indicator_id not in profile: {req.indicator_id}")

    spec = profile.find_series(req.indicator_id)
    method = req.method or spec.method
    horizon = req.horizon_months or spec.horizon_months
    hist_n = req.historical_periods or spec.historical_periods
    if method not in {"sma", "ema", "linear_trend", "seasonal_naive"}:
        raise ValueError(f"unknown method: {method!r}")
    if horizon < 1 or horizon > 60:
        raise ValueError(f"horizon_months must be in [1, 60], got {horizon}")

    # Generate the mock history. Use enough periods so the chosen method
    # can converge (e.g. seasonal_naive needs ≥13 to "fire").
    actual_n = max(hist_n, 24) if method == "seasonal_naive" else hist_n
    history_vals = _generate_history(req.indicator_id, actual_n)
    months = _months_window_ending_now(actual_n)

    fit = _fit_method(
        method,
        history_vals,
        sma_window=spec.sma_window,
        ema_alpha=spec.ema_alpha,
    )
    forecast_vals = _forecast_method(
        fit,
        horizon,
        sma_window=spec.sma_window,
        ema_alpha=spec.ema_alpha,
    )

    z = spec.ci_z
    # 1) historical points — use the fitted value as `point`; widen CI
    #    by residual sigma. Clamp lower to 0 for non-negative metrics.
    historical: list[ForecastPoint] = []
    for i, (yy, mm) in enumerate(months):
        v = history_vals[i]
        f = fit.fitted[i]
        # Band widens toward older data (less relevant) — keep constant for simplicity.
        half = z * fit.sigma
        historical.append(
            ForecastPoint(
                period=_month_str(yy, mm),
                point=round(v, 6),
                lower=round(v - half, 6),
                upper=round(v + half, 6),
                is_actual=True,
            )
        )

    # 2) forecast points — use the forecasted value, CI widens with horizon
    forecast_pts: list[ForecastPoint] = []
    last_y, last_m = months[-1]
    for h in range(1, horizon + 1):
        yy, mm = _add_months(last_y, last_m, h)
        v = forecast_vals[h - 1]
        # Widen band linearly with horizon (in months): half = z * sigma * sqrt(h)
        half = z * fit.sigma * math.sqrt(h)
        forecast_pts.append(
            ForecastPoint(
                period=_month_str(yy, mm),
                point=round(v, 6),
                lower=round(v - half, 6),
                upper=round(v + half, 6),
                is_actual=False,
            )
        )

    mape = _mape(history_vals, fit.fitted)
    bias = _bias(history_vals, fit.fitted)
    attribution = _build_attribution(profile) if req.include_attribution else None

    return ForecastResult(
        line_id=profile.line_id,
        indicator_id=req.indicator_id,
        indicator_name=spec.name or req.indicator_id,
        method=method,
        frequency=spec.frequency,
        historical=historical,
        forecast=forecast_pts,
        mape=round(mape, 6) if mape is not None else None,
        bias=round(bias, 6) if bias is not None else None,
        confidence=0.95,
        attribution=attribution,
    )


# ─────────────────────────────────────────────────────────────────────────
# Compare actuals vs forecast (mock — for dashboard "variance" panel)
# ─────────────────────────────────────────────────────────────────────────


class ActualVsForecastRequest(BaseModel):
    """POST body for /api/forecast/compare."""

    model_config = ConfigDict(extra="forbid")

    line_id: str
    indicator_id: str
    horizon_months: int = 3
    method: str | None = None


class ActualVsForecastRow(BaseModel):
    period: str
    actual: float
    forecast: float
    delta: float
    delta_pct: float


class ActualVsForecastResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str
    indicator_id: str
    method: str
    rows: list[ActualVsForecastRow]
    mean_delta_pct: float
    mean_abs_delta_pct: float
    attribution: list[ForecastAttributionRow] | None = None


def run_compare(
    profile: ForecastProfile, req: ActualVsForecastRequest
) -> ActualVsForecastResult:
    """Mock compare: take the last `horizon` historical points, ask the
    engine to forecast them, then compare. (Forecasting the past is
    cheating, but this gives a clean "actual vs predicted" view that
    downstream UIs can plot.)
    """
    if req.indicator_id not in profile.series_ids():
        raise KeyError(f"indicator_id not in profile: {req.indicator_id}")
    spec = profile.find_series(req.indicator_id)
    method = req.method or spec.method

    history = _generate_history(req.indicator_id, max(req.horizon_months + 1, 6))
    cut = history[: -req.horizon_months] if req.horizon_months > 0 else history
    actual_tail = history[-req.horizon_months:] if req.horizon_months > 0 else []

    fit = _fit_method(
        method,
        cut,
        sma_window=spec.sma_window,
        ema_alpha=spec.ema_alpha,
    )
    forecast_tail = _forecast_method(
        fit,
        req.horizon_months,
        sma_window=spec.sma_window,
        ema_alpha=spec.ema_alpha,
    )

    today = date.today()
    # Months of actual_tail: ending at today, going back horizon-1 months.
    # forecast_tail lines up 1:1.
    months_back = list(range(req.horizon_months - 1, -1, -1))
    rows: list[ActualVsForecastRow] = []
    for h, k in enumerate(months_back):
        yy, mm = _add_months(today.year, today.month, -k)
        actual = actual_tail[h] if h < len(actual_tail) else 0.0
        fc = forecast_tail[h] if h < len(forecast_tail) else 0.0
        delta = actual - fc
        delta_pct = (delta / fc) if fc != 0 else 0.0
        rows.append(
            ActualVsForecastRow(
                period=_month_str(yy, mm),
                actual=round(actual, 6),
                forecast=round(fc, 6),
                delta=round(delta, 6),
                delta_pct=round(delta_pct, 6),
            )
        )

    if rows:
        mean_delta_pct = sum(r.delta_pct for r in rows) / len(rows)
        mean_abs_delta_pct = sum(abs(r.delta_pct) for r in rows) / len(rows)
    else:
        mean_delta_pct = 0.0
        mean_abs_delta_pct = 0.0

    return ActualVsForecastResult(
        line_id=profile.line_id,
        indicator_id=req.indicator_id,
        method=method,
        rows=rows,
        mean_delta_pct=round(mean_delta_pct, 6),
        mean_abs_delta_pct=round(mean_abs_delta_pct, 6),
        attribution=_build_attribution(profile),
    )
