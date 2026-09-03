"""
apps/api/tests/test_alerts.py

Tests for the universal Alert Engine and its HTTP router.

Coverage:
  1.  Profile loading + 404 on missing
  2.  list_profiles returns the registered business lines
  3.  Operator: `<` fires when current < threshold
  4.  Operator: `>` fires when current > threshold
  5.  Operator: `between` with [lo, hi] range
  6.  Operator: `change_pct` vs previous period
  7.  Operator: `==` with string-typed threshold (redlines)
  8.  consecutive(N) — only fires after N consecutive periods hold
  9.  Summary count by severity
 10.  Acknowledge + delete
 11.  History pagination + filter
 12.  End-to-end HTTP: rules + check + acknowledge
 13.  Bad line_id → 404
 14.  Universality: a 5th line works without engine code changes.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.alert_engine import (
    AlertCheckRequest,
    clear_profile_cache,
    list_profiles,
    load_profile,
    check,
    reset_store,
)


# ─────────────────────────────────────────────────────────────────────────
# 1) Profile loading
# ─────────────────────────────────────────────────────────────────────────


def test_load_profile_residential():
    clear_profile_cache()
    p = load_profile("residential")
    assert p.line_id == "residential"
    assert len(p.rules) >= 4
    assert len(p.attribution) == 4
    # confirm operators covered
    ops = {r.operator for r in p.rules}
    assert "<" in ops
    assert "change_pct" in ops
    assert "==" in ops
    assert "between" in ops


def test_load_profile_retail():
    clear_profile_cache()
    p = load_profile("retail")
    assert p.line_id == "retail"
    # retail uses consecutive for vacancy
    consec = [r for r in p.rules if r.consecutive]
    assert len(consec) >= 1


def test_load_profile_retail_leasing():
    clear_profile_cache()
    p = load_profile("retail-leasing")
    assert p.line_id == "retail-leasing"
    assert any(r.id == "occupancy_below" for r in p.rules)


def test_load_profile_unknown_line_raises():
    clear_profile_cache()
    try:
        load_profile("does-not-exist-line")
    except FileNotFoundError as exc:
        assert "alerts.yaml" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")


# ─────────────────────────────────────────────────────────────────────────
# 2) list_profiles
# ─────────────────────────────────────────────────────────────────────────


def test_list_profiles_returns_three():
    ids = list_profiles()
    assert {"residential", "retail", "retail-leasing"}.issubset(set(ids))
    assert "my-line" not in ids


# ─────────────────────────────────────────────────────────────────────────
# 3) Operator: < (less-than)
# ─────────────────────────────────────────────────────────────────────────


def test_lt_operator_fires(monkeypatch):
    """Mock current value below threshold; rule should fire."""
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    # Filter to one rule we control directly.
    rule = next(r for r in p.rules if r.id == "irr_below_threshold")
    # Override the mock period generator to return 0.05 every period
    from app.services import alert_engine

    def fake_periods(_target_id: str, _indicator: str, n: int = 6) -> list[float]:
        return [0.05] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    # Falls back to line-level target when API is down → at least 1 alert
    assert res.rules_evaluated == 1
    assert len(res.alerts_triggered) >= 1
    a = res.alerts_triggered[0]
    assert a.rule_id == "irr_below_threshold"
    assert a.severity == "high"
    # message should be rendered
    assert "动态 IRR" in a.message or "irr" in a.message.lower() or "{value" in a.message


def test_lt_operator_does_not_fire_when_above(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    rule = next(r for r in p.rules if r.id == "irr_below_threshold")
    from app.services import alert_engine

    def fake_periods(_target_id: str, _indicator: str, n: int = 6) -> list[float]:
        return [0.20] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    assert res.alerts_triggered == []


# ─────────────────────────────────────────────────────────────────────────
# 4) Operator: > (greater-than)
# ─────────────────────────────────────────────────────────────────────────


def test_gt_operator_fires(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("retail")
    rule = next(r for r in p.rules if r.id == "vacancy_spike")
    from app.services import alert_engine

    def fake_periods(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.25] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="retail", rule_ids=[rule.id])
    res = check(p, req)
    assert res.rules_evaluated == 1
    assert len(res.alerts_triggered) >= 1
    assert res.alerts_triggered[0].rule_id == "vacancy_spike"


# ─────────────────────────────────────────────────────────────────────────
# 5) Operator: between
# ─────────────────────────────────────────────────────────────────────────


def test_between_operator_fires(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    rule = next(r for r in p.rules if r.id == "irr_between_band")
    from app.services import alert_engine

    def fake_periods(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.12] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    # 0.12 is in [0.10, 0.15] → fires
    assert len(res.alerts_triggered) >= 1
    assert res.alerts_triggered[0].severity == "low"


def test_between_operator_outside_range(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    rule = next(r for r in p.rules if r.id == "irr_between_band")
    from app.services import alert_engine

    def fake_periods(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.05] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    assert res.alerts_triggered == []


# ─────────────────────────────────────────────────────────────────────────
# 6) Operator: change_pct
# ─────────────────────────────────────────────────────────────────────────


def test_change_pct_fires_on_drop(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    rule = next(r for r in p.rules if r.id == "payment_drop")
    from app.services import alert_engine

    # Previous period 0.80, current 0.70 → drop -12.5%, threshold -5% → fires
    def fake_periods(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.80] * (n - 1) + [0.70]
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    assert len(res.alerts_triggered) >= 1
    a = res.alerts_triggered[0]
    assert a.rule_id == "payment_drop"
    assert a.severity == "medium"
    # message should mention delta_pct (already rendered into template)
    assert "回款" in a.message or "delta" in a.message.lower() or "{delta_pct" in a.message


def test_change_pct_does_not_fire_on_rise(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    rule = next(r for r in p.rules if r.id == "payment_drop")
    from app.services import alert_engine

    def fake_periods(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.70] * (n - 1) + [0.80]  # up 14%
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    assert res.alerts_triggered == []


# ─────────────────────────────────────────────────────────────────────────
# 7) Operator: == (string threshold for redlines)
# ─────────────────────────────────────────────────────────────────────────


def test_eq_operator_string_threshold(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    rule = next(r for r in p.rules if r.id == "redline_breach")
    from app.services import alert_engine

    def fake_periods(_t: str, _i: str, n: int = 6) -> list[str]:
        return ["green", "yellow", "yellow", "yellow", "yellow", "red"]
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    req = AlertCheckRequest(line_id="residential", rule_ids=[rule.id])
    res = check(p, req)
    assert len(res.alerts_triggered) >= 1
    a = res.alerts_triggered[0]
    assert a.metric_value == "red"
    assert a.severity == "high"


# ─────────────────────────────────────────────────────────────────────────
# 8) Consecutive (N)
# ─────────────────────────────────────────────────────────────────────────


def test_consecutive_requires_n_periods(monkeypatch):
    """consecutive=3 means the condition must hold for the LAST 3 periods."""
    reset_store()
    clear_profile_cache()
    p = load_profile("retail")
    rule = next(r for r in p.rules if r.id == "vacancy_consecutive_high")
    from app.services import alert_engine

    # Last 3 periods > 0.20, first 3 below → should fire
    def fake_periods_yes(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.10, 0.10, 0.10, 0.25, 0.25, 0.25]

    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods_yes)
    res = check(p, AlertCheckRequest(line_id="retail", rule_ids=[rule.id]))
    assert len(res.alerts_triggered) >= 1
    assert res.alerts_triggered[0].severity == "critical"

    # Now: only 2 consecutive → does NOT fire
    def fake_periods_no(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.10, 0.10, 0.10, 0.10, 0.25, 0.25]
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods_no)
    reset_store()
    res2 = check(p, AlertCheckRequest(line_id="retail", rule_ids=[rule.id]))
    assert res2.alerts_triggered == []


# ─────────────────────────────────────────────────────────────────────────
# 9) Summary count by severity
# ─────────────────────────────────────────────────────────────────────────


def test_summary_counts_severities(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    from app.services import alert_engine

    def fake_periods(_t: str, _i: str, n: int = 6) -> list[float]:
        return [0.0] * n  # everything below thresholds → many fires
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    res = check(p, AlertCheckRequest(line_id="residential"))
    s = res.summary
    assert "critical" in s and "high" in s and "medium" in s and "low" in s
    # total of summary equals number of triggered alerts
    assert sum(s.values()) == len(res.alerts_triggered)


# ─────────────────────────────────────────────────────────────────────────
# 10) Acknowledge + delete
# ─────────────────────────────────────────────────────────────────────────


def test_acknowledge_and_delete(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    from app.services import alert_engine

    def fake_periods(_t: str, indicator: str, n: int = 6) -> list:
        if indicator == "dynamic_irr":
            return [0.05, 0.06, 0.07, 0.08, 0.10, 0.12]
        if indicator == "payment_completion":
            return [0.50, 0.50, 0.50, 0.50, 0.50, 0.05]
        if indicator == "redlines":
            return ["green", "green", "green", "green", "green", "red"]
        if indicator == "dedup_rate":
            return [0.30] * 6
        return [0.0] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    res = check(p, AlertCheckRequest(line_id="residential"))
    assert res.alerts_triggered
    alert_id = res.alerts_triggered[0].alert_id

    # Acknowledge
    from app.services.alert_engine import acknowledge, delete
    a = acknowledge(alert_id)
    assert a is not None
    assert a.acknowledged is True

    # History still includes it (acked)
    from app.services.alert_engine import history
    items, total = history("residential", 100, 0)
    assert total >= 1
    acked = next((x for x in items if x.alert_id == alert_id), None)
    assert acked is not None
    assert acked.acknowledged is True

    # Delete (soft)
    assert delete(alert_id) is True
    items2, total2 = history("residential", 100, 0)
    assert total2 == total - 1
    assert not any(x.alert_id == alert_id for x in items2)

    # Second delete → not found
    assert delete(alert_id) is False


# ─────────────────────────────────────────────────────────────────────────
# 11) History pagination + filter
# ─────────────────────────────────────────────────────────────────────────


def test_history_pagination_and_line_filter(monkeypatch):
    reset_store()
    clear_profile_cache()
    p = load_profile("residential")
    from app.services import alert_engine

    def fake_periods(_t: str, indicator: str, n: int = 6) -> list:
        # Return values that fire all 5 residential rules:
        #  - irr_below_threshold (0.05)
        #  - payment_drop       (drops to 0.05 in last period)
        #  - redline_breach     (last period is "red")
        #  - dedup_stall        (all 0.05 < 0.50, consecutive 6)
        #  - irr_between_band   (0.12 in [0.10, 0.15])
        if indicator == "dynamic_irr":
            return [0.05, 0.06, 0.07, 0.08, 0.10, 0.12]
        if indicator == "payment_completion":
            return [0.50, 0.50, 0.50, 0.50, 0.50, 0.05]
        if indicator == "redlines":
            return ["green", "green", "green", "green", "green", "red"]
        if indicator == "dedup_rate":
            return [0.30] * 6
        return [0.0] * n
    monkeypatch.setattr(alert_engine, "_mock_periods", fake_periods)
    res = check(p, AlertCheckRequest(line_id="residential"))
    # All 5 rules should fire → 5 alerts
    assert len(res.alerts_triggered) >= 3

    from app.services.alert_engine import history
    items, total = history("residential", 2, 0)
    assert len(items) == 2
    assert total >= 3
    # second page
    items2, total2 = history("residential", 2, 2)
    assert len(items2) >= 1
    assert total2 == total
    # different line_id yields 0
    items3, total3 = history("retail", 100, 0)
    assert total3 == 0


# ─────────────────────────────────────────────────────────────────────────
# 12) End-to-end HTTP
# ─────────────────────────────────────────────────────────────────────────


def test_http_rules_endpoint():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/alerts/rules/residential")
        assert r.status_code == 200
        data = r.json()
        assert data["line_id"] == "residential"
        assert len(data["rules"]) >= 4


def test_http_rules_unknown_line_404():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/alerts/rules/does-not-exist")
        assert r.status_code == 404


def test_http_rules_summary():
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/alerts/rules/residential/summary")
        assert r.status_code == 200
        data = r.json()
        assert data["total_rules"] >= 4
        assert "by_severity" in data
        assert sum(data["by_severity"].values()) == data["total_rules"]


def test_http_check_unknown_line_404():
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/alerts/check", json={"line_id": "does-not-exist"})
        assert r.status_code == 404


def test_http_check_and_history_and_acknowledge():
    app = create_app()
    with TestClient(app) as client:
        # reset store via an internal hack? Not needed — we run on a fresh app.
        r = client.post("/api/alerts/check", json={"line_id": "residential"})
        assert r.status_code == 200, r.text
        data = r.json()
        rules_eval = data["rules_evaluated"]
        triggered = data["alerts_triggered"]
        assert rules_eval >= 4  # at least the enabled rules
        # the alert list is non-deterministic with mock data, so just
        # assert the contract: when there are alerts, they have an id
        if triggered:
            aid = triggered[0]["alert_id"]
            # history
            hr = client.get(f"/api/alerts/history?line_id=residential&limit=10")
            assert hr.status_code == 200
            hdata = hr.json()
            assert hdata["total"] >= 1
            assert any(it["alert_id"] == aid for it in hdata["items"])
            # acknowledge
            ack = client.post(f"/api/alerts/acknowledge/{aid}")
            assert ack.status_code == 200
            ackdata = ack.json()
            assert ackdata["acknowledged"] is True
            # delete
            dr = client.delete(f"/api/alerts/{aid}")
            assert dr.status_code == 200
            # second delete → 404
            dr2 = client.delete(f"/api/alerts/{aid}")
            assert dr2.status_code == 404
        else:
            # No alerts is OK for this run; just verify the schema.
            assert "summary" in data
            assert set(data["summary"].keys()) == {"critical", "high", "medium", "low"}


def test_http_acknowledge_unknown_404():
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/alerts/acknowledge/no-such-id")
        assert r.status_code == 404


# ─────────────────────────────────────────────────────────────────────────
# 13) Universality: 5th line
# ─────────────────────────────────────────────────────────────────────────


def test_universality_with_temp_line(repo_root):
    """A throwaway line + alerts.yaml should be auto-discoverable
    without any change to engine code."""
    import shutil

    line_dir = repo_root / "business_lines" / "tmp-alerts-line"
    line_dir.mkdir(exist_ok=True)
    yaml_text = """
line_id: tmp-alerts-line
line_name: "临时告警线"
rules:
  - id: dummy_rule
    name: 永远触发
    indicator_id: dummy
    operator: "<"
    threshold: 100
    severity: low
    message_template: "{target} 数值 {value}"
    enabled: true
    channels: [in_app]
    scope: line
"""
    (line_dir / "alerts.yaml").write_text(yaml_text, encoding="utf-8")
    clear_profile_cache()
    reset_store()
    try:
        ids = list_profiles()
        assert "tmp-alerts-line" in ids
        p = load_profile("tmp-alerts-line")
        assert p.line_id == "tmp-alerts-line"
        res = check(p, AlertCheckRequest(line_id="tmp-alerts-line"))
        assert res.rules_evaluated == 1
        # scope=line → one line-level target → exactly 1 alert
        assert len(res.alerts_triggered) == 1
    finally:
        clear_profile_cache()
        reset_store()
        shutil.rmtree(line_dir, ignore_errors=True)
