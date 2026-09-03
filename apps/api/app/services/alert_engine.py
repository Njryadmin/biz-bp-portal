"""
apps/api/app/services/alert_engine.py

Universal Alert Engine for the fin-bp-portal.

CONTRACT
========

A business line provides an `alerts.yaml` in its own directory, e.g.
`business_lines/<line_id>/alerts.yaml`. The file declares a list of
`rules` and an `attribution` block. Each rule has:

- `indicator_id`   which KPI to watch
- `operator`       one of  >  <  >=  <=  ==  between  change_pct
- `threshold`      numeric (or [lo, hi] for `between`, or string for `==`)
- `consecutive`    optional N — fires when the condition holds for N periods
- `severity`       low | medium | high | critical
- `message_template`  a Python `{name:.fmt}` template
- `enabled`        bool
- `channels`       list (only "in_app" is implemented)
- `scope`          "project" | "property" | "line" (controls which targets
                    the engine iterates over)

The engine is INTENTIONALLY GENERIC:

- It never imports `business_lines/*`. It only reads
  `business_lines/<line>/alerts.yaml`.
- Adding a 5th business line requires only writing its own YAML.
- Target lists come from the live line API (`/projects` or `/properties`).
  When the line API is unreachable, the engine falls back to a single
  "line-level" target so the rule still fires.
- Mock period history is generated per-target so the `consecutive` and
  `change_pct` operators have something to compare against.

STORAGE
=======

Triggered alerts are stored in an in-memory dict keyed by `alert_id`
(uuid4). Acknowledged / deleted alerts move to a tombstone set so the
history endpoint can filter them out.

The store is process-local. That's fine for a demo / dev deployment.
A future iteration could move this to Redis or Postgres.
"""
from __future__ import annotations

import math
import os
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.logging import get_logger
from ..core.registry import get_project_root

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Profile DTOs
# ─────────────────────────────────────────────────────────────────────────


Operator = Literal["<", "<=", ">", ">=", "==", "between", "change_pct"]
Severity = Literal["low", "medium", "high", "critical"]
Scope = Literal["project", "property", "line"]


class AlertRule(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    indicator_id: str
    operator: str
    threshold: Any
    consecutive: int | None = None
    severity: str = "medium"
    message_template: str = "{target} {indicator} = {value}"
    enabled: bool = True
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    scope: str = "line"

    @field_validator("operator")
    @classmethod
    def _check_op(cls, v: str) -> str:
        if v not in {"<", "<=", ">", ">=", "==", "between", "change_pct"}:
            raise ValueError(
                f"operator must be one of <, <=, >, >=, ==, between, change_pct; got {v!r}"
            )
        return v

    @field_validator("severity")
    @classmethod
    def _check_sev(cls, v: str) -> str:
        if v not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"severity must be low|medium|high|critical; got {v!r}")
        return v


class AlertAttribution(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str = ""
    drivers: list[str] = Field(default_factory=list)


class AlertProfile(BaseModel):
    model_config = ConfigDict(extra="allow")

    line_id: str
    line_name: str = ""
    rules: list[AlertRule] = Field(default_factory=list)
    attribution: list[AlertAttribution] = Field(default_factory=list)

    def rule_ids(self) -> list[str]:
        return [r.id for r in self.rules]

    def find_rule(self, rule_id: str) -> AlertRule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(rule_id)


# ─────────────────────────────────────────────────────────────────────────
# Triggered-alert DTOs
# ─────────────────────────────────────────────────────────────────────────


class AlertCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str
    rule_ids: list[str] | None = None  # None → all enabled
    dry_run: bool = False  # If true, don't store the triggered alerts


class TriggeredAlert(BaseModel):
    alert_id: str
    rule_id: str
    rule_name: str
    severity: str
    message: str
    line_id: str
    target: str
    target_name: str
    metric_value: float | str
    triggered_at: str
    acknowledged: bool = False


class AlertCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str
    checked_at: str
    rules_evaluated: int
    alerts_triggered: list[TriggeredAlert]
    summary: dict[str, int]


class AlertHistoryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_id: str | None
    total: int
    limit: int
    offset: int
    items: list[TriggeredAlert]


# ─────────────────────────────────────────────────────────────────────────
# Profile loading
# ─────────────────────────────────────────────────────────────────────────


def _profile_path(line_id: str) -> Path:
    root = get_project_root()
    return root / "business_lines" / line_id / "alerts.yaml"


@lru_cache(maxsize=32)
def _load_profile_cached(line_id: str) -> AlertProfile:
    return _load_profile_uncached(line_id)


def _load_profile_uncached(line_id: str) -> AlertProfile:
    path = _profile_path(line_id)
    if not path.exists():
        raise FileNotFoundError(
            f"alerts profile not found for line_id={line_id!r} "
            f"(expected at {path}). Add an alerts.yaml under "
            f"business_lines/{line_id}/ to enable the alert center."
        )
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"alerts.yaml for {line_id} must be a mapping, got {type(raw)}")
    raw.setdefault("line_id", line_id)
    return AlertProfile.model_validate(raw)


