"""
apps/api/tests/test_sensitivity.py

Tests for the universal sensitivity engine and its HTTP router.

Coverage:
  1. Profile loading (per-line + 404 on missing)
  2. list_profiles() returns the registered business lines
  3. 1D analysis: matrix shape, base point at center
  4. 2D analysis: matrix shape, corner cells
  5. Tornado: sorted by span, correct number of bars
  6. Scenarios: 3 for 1D, 7 for 2D, base scenario at zero delta
  7. Bad input: unknown output_id → 400
  8. Bad input: unknown line_id → 404
  9. End-to-end: POST /analyze returns 200 + valid SensitivityResult
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services import sensitivity_engine
from app.services.sensitivity_engine import (
    SensitivityRequest,
    analyze,
    clear_profile_cache,
    list_profiles,
    load_profile,
)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _get_profiles() -> list[str]:
    """All line ids that should have a sensitivity.yaml in this repo."""
    return list_profiles()


# ─────────────────────────────────────────────────────────────────────────
# 1) Profile loading
# ─────────────────────────────────────────────────────────────────────────


def test_load_profile_residential(repo_root, client_with_auth):
    """residential sensitivity.yaml loads, has inputs + outputs."""
    clear_profile_cache()
    p = load_profile("residential")
    assert p.line_id == "residential"
    assert p.line_name  # non-empty
    assert len(p.inputs) >= 2
    assert len(p.outputs) >= 2
    # Coefficients are dicts of float
    for o in p.outputs:
        assert isinstance(o.coefficients, dict)
        for k, v in o.coefficients.items():
            assert isinstance(k, str)
            assert isinstance(v, (int, float))


def test_load_profile_retail(repo_root, client_with_auth):
    clear_profile_cache()
    p = load_profile("retail")
    assert p.line_id == "retail"
    # We designed retail with vacancy_rate as an input — confirm.
    assert any(i.id == "vacancy_rate" for i in p.inputs)
    # NOI is the headline retail output.
    assert any(o.id == "noi" for o in p.outputs)


def test_load_profile_retail_leasing(repo_root, client_with_auth):
    clear_profile_cache()
    p = load_profile("retail-leasing")
    assert p.line_id == "retail-leasing"
    assert any(i.id == "avg_deal_rent" for i in p.inputs)
    assert any(o.id == "commission_revenue" for o in p.outputs)


def test_load_profile_unknown_line_raises(client_with_auth):
    clear_profile_cache()
    try:
        load_profile("does-not-exist-line-xyz")
    except FileNotFoundError as exc:
        assert "sensitivity.yaml" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


# ─────────────────────────────────────────────────────────────────────────
# 2) list_profiles
# ─────────────────────────────────────────────────────────────────────────


def test_list_profiles_returns_three(repo_root, client_with_auth):
    """All 3 lines have sensitivity.yaml in the seed; the 4th (my-line) does not."""
    lines = _get_profiles()
    assert "residential" in lines
    assert "retail" in lines
    assert "retail-leasing" in lines
    # my-line has no sensitivity.yaml yet — that's fine, the engine
    # gracefully omits it.
    assert "my-line" not in lines


# ─────────────────────────────────────────────────────────────────────────
# 3) 1D analysis
# ─────────────────────────────────────────────────────────────────────────


def test_analyze_1d_matrix_shape_and_base_point(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="dynamic_irr",
        input1_id="avg_price",
        input2_id=None,
        input1_range=[-0.10, 0.10],
        input1_step=0.02,
        base_overrides={"dynamic_irr": 0.18},
    )
    result = analyze(p, req)
    # matrix must be 2D; for 1D we have 1 row × N cols
    assert len(result.matrix) == 1
    cols = len(result.matrix[0])
    # [-10%, +10%] step 0.02 → 11 points
    assert cols == 11
    # Center column corresponds to 0% delta → output == base_value.
    center_idx = cols // 2
    assert abs(result.matrix[0][center_idx] - result.base_value) < 1e-6
    # positive delta on avg_price should INCREASE dynamic_irr (coef +1.5).
    last = result.matrix[0][-1]
    first = result.matrix[0][0]
    assert last > result.base_value > first


# ─────────────────────────────────────────────────────────────────────────
# 4) 2D analysis
# ─────────────────────────────────────────────────────────────────────────


def test_analyze_2d_matrix_shape_and_corners(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="dynamic_irr",
        input1_id="avg_price",
        input2_id="construction_cost",
        input1_range=[-0.10, 0.10],
        input2_range=[-0.05, 0.05],
        input1_step=0.02,
        input2_step=0.01,
        base_overrides={"dynamic_irr": 0.18},
    )
    result = analyze(p, req)
    # row count = number of input2 sweep points; col = input1 sweep points
    assert len(result.matrix) == 11  # [-5%, +5%] step 0.01 → 11
    assert len(result.matrix[0]) == 11
    # coef: avg_price=+1.5, construction_cost=-0.8
    # matrix[0][0]: input1 at -0.10, input2 at -0.05 → 0.18 + 1.5*(-0.10) + (-0.8)*(-0.05)
    #             = 0.18 - 0.15 + 0.04 = 0.07
    # matrix[0][-1]: input1 at +0.10, input2 at -0.05 → 0.18 + 0.15 + 0.04 = 0.37
    # matrix[-1][0]: input1 at -0.10, input2 at +0.05 → 0.18 - 0.15 - 0.04 = -0.01 (worst)
    # matrix[-1][-1]: input1 at +0.10, input2 at +0.05 → 0.18 + 0.15 - 0.04 = 0.29
    assert abs(result.matrix[0][0] - 0.07) < 1e-4
    assert abs(result.matrix[0][-1] - 0.37) < 1e-4
    assert abs(result.matrix[-1][0] - (-0.01)) < 1e-4
    assert abs(result.matrix[-1][-1] - 0.29) < 1e-4
    # center cell == base_value
    mid_row = len(result.matrix) // 2
    mid_col = len(result.matrix[0]) // 2
    assert abs(result.matrix[mid_row][mid_col] - result.base_value) < 1e-6


# ─────────────────────────────────────────────────────────────────────────
# 5) Tornado
# ─────────────────────────────────────────────────────────────────────────


def test_tornado_sorted_by_span(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="dynamic_irr",
        input1_id="avg_price",
        input2_id=None,
        input1_range=[-0.10, 0.10],
        input1_step=0.02,
        base_overrides={"dynamic_irr": 0.18},
    )
    result = analyze(p, req)
    # residential has 4 inputs; 1D tornado includes the 3 NON-swept inputs.
    assert len(result.tornado) == 3
    # Sorted desc by span
    spans = [t.span for t in result.tornado]
    assert spans == sorted(spans, reverse=True)
    # Non-swept coefs & ranges:
    #   dedup_speed:        +0.3 × ±0.20 → span 0.12
    #   construction_cost:  -0.8 × ±0.05 → span 0.08
    #   channel_fee_rate:   -0.2 × ±0.30 → span 0.12
    # dedup_speed and channel_fee_rate tie at 0.12. Alphabetic tie-break
    # → channel_fee_rate first.
    top = result.tornado[0]
    assert top.input_id == "channel_fee_rate"
    assert abs(top.span - 0.12) < 1e-4
    bottom = result.tornado[-1]
    assert bottom.input_id == "construction_cost"
    assert abs(bottom.span - 0.08) < 1e-4


# ─────────────────────────────────────────────────────────────────────────
# 6) Scenarios
# ─────────────────────────────────────────────────────────────────────────


def test_scenarios_1d_three_items_base_in_middle(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="dynamic_irr",
        input1_id="avg_price",
        input2_id=None,
        input1_range=[-0.10, 0.10],
        input1_step=0.02,
        base_overrides={"dynamic_irr": 0.18},
    )
    result = analyze(p, req)
    assert len(result.scenarios) == 3
    names = [s.name for s in result.scenarios]
    assert names == ["worst", "base", "best"]
    base = result.scenarios[1]
    assert base.input1_delta == 0.0
    assert base.output_value == result.base_value
    assert base.delta_from_base == 0.0


def test_scenarios_2d_seven_items_includes_corners(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="dynamic_irr",
        input1_id="avg_price",
        input2_id="construction_cost",
        input1_range=[-0.10, 0.10],
        input2_range=[-0.05, 0.05],
        input1_step=0.02,
        input2_step=0.01,
        base_overrides={"dynamic_irr": 0.18},
    )
    result = analyze(p, req)
    assert len(result.scenarios) == 7
    names = [s.name for s in result.scenarios]
    assert "worst" in names
    assert "best" in names
    assert "base" in names
    base = next(s for s in result.scenarios if s.name == "base")
    assert base.input1_delta == 0.0 and base.input2_delta == 0.0
    # worst corner has negative delta_from_base; best has positive
    worst = next(s for s in result.scenarios if s.name == "worst")
    best = next(s for s in result.scenarios if s.name == "best")
    assert worst.delta_from_base < 0
    assert best.delta_from_base > 0


# ─────────────────────────────────────────────────────────────────────────
# 7) Bad input
# ─────────────────────────────────────────────────────────────────────────


def test_unknown_output_id_raises_keyerror(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="not_a_real_output",
        input1_id="avg_price",
        input2_id=None,
    )
    try:
        analyze(p, req)
    except KeyError as exc:
        assert "output_id" in str(exc) or "not_a_real_output" in str(exc)
    else:
        raise AssertionError("expected KeyError")


def test_unknown_input_id_raises_keyerror(client_with_auth):
    clear_profile_cache()
    p = load_profile("residential")
    req = SensitivityRequest(
        line_id="residential",
        output_id="dynamic_irr",
        input1_id="not_a_real_input",
        input2_id=None,
    )
    try:
        analyze(p, req)
    except KeyError as exc:
        assert "input1_id" in str(exc) or "not_a_real_input" in str(exc)
    else:
        raise AssertionError("expected KeyError")


# ─────────────────────────────────────────────────────────────────────────
# 8) End-to-end HTTP
# ─────────────────────────────────────────────────────────────────────────


def test_http_profiles_endpoint(client_with_auth):
        r = client_with_auth.get("/api/sensitivity/profiles")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 3
        line_ids = {p["line_id"] for p in data["profiles"]}
        assert {"residential", "retail", "retail-leasing"}.issubset(line_ids)


def test_http_profile_for_one_line(client_with_auth):
        r = client_with_auth.get("/api/sensitivity/profiles/residential")
        assert r.status_code == 200
        data = r.json()
        assert data["line_id"] == "residential"
        # residential: 4 inputs + 3 outputs (per the task spec)
        assert len(data["inputs"]) == 4
        assert len(data["outputs"]) == 3


def test_http_profile_unknown_line_404(client_with_auth):
        r = client_with_auth.get("/api/sensitivity/profiles/does-not-exist")
        assert r.status_code == 404


def test_http_analyze_1d_success(client_with_auth):
        body = {
            "line_id": "residential",
            "output_id": "dynamic_irr",
            "input1_id": "avg_price",
            "input2_id": None,
            "input1_range": [-0.10, 0.10],
            "input1_step": 0.02,
            "base_overrides": {"dynamic_irr": 0.18},
        }
        r = client_with_auth.post("/api/sensitivity/analyze", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["line_id"] == "residential"
        assert data["output_id"] == "dynamic_irr"
        assert len(data["matrix"]) == 1
        assert len(data["matrix"][0]) == 11
        # 1D tornado: 3 bars (inputs not swept)
        assert len(data["tornado"]) == 3
        # 3 scenarios for 1D
        assert len(data["scenarios"]) == 3


def test_http_analyze_2d_success(client_with_auth):
        body = {
            "line_id": "residential",
            "output_id": "dynamic_irr",
            "input1_id": "avg_price",
            "input2_id": "construction_cost",
            "input1_range": [-0.10, 0.10],
            "input2_range": [-0.05, 0.05],
            "input1_step": 0.02,
            "input2_step": 0.01,
            "base_overrides": {"dynamic_irr": 0.18},
        }
        r = client_with_auth.post("/api/sensitivity/analyze", json=body)
        assert r.status_code == 200, r.text
        data = r.json()
        rows, cols = len(data["matrix"]), len(data["matrix"][0])
        assert rows == 11 and cols == 11
        # 2D tornado: 4 bars (all inputs contribute)
        assert len(data["tornado"]) == 4
        # 7 scenarios for 2D
        assert len(data["scenarios"]) == 7


def test_http_analyze_unknown_output_400(client_with_auth):
        body = {
            "line_id": "residential",
            "output_id": "not_real",
            "input1_id": "avg_price",
            "input2_id": None,
        }
        r = client_with_auth.post("/api/sensitivity/analyze", json=body)
        # Bad IDs surface as 400 from the router's KeyError handler.
        assert r.status_code in (400, 404)


def test_http_analyze_unknown_line_404(client_with_auth):
        body = {
            "line_id": "does-not-exist",
            "output_id": "x",
            "input1_id": "y",
            "input2_id": None,
        }
        r = client_with_auth.post("/api/sensitivity/analyze", json=body)
        assert r.status_code == 404


def test_http_scenarios_endpoint(client_with_auth):
        r = client_with_auth.get("/api/sensitivity/scenarios/retail")
        assert r.status_code == 200
        data = r.json()
        assert data["line_id"] == "retail"
        # at least 1 output preset
        assert len(data["scenarios"]) >= 1
        first = data["scenarios"][0]
        assert "output_id" in first
        if "scenarios" in first:
            assert len(first["scenarios"]) >= 1


# ─────────────────────────────────────────────────────────────────────────
# 9) Universality: a 5th line works without engine code changes
# ─────────────────────────────────────────────────────────────────────────


def test_universality_with_temp_line(repo_root, tmp_path, monkeypatch, client_with_auth):
    """A throwaway line + sensitivity.yaml should be auto-discoverable
    without any change to engine code."""
    import shutil

    line_dir = repo_root / "business_lines" / "tmp-line"
    line_dir.mkdir(exist_ok=True)
    yaml_text = """
