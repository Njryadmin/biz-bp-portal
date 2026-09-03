"""
apps/api/tests/test_forecast.py

Tests for the universal Rolling Forecast engine and its HTTP router.

Coverage:
  1.  Profile loading (per-line + 404 on missing)
  2.  list_profiles() returns the registered business lines
  3.  sma method: shape, flat forecast, narrow band
  4.  ema method: smooths toward last fitted value
  5.  linear_trend: monotone direction + widening CI
  6.  seasonal_naive: periodic 12-month pattern
  7.  MAPE / bias computed on last 6 historical periods
  8.  attribution when include_attribution=True (4 buckets / weights)
  9.  End-to-end HTTP: profiles + run
 10.  compare endpoint returns delta + mean_abs_delta_pct
 11.  Bad line_id → 404
 12.  Bad indicator_id → 400
 13.  Universality: a 5th line works without engine code changes.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.forecast_engine import (
    ForecastRequest,
    clear_profile_cache,
    list_profiles,
    load_profile,
    run_forecast,
)


# ─────────────────────────────────────────────────────────────────────────
# 1) Profile loading
# ─────────────────────────────────────────────────────────────────────────


def test_load_profile_residential(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    assert p.line_id == "residential"
    assert p.line_name
    # 4 series declared in residential/forecast.yaml
    assert len(p.series) >= 3
    # 4 attribution buckets
    assert len(p.attribution) == 4


def test_load_profile_retail(client_with_auth):
    clear_profile_cache()
    p = load_profile("retail")
    assert p.line_id == "retail"
    assert any(s.indicator_id == "noi" for s in p.series)


def test_load_profile_retail_leasing(client_with_auth):
    clear_profile_cache()
    p = load_profile("retail-leasing")
    assert p.line_id == "retail-leasing"
    assert any(s.indicator_id == "occupancy_rate" for s in p.series)


def test_load_profile_unknown_line_raises(client_with_auth):
    clear_profile_cache()
    try:
        load_profile("does-not-exist-line")
    except FileNotFoundError as exc:
        assert "forecast.yaml" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


# ─────────────────────────────────────────────────────────────────────────
# 2) list_profiles
# ─────────────────────────────────────────────────────────────────────────


def test_list_profiles_returns_three(client_with_auth):
    ids = list_profiles()
    assert "residential" in ids
    assert "retail" in ids
    assert "retail-leasing" in ids
    # my-line has no forecast.yaml — gracefully omitted.
    assert "my-line" not in ids


# ─────────────────────────────────────────────────────────────────────────
# 3) sma method
# ─────────────────────────────────────────────────────────────────────────


def test_sma_forecast_shape_and_values(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="dedup_rate",
        method="sma",
        horizon_months=12,
        historical_periods=18,
    )
    r = run_forecast(p, req)
    assert r.method == "sma"
    # history has 18 points, forecast has 12 points
    assert len(r.historical) == 18
    assert len(r.forecast) == 12
    # every point has lower <= point <= upper
    for pnt in r.historical + r.forecast:
        assert pnt.lower <= pnt.point <= pnt.upper
    # all historical are is_actual=True
    assert all(pnt.is_actual for pnt in r.historical)
    assert not any(pnt.is_actual for pnt in r.forecast)
    # SMA forecast is a near-flat line (slight decay 0.5%/step)
    head = r.forecast[0].point
    tail = r.forecast[-1].point
    assert head > 0 and tail > 0
    # forecast[0] is the most recent SMA value, which should be close
    # to the mean of the last 3 historical points.
    last3 = [h.point for h in r.historical[-3:]]
    sma = sum(last3) / 3
    assert abs(head - sma) / sma < 0.01  # within 1% (decay is tiny)


# ─────────────────────────────────────────────────────────────────────────
# 4) ema method
# ─────────────────────────────────────────────────────────────────────────


def test_ema_forecast_is_flat_line(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="payment_completion",
        method="ema",
        horizon_months=12,
        historical_periods=24,
    )
    r = run_forecast(p, req)
    assert r.method == "ema"
    assert len(r.forecast) == 12
    # EMA forecast is flat (= last fitted value)
    vals = [f.point for f in r.forecast]
    assert max(vals) - min(vals) < 1e-6


# ─────────────────────────────────────────────────────────────────────────
# 5) linear_trend
# ─────────────────────────────────────────────────────────────────────────


def test_linear_trend_widening_ci(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="dynamic_irr",
        method="linear_trend",
        horizon_months=12,
        historical_periods=24,
    )
    r = run_forecast(p, req)
    assert r.method == "linear_trend"
    assert len(r.forecast) == 12
    # CI half-width grows monotonically with horizon (sqrt(h))
    prev_half = -1.0
    for pnt in r.forecast:
        half = (pnt.upper - pnt.lower) / 2.0
        assert half + 1e-9 >= prev_half
        prev_half = half


# ─────────────────────────────────────────────────────────────────────────
# 6) seasonal_naive
# ─────────────────────────────────────────────────────────────────────────


def test_seasonal_naive_repeats_year_ago_value(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="channel_fee_ratio",
        method="seasonal_naive",
        horizon_months=12,
        historical_periods=24,
    )
    r = run_forecast(p, req)
    assert r.method == "seasonal_naive"
    assert len(r.forecast) == 12
    # forecast[h] should equal history[-12 + h] for h=1..12
    for h, f in enumerate(r.forecast, start=1):
        expected = r.historical[-12 + (h - 1)].point
        assert abs(f.point - expected) < 1e-6, (
            f"forecast[{h}]={f.point} vs history[-12+{h-1}]={expected}"
        )


# ─────────────────────────────────────────────────────────────────────────
# 7) MAPE / bias
# ─────────────────────────────────────────────────────────────────────────


def test_mape_and_bias_present(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="dynamic_irr",
        method="linear_trend",
        horizon_months=12,
        historical_periods=24,
    )
    r = run_forecast(p, req)
    assert r.mape is not None
    assert 0.0 <= r.mape < 1.0  # tolerate up to 100% error but usually < few %
    assert r.bias is not None
    assert r.confidence == 0.95


# ─────────────────────────────────────────────────────────────────────────
# 8) Attribution
# ─────────────────────────────────────────────────────────────────────────


def test_attribution_buckets_and_weights(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="dynamic_irr",
        method="linear_trend",
        horizon_months=12,
        historical_periods=24,
        include_attribution=True,
    )
    r = run_forecast(p, req)
    assert r.attribution is not None
    assert len(r.attribution) == 4  # 4 buckets
    # weights sum to ~1.0
    total_w = sum(a.weight for a in r.attribution)
    assert abs(total_w - 1.0) < 1e-4
    # each has a non-empty top_driver from the YAML
    for a in r.attribution:
        assert a.factor_id
        assert a.factor_name
        assert a.top_driver


def test_attribution_omitted_by_default(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = ForecastRequest(
        line_id="residential",
        indicator_id="dynamic_irr",
        method="sma",
        horizon_months=12,
        historical_periods=12,
    )
    r = run_forecast(p, req)
    assert r.attribution is None


# ─────────────────────────────────────────────────────────────────────────
# 9) End-to-end HTTP
# ─────────────────────────────────────────────────────────────────────────


def test_http_profiles_endpoint(client_with_auth):
        r = client_with_auth.get("/api/forecast/profiles")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 3
        line_ids = {p["line_id"] for p in data["profiles"]}
        assert {"residential", "retail", "retail-leasing"}.issubset(line_ids)


def test_http_profile_for_one_line(client_with_auth):
        r = client_with_auth.get("/api/forecast/profiles/residential")
        assert r.status_code == 200
        data = r.json()
        assert data["line_id"] == "residential"
        assert len(data["series"]) >= 3
        assert len(data["attribution"]) == 4


def test_http_profile_unknown_line_404(client_with_auth):
        r = client_with_auth.get("/api/forecast/profiles/does-not-exist")
        assert r.status_code == 404


def test_http_run_returns_historical_and_forecast(client_with_auth):
        body = {
            "line_id": "residential",
            "indicator_id": "dynamic_irr",
            "horizon_months": 12,
            "method": "linear_trend",
        }
        r = client_with_auth.post("/api/forecast/run", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["line_id"] == "residential"
        assert data["indicator_id"] == "dynamic_irr"
        assert len(data["historical"]) >= 20
        assert len(data["forecast"]) == 12
        # every forecast point has period + point + lower + upper
        for pnt in data["forecast"]:
            assert "period" in pnt
            assert "point" in pnt
            assert "lower" in pnt
            assert "upper" in pnt
            assert pnt["is_actual"] is False


def test_http_run_unknown_line_404(client_with_auth):
        body = {"line_id": "does-not-exist", "indicator_id": "x"}
        r = client_with_auth.post("/api/forecast/run", json=body)
        assert r.status_code == 404


def test_http_run_unknown_indicator_400(client_with_auth):
        body = {"line_id": "residential", "indicator_id": "not_an_indicator"}
        r = client_with_auth.post("/api/forecast/run", json=body)
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────
# 10) Compare endpoint
# ─────────────────────────────────────────────────────────────────────────


def test_http_compare_returns_deltas(client_with_auth):
        body = {
            "line_id": "residential",
            "indicator_id": "dynamic_irr",
            "horizon_months": 3,
        }
        r = client_with_auth.post("/api/forecast/compare", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["line_id"] == "residential"
        assert len(data["rows"]) == 3
        # each row has period, actual, forecast, delta, delta_pct
        for row in data["rows"]:
            assert "period" in row
            assert "actual" in row
            assert "forecast" in row
            assert "delta" in row
            assert "delta_pct" in row
        # summary stats present
        assert "mean_delta_pct" in data
        assert "mean_abs_delta_pct" in data


# ─────────────────────────────────────────────────────────────────────────
# 11) Universality: 5th line, no engine code changes
# ─────────────────────────────────────────────────────────────────────────


def test_universality_with_temp_line(repo_root, tmp_path, client_with_auth):
    """A throwaway line + forecast.yaml should be auto-discoverable
    without any change to engine code."""
    import shutil

    line_dir = repo_root / "business_lines" / "tmp-forecast-line"
    line_dir.mkdir(exist_ok=True)
    yaml_text = """
line_id: tmp-forecast-line
line_name: "临时预测线"
series:
  - indicator_id: dummy_kpi
    name: 占位指标
    frequency: monthly
    method: sma
    horizon_months: 6
    historical_periods: 12
attribution:
  - id: a
    name: A
    drivers: [a1]
  - id: b
    name: B
    drivers: [b1]
"""
    (line_dir / "forecast.yaml").write_text(yaml_text, encoding="utf-8")
    clear_profile_cache()
    try:
        ids = list_profiles()
        assert "tmp-forecast-line" in ids
        p = load_profile("tmp-forecast-line")
        assert p.line_id == "tmp-forecast-line"
        req = ForecastRequest(
            line_id="tmp-forecast-line",
            indicator_id="dummy_kpi",
        )
        r = run_forecast(p, req)
        assert len(r.forecast) == 6
        assert len(r.historical) == 12
    finally:
        clear_profile_cache()
        shutil.rmtree(line_dir, ignore_errors=True)