def load_profile(line_id: str) -> AlertProfile:
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
        if (d / "alerts.yaml").exists():
            out.append(d.name)
    return out


def clear_profile_cache() -> None:
    _load_profile_cached.cache_clear()


# ─────────────────────────────────────────────────────────────────────────
# In-memory alert store
# ─────────────────────────────────────────────────────────────────────────


class _AlertStore:
    """Process-local store. Single source of truth for triggered alerts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._alerts: dict[str, TriggeredAlert] = {}
        self._deleted: set[str] = set()

    def add(self, alert: TriggeredAlert) -> None:
        with self._lock:
            self._alerts[alert.alert_id] = alert

    def add_many(self, alerts: list[TriggeredAlert]) -> None:
        with self._lock:
            for a in alerts:
                self._alerts[a.alert_id] = a

    def get(self, alert_id: str) -> TriggeredAlert | None:
        with self._lock:
            return self._alerts.get(alert_id)

    def acknowledge(self, alert_id: str) -> TriggeredAlert | None:
        with self._lock:
            a = self._alerts.get(alert_id)
            if a is None or alert_id in self._deleted:
                return None
            acked = a.model_copy(update={"acknowledged": True})
            self._alerts[alert_id] = acked
            return acked

    def delete(self, alert_id: str) -> bool:
        with self._lock:
            if alert_id in self._deleted:
                return False
            if alert_id not in self._alerts:
                return False
            self._deleted.add(alert_id)
            return True

    def history(
        self,
        line_id: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[TriggeredAlert], int]:
        with self._lock:
            all_items = [a for a in self._alerts.values() if a.alert_id not in self._deleted]
        if line_id is not None:
            all_items = [a for a in all_items if a.line_id == line_id]
        # Newest first
        all_items.sort(key=lambda a: a.triggered_at, reverse=True)
        total = len(all_items)
        return all_items[offset : offset + limit], total

    def reset(self) -> None:
        with self._lock:
            self._alerts.clear()
            self._deleted.clear()


_STORE = _AlertStore()


# ─────────────────────────────────────────────────────────────────────────
# Target + history mocks
# ─────────────────────────────────────────────────────────────────────────


API_BASE = os.environ.get("FIN_BP_API_BASE", "http://127.0.0.1:8769")


def _http_json(path: str) -> dict[str, Any] | None:
    """Best-effort HTTP GET. Returns None on any error."""
    try:
        import json
        import urllib.request

        with urllib.request.urlopen(f"{API_BASE}{path}", timeout=0.5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _resolve_targets(line_id: str, rule: AlertRule) -> list[dict[str, str]]:
    """Return a list of {"id": ..., "name": ...} for the rule's scope.

    Falls back to a single line-level target if the API is unreachable.
    """
    scope = rule.scope
    if scope == "project":
        data = _http_json(f"/api/lines/{line_id}/projects")
        if data and isinstance(data.get("projects"), list) and data["projects"]:
            return [
                {
                    "id": str(p.get("project_id", "")),
                    "name": str(p.get("name", p.get("project_id", "—"))),
                }
                for p in data["projects"]
                if p.get("project_id")
            ]
    elif scope == "property":
        data = _http_json(f"/api/lines/{line_id}/properties")
        items = (data or {}).get("items") or (data or {}).get("properties") or []
        if items:
            return [
                {
                    "id": str(p.get("property_id", "")),
                    "name": str(p.get("name", p.get("property_id", "—"))),
                }
                for p in items
                if p.get("property_id")
            ]
    # Fallback: single line-level target
    return [{"id": line_id, "name": line_id}]


def _indicator_seed(indicator_id: str) -> int:
    return sum((i + 1) * ord(c) for i, c in enumerate(indicator_id)) & 0xFFFF


def _mock_periods(target_id: str, indicator_id: str, n: int = 6) -> list[float]:
    """Deterministic per-target mock period history, descending in time.

    The current value is `periods[-1]`; the previous is `periods[-2]`,
    etc. Range and direction are tuned per indicator so different rules
    fire and don't fire predictably across tests.
    """
    seed = (sum((i + 1) * ord(c) for i, c in enumerate(target_id + indicator_id)) & 0xFFFF)
    out: list[float] = []
    for i in range(n):
        if indicator_id in {"dynamic_irr", "payment_completion", "dedup_rate",
                            "collection_rate", "occupancy_rate", "renewal_rate"}:
            # Mix of low / mid / high values depending on the seed
            # so different rules fire for different (target, indicator)
            # combinations.
            bucket = seed % 6
            if bucket == 0:
                # IRR-like: drops well below 0.10 (fires irr_below_threshold)
                base = 0.05 + 0.02 * (i % 2)
            elif bucket == 1:
                # payment_completion: steep drop in last period (fires payment_drop)
                base = 0.80 - 0.12 * (i + 1)
            elif bucket == 2:
                # dedup_rate: < 0.50 for many periods (fires dedup_stall, consecutive)
                base = 0.30 + 0.05 * (i % 2)
            elif bucket == 3:
                # mid: 0.10..0.15 (fires irr_between_band)
                base = 0.10 + 0.02 * (i % 3)
            elif bucket == 4:
                # volatile: 0.40..0.80 with noise (rarely fires strict rules)
                base = 0.45 + 0.20 * (((seed * (i + 1)) & 0xF) / 16.0)
            else:
                # high stable: 0.75+ (fires NO rules with default thresholds)
                base = 0.75 + 0.05 * (i % 2)
            noise = ((seed * (i + 1) * 2654435761) & 0xFFFF) / 65535.0 - 0.5
            out.append(round(max(0.0, min(1.0, base + noise * 0.02)), 6))
        elif indicator_id in {"vacancy_rate"}:
            # vacancy depends on the seed: 30% high (>0.15) to fire vacancy_spike
            if seed % 3 == 0:
                base = 0.18 + 0.03 * i  # rising vacancy
            else:
                base = 0.05 + 0.02 * i
            out.append(round(min(0.50, max(0.0, base)), 6))
        elif indicator_id in {"noi"}:
            # NOI can drop over time (fires change_pct for noi_drop)
            base = 1000.0 - 80 * i + (seed % 200)
            out.append(round(base, 2))
        elif indicator_id in {"channel_fee_ratio"}:
            out.append(round(0.05 + 0.01 * (seed % 5) + 0.005 * i, 6))
        elif indicator_id in {"efficiency"}:
            # Half the time below 200 (fires efficiency_below_band)
            base = 150.0 + (seed % 200)
            out.append(round(base, 2))
        elif indicator_id in {"owner_vacancy_days"}:
            # 50% over 60 (fires vacancy_days_high)
            base = 30.0 + (seed % 80) + 5 * i
            out.append(round(base, 2))
        elif indicator_id in {"benchmark_gap_pct"}:
            # 50% under -0.10 (fires benchmark_gap_negative)
            base = -0.15 + 0.01 * i + ((seed * (i + 1)) & 7) / 1000
            out.append(round(base, 6))
        elif indicator_id in {"avg_deal_rent"}:
            out.append(round(320.0 + (seed % 50) - 5 * i, 2))
        elif indicator_id in {"redlines"}:
            # 1-in-3 chance the value is "red" (fires redline_breach)
            bucket = (seed + i) % 9
            if bucket in (2, 5):
                out.append("red")
            elif bucket in (1, 4, 7):
                out.append("yellow")
            else:
                out.append("green")
        else:
            out.append(1.0)
    return out


# ─────────────────────────────────────────────────────────────────────────
# Operator evaluation
# ─────────────────────────────────────────────────────────────────────────


def _to_number(x: Any) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return math.nan
    return math.nan


def _compare(op: str, value: float, threshold: Any) -> bool:
    if op == "between":
        if not isinstance(threshold, (list, tuple)) or len(threshold) != 2:
            return False
        lo, hi = float(threshold[0]), float(threshold[1])
        if lo > hi:
            lo, hi = hi, lo
        return lo <= value <= hi
    if op == "change_pct":
        # Special-cased at call site (we pass threshold directly)
        if not isinstance(threshold, (int, float)):
            return False
        return value <= float(threshold)
    if op == "==":
        if isinstance(threshold, (int, float)):
            return value == float(threshold)
        return str(value) == str(threshold)
    t = _to_number(threshold)
    if math.isnan(t):
        return False
    if op == "<":
        return value < t
    if op == "<=":
        return value <= t
    if op == ">":
        return value > t
    if op == ">=":
        return value >= t
    return False


def _consecutive_holds(
    op: str,
    threshold: Any,
    periods: list[float],
    n: int,
) -> int:
    """Return the count of consecutive trailing periods for which the
    condition holds. If `n` consecutive periods hold, returns `n` (or
    more). Otherwise returns 0.
    """
    if n <= 0 or not periods:
        return 0
    count = 0
    for v in reversed(periods):
        if _compare(op, v, threshold):
            count += 1
            if count >= n:
                return count
        else:
            break
    return 0


# ─────────────────────────────────────────────────────────────────────────
# Message templating
# ─────────────────────────────────────────────────────────────────────────


def _render_message(
    template: str,
    *,
    target: str,
    target_name: str,
    indicator_id: str,
    value: Any,
    delta_pct: float | None = None,
    n: int | None = None,
) -> str:
    """Safely render a message template. Unknown keys render as empty
    strings; format errors fall back to the raw template + a hint."""

    class _SafeDict(dict):
        def __missing__(self, key: str) -> str:  # type: ignore[override]
            return ""

    scope_label = "project" if target != target_name and target else target_name
    mapping = _SafeDict(
        project=target_name,
        property=target_name,
        target=target_name,
        target_id=target,
        indicator=indicator_id,
        value=value,
        delta_pct=delta_pct if delta_pct is not None else 0.0,
        n=n if n is not None else 0,
    )
    try:
        return template.format_map(mapping)
    except Exception:
        return f"[alert render error] {template} | target={target_name} value={value}"


# ─────────────────────────────────────────────────────────────────────────
# Core check
# ─────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _eval_rule(
    profile: AlertProfile,
    rule: AlertRule,
) -> list[TriggeredAlert]:
    """Evaluate one rule against every target. Returns triggered alerts."""
    targets = _resolve_targets(profile.line_id, rule)
    triggered: list[TriggeredAlert] = []
    for tgt in targets:
        periods = _mock_periods(tgt["id"], rule.indicator_id, n=6)
        current = periods[-1] if periods else 0.0

        if rule.operator == "change_pct":
            # Compare current vs previous period.
            if len(periods) < 2:
                continue
            prev = periods[-2]
            if prev == 0:
                continue
            delta_pct = (current - prev) / prev
            value_for_msg = current
            fires = _compare("change_pct", delta_pct, rule.threshold)
            metric_value: float | str = round(current, 6)
            if fires:
                triggered.append(
                    TriggeredAlert(
                        alert_id=str(uuid.uuid4()),
                        rule_id=rule.id,
                        rule_name=rule.name or rule.id,
                        severity=rule.severity,
                        message=_render_message(
                            rule.message_template,
                            target=tgt["id"],
                            target_name=tgt["name"],
                            indicator_id=rule.indicator_id,
                            value=value_for_msg,
                            delta_pct=round(delta_pct, 6),
                            n=2,
                        ),
                        line_id=profile.line_id,
                        target=tgt["id"],
                        target_name=tgt["name"],
                        metric_value=metric_value,
                        triggered_at=_now_iso(),
                    )
                )
            continue

        if rule.consecutive is not None and rule.consecutive > 0:
            cnt = _consecutive_holds(rule.operator, rule.threshold, periods, rule.consecutive)
            if cnt < rule.consecutive:
                continue
            # fire
            triggered.append(
                TriggeredAlert(
                    alert_id=str(uuid.uuid4()),
                    rule_id=rule.id,
                    rule_name=rule.name or rule.id,
                    severity=rule.severity,
                    message=_render_message(
                        rule.message_template,
                        target=tgt["id"],
                        target_name=tgt["name"],
                        indicator_id=rule.indicator_id,
                        value=current,
                        n=cnt,
                    ),
                    line_id=profile.line_id,
                    target=tgt["id"],
                    target_name=tgt["name"],
                    metric_value=round(current, 6) if isinstance(current, (int, float)) else current,
                    triggered_at=_now_iso(),
                )
            )
            continue

        # Plain operator
        if not _compare(rule.operator, current, rule.threshold):
            continue
        triggered.append(
            TriggeredAlert(
                alert_id=str(uuid.uuid4()),
                rule_id=rule.id,
                rule_name=rule.name or rule.id,
                severity=rule.severity,
                message=_render_message(
                    rule.message_template,
                    target=tgt["id"],
                    target_name=tgt["name"],
                    indicator_id=rule.indicator_id,
                    value=current,
                ),
                line_id=profile.line_id,
                target=tgt["id"],
                target_name=tgt["name"],
                metric_value=round(current, 6) if isinstance(current, (int, float)) else current,
                triggered_at=_now_iso(),
            )
        )
    return triggered


def check(
    profile: AlertProfile, req: AlertCheckRequest
) -> AlertCheckResult:
    """Run the alert check and (unless dry_run) persist the triggered alerts."""
    wanted = set(req.rule_ids) if req.rule_ids else None
    rules = [r for r in profile.rules if r.enabled and (wanted is None or r.id in wanted)]
    all_triggered: list[TriggeredAlert] = []
    for r in rules:
        all_triggered.extend(_eval_rule(profile, r))
    if not req.dry_run:
        _STORE.add_many(all_triggered)
    summary = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for a in all_triggered:
        summary[a.severity] = summary.get(a.severity, 0) + 1
    return AlertCheckResult(
        line_id=profile.line_id,
        checked_at=_now_iso(),
        rules_evaluated=len(rules),
        alerts_triggered=all_triggered,
        summary=summary,
    )


# ─────────────────────────────────────────────────────────────────────────
# Public store helpers
# ─────────────────────────────────────────────────────────────────────────


def acknowledge(alert_id: str) -> TriggeredAlert | None:
    return _STORE.acknowledge(alert_id)


def delete(alert_id: str) -> bool:
    return _STORE.delete(alert_id)


def history(
    line_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[TriggeredAlert], int]:
    return _STORE.history(line_id, limit, offset)


def reset_store() -> None:
    """Test helper: drop the in-memory store."""
    _STORE.reset()


def get_store() -> _AlertStore:
    """For tests that need to inspect the store directly."""
    return _STORE