line_id: tmp-line
line_name: "临时业务线"
inputs:
  - id: foo
    name: "Foo 因子"
    unit: ""
    base_value_ref: ""
    default_range: [-0.10, 0.10]
    default_step: 0.05
outputs:
  - id: bar
    name: "Bar 指标"
    unit: ""
    base_value_ref: ""
    coefficients:
      foo: +1.0
"""
    (line_dir / "sensitivity.yaml").write_text(yaml_text, encoding="utf-8")
    # Bust the cache so the engine re-reads the directory.
    clear_profile_cache()
    try:
        # list_profiles picks it up
        ids = list_profiles()
        assert "tmp-line" in ids
        # load + analyze works
        p = load_profile("tmp-line")
        assert p.line_id == "tmp-line"
        req = SensitivityRequest(
            line_id="tmp-line",
            output_id="bar",
            input1_id="foo",
            input2_id=None,
            input1_range=[-0.10, 0.10],
            input1_step=0.05,
            base_overrides={"bar": 0.5},
        )
        result = analyze(p, req)
        # base=0.5, foo=+1.0; at +10% → 0.6; at -10% → 0.4
        assert abs(result.matrix[0][0] - 0.4) < 1e-6
        assert abs(result.matrix[0][-1] - 0.6) < 1e-6
    finally:
        # Clean up: remove the temp line entirely.
        clear_profile_cache()
        shutil.rmtree(line_dir, ignore_errors=True)
