"""
apps/api/app/services/llm/mock.py

Mock LLM backend — deterministic rule engine that turns a user question
into a templated answer plus a list of citations pointing at real
business-line API endpoints.

The mock backend is the DEFAULT. It has zero external dependencies (no
network), is fully deterministic, and is the test target. It must give
useful answers for at least 8 distinct intents (see INTENT_TEMPLATES).

CONTRACT
========
- `complete(prompt, ...)` is called by the Copilot engine with a fully-
  composed prompt that already includes the user's question. The mock
  IGNORES the prompt and works off the same `question` it was given at
  construction (or, if not provided, the last user line of the prompt).

  Why this design: the mock is a rule engine over the question string,
  not a generic LLM. We deliberately do NOT try to parse the system
  prompt — that would couple the mock to the prompt format and defeat
  the point of having a real backend swap in transparently.

- `name` is "mock".

- The engine does not call `embed()` — the mock returns [].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Intent templates
# ---------------------------------------------------------------------------
#
# Each template is a small function: (question, parsed) -> (answer, citations,
# chart_data, intent, confidence). The mock engine is a deterministic
# keyword/dispatch router — not an LLM.
#
# The Copilot engine feeds these templates the parsed question (line, intent,
# top_n, threshold, etc.) and the helper functions fetch real data via the
# shared HTTP client (see `llm/mock_helpers.py`).
#
# The engine in `copilot_engine.py` provides 8+ ready-to-use intents:
#   1.  residential_irr_top        — "住宅 IRR 最高"  (projects)
#   2.  residential_payment_low     — "住宅回款下降"  (projects)
#   3.  residential_redlines       — "住宅三道红线"  (projects)
#   4.  retail_noi_top             — "零售 NOI top"  (properties)
#   5.  retail_renovation_npv      — "零售调改 NPV"  (properties)
#   6.  retail_collection_low      — "零售收缴率"    (properties)
#   7.  leasing_vacancy_long       — "零售租赁空置期" (properties)
#   8.  leasing_benchmark_gap      — "零售租赁基准差" (properties)
#   9.  cross_line_overview        — "三业务线 KPI"  (registry + indicators)
#   10. sensitivity_analyze        — "做一份敏感性分析" (sensitivity)
#   11. fallback_unknown           — "我爱你" / gibberish
#   12. line_kpi_summary           — "<line> 的指标"  (generic)
# ---------------------------------------------------------------------------


@dataclass
class MockAnswer:
    """Result of one intent template.

    The Copilot engine wraps this into a full CopilotResponse.
    """

    answer: str
    intent: str
    confidence: float
    citations: list[dict[str, Any]] = field(default_factory=list)
    chart_data: dict[str, Any] | None = None
    debug: dict[str, Any] = field(default_factory=dict)


# Keyword patterns. Order matters — more specific patterns first.
_INTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # sensitivity
    ("sensitivity", re.compile(r"敏感|敏感性|sensitivity|what.?if|扰动|压力测|scenario", re.IGNORECASE)),
    # red lines
    ("redlines", re.compile(r"三道红线|红线|资产负债率.*触发|net_debt|cash_to_short_debt|杠杆|leverage", re.IGNORECASE)),
    # payment / 回款
    ("payment_low", re.compile(r"回款.*(下|低|不|差|降|慢)|payment.*(down|low|miss)|回款率低|回款完成", re.IGNORECASE)),
    # dedup / 去化
    ("dedup_low", re.compile(r"去化.*(低|下|慢|差|降)|售.*速度|dedup|sell.?through", re.IGNORECASE)),
    # IRR / NPV
    ("irr_top", re.compile(r"irr|内部收益|动态.*回报|投资回报率|top.*irr|最高的.*irr|irr.*最高|irr.*高|收益.*最", re.IGNORECASE)),
    # NOI
    ("noi_top", re.compile(r"noi|净.*营业|net.*operating|营业收入|营收|最高.*noi|noi.*最|noi.*高|高效|优.*物业", re.IGNORECASE)),
    # renovation NPV / 调改
    ("renovation", re.compile(r"调改|renovat|npv.*正|改造|装修|升级|翻新|redevelop", re.IGNORECASE)),
    # collection / 收缴
    ("collection", re.compile(r"收缴|收款|催收|collection|欠租|坏账|拖欠", re.IGNORECASE)),
    # vacancy / 空置 (零售租赁)
    ("vacancy", re.compile(r"空置|vacanc|空.*期|空窗", re.IGNORECASE)),
    # benchmark gap / 基准差
    ("benchmark", re.compile(r"基准|对标|benchmark|竞品|周边|同业|租.*基准", re.IGNORECASE)),
    # cross-line overview
    ("cross_overview", re.compile(r"三.*业务|所有.*业务|概览.*对比|cross|all.*lines|整体.*kpi|三业务线", re.IGNORECASE)),
    # line indicator summary
    ("line_indicators", re.compile(r"指标|有哪些.*kpi|kpis|指标库|indicators", re.IGNORECASE)),
    # comparison across lines
    ("compare", re.compile(r"对比|比较|versus|compare|vs\.?", re.IGNORECASE)),
]


# Resolved-line keywords. Map Chinese/English aliases to line_id. If none
# match, the line is None and the engine expands the question to all lines.
#
# Built dynamically at module import from `load_registry()` so any new
# business line added to `business_lines/registry.yaml` is automatically
# recognised. We seed a small hand-curated alias map for the 6 new
# business lines (valuation / advisory / office-leasing / investment /
# project-management / industrial) because the natural-language aliases
# for those lines are NOT in their manifest `name` field — they're
# shorthand terms the user would actually type ("估价", "代建", "REITs").
#
# NOTE: the assignment is at the BOTTOM of this file (after
# `build_line_keywords_from_registry` is defined) to avoid a
# forward-reference at import time. A module-level binding with the
# final dict is exposed as `_LINE_KEYWORDS`.


# Hand-curated alias seeds for the 6 newer business lines. These are
# the short Chinese/English nouns a user would actually type in a
# Copilot question — they are deliberately NOT taken from the manifest
# `name` field (which is the full department title, e.g. "写字楼租赁部")
# because nobody types "写字楼租赁部" — they type "写字楼" or "租赁".
# We still merge the manifest's id + name into the final list.
_LINE_ALIAS_SEEDS: dict[str, list[str]] = {
    # Original 4 lines
    "residential": ["住宅", "楼盘", "地产", "residential", "housing", "real.?estate", "项目"],
    "retail": ["零售", "商场", "购物中心", "retail", "mall", "物业"],
    "retail-leasing": ["零售租赁", "租赁", "商铺", "招租", "leasing", "rent"],
    "my-line": ["my.?line", "我的业务线", "试验线"],
    # 6 new lines — Chinese/English shorthand
    "valuation": [
        "估价", "评估", "估", "valuation", "appraisal",
        "抵押估价", "交易估价", "司法估价", "征收估价", "课税估价",
    ],
    "advisory": [
        "顾问", "咨询", "可行性研究", "拿地顾问",
        "advisory", "consulting", "consult", "advisor",
    ],
    "office-leasing": [
        "写字楼", "办公", "办公楼", "租赁代理", "写字楼租赁",
        "office", "leasing", "broker",
    ],
    "investment": [
        "投资", "基金", "reits", "REITs", "地产基金", "gp", "lp",
        "investment", "fund", "portfolio",
    ],
    "project-management": [
        "代建", "项目管理", "代建项目", "代建管理", "pm",
        "project", "construction.?management",
    ],
    "industrial": [
        "工业", "厂房", "仓库", "仓储", "物流", "物流园",
        "industrial", "warehouse", "logistics", "factory",
    ],
}


def _extract_description_keywords(description: str, max_n: int = 3) -> list[str]:
    """Pull 2-3 short, salient Chinese/English tokens out of a manifest
    description string. We split on common CJK delimiters and pick the
    first few non-trivial tokens. Used to enrich the auto-generated
    keyword list with domain vocabulary (e.g. "抵押/交易/征收" for
    valuation).
    """
    if not description:
        return []
    # Prefer split on Chinese commas / enumeration marks.
    # Try multiple separators; pick the one that gives the most parts
    # (most informative split). Also tries ":" and ";" since some
    # manifests use them.
    best: list[str] = []
    for sep in ("、", ",", "，", "/", ":", "：", ";", "；", " ", "。"):
        if sep in description:
            cand = [p.strip() for p in description.split(sep) if p.strip()]
            if len(cand) > len(best):
                best = cand
    if not best:
        best = [description]
    out: list[str] = []
    skip = {
        "围绕", "展开", "分析", "业务", "全流程", "的", "与", "和", "及",
        "、", "和", "等", "通过", "为", "在", "以", "提供",
    }
    for p in best:
        # Drop particles / generic verbs
        if len(p) < 2:
            continue
        if p in skip:
            continue
        # Drop long fragments (they hurt regex matching more than help
        # — descriptions include clauses like "投资顾问、可行性研究"
        # but also full sentences). Cap at 8 chars.
        if len(p) > 8:
            continue
        out.append(p)
        if len(out) >= max_n:
            break
    return out


def _short_name(name: str) -> list[str]:
    """Decompose a manifest display name into searchable short tokens.

    Example: "写字楼租赁部" → ["写字楼租赁部", "写字楼租赁", "写字楼", "租赁"]
    "地产投资部"       → ["地产投资部", "地产投资", "投资", "地产"]
    "地产项目管理部"   → ["地产项目管理部", "地产项目", "项目管理", "项目"]
    "工业地产部"       → ["工业地产部", "工业地产", "工业", "地产"]
    """
    if not name:
        return []
    out = [name]
    if name.endswith("部"):
        out.append(name[:-1])
    # Also strip common 2-char suffixes that aren't useful for matching
    for suffix in ("分析",):
        if name.endswith(suffix):
            out.append(name[: -len(suffix)])
    return out


def build_line_keywords_from_registry() -> dict[str, list[str]]:
    """Build the {line_id: [keyword, ...]} map dynamically from the
    live registry.

    For each line we collect:
      1. Hand-curated aliases from `_LINE_ALIAS_SEEDS` (if any).
      2. The line.id slug itself (e.g. "valuation").
      3. The display name + short-name decompositions (e.g. "估价部"
         from manifest, plus "估价" derived from suffix stripping).
      4. Up to 3 domain keywords from the manifest `description`.

    This runs ONCE at module import. If a new business line is added
    to `business_lines/registry.yaml` without alias seeds, the engine
    still picks it up via its id + name (just with weaker matching).
    """
    try:
        # Lazy import — `core.registry` pulls yaml + pydantic_settings,
        # and the mock backend is sometimes imported in unit tests
        # without a real registry on disk.
        from ...core.registry import load_registry
        entries = load_registry()
    except Exception:  # noqa: BLE001 — fall back to seeds only
        entries = []

    out: dict[str, list[str]] = {}
    for entry in entries:
        lid = entry.line.id
        kws: list[str] = []

        # 1. Hand-curated seeds
        for kw in _LINE_ALIAS_SEEDS.get(lid, []):
            if kw not in kws:
                kws.append(kw)

        # 2. The id itself, e.g. "valuation", "office-leasing"
        if lid and lid not in kws:
            kws.append(lid)
        # Plus the unhyphenated variant so "officeleasing" also matches
        if lid and "-" in lid:
            unhyph = lid.replace("-", "")
            if unhyph not in kws:
                kws.append(unhyph)

        # 3. Display name + short-name decompositions
        for tok in _short_name(entry.line.name):
            if tok and tok not in kws:
                kws.append(tok)

        # 4. A few description keywords
        for tok in _extract_description_keywords(entry.line.description):
            if tok and tok not in kws:
                kws.append(tok)

        out[lid] = kws

    # If the registry loader returned nothing (e.g. running this module
    # outside the project), still surface the seed-only map so the
    # original 4 lines keep working.
    if not out:
        return {lid: list(v) for lid, v in _LINE_ALIAS_SEEDS.items()}

    return out


# Module-level alias for readability / monkey-patching in tests.
_build_line_keywords_from_registry = build_line_keywords_from_registry


# Resolved-line keyword map. Computed once at module import.
# `parse_question()` reads this dict at call time (no closure capture),
# so a reload of this module immediately picks up any registry change.
_LINE_KEYWORDS: dict[str, list[str]] = build_line_keywords_from_registry()


def parse_question(question: str) -> dict[str, Any]:
    """Extract intent + line from a free-form question.

    Returns a dict with keys: line (str|None), intent (str), top_n (int),
    threshold (float|None). All heuristic.
    """
    q = (question or "").strip()
    lower = q.lower()
    parsed: dict[str, Any] = {
        "line": None,
        "intent": "fallback_unknown",
        "top_n": 3,
        "threshold": None,
    }

    # 1. Line detection (most specific first).
    #    We score each line by (a) the longest keyword that hit and
    #    (b) the total count of keyword hits. The tie-breaker is the
    #    LONGEST matching keyword, not the first-inserted line — this
    #    prevents generic words like "项目" (residential) from
    #    out-ranking specific words like "投资" (investment) when both
    #    match in the same question.
    line_longest: dict[str, int] = {}
    line_hits: dict[str, int] = {}
    for line_id, kws in _LINE_KEYWORDS.items():
        for kw in kws:
            try:
                if re.search(kw, q, re.IGNORECASE):
                    line_hits[line_id] = line_hits.get(line_id, 0) + 1
                    if len(kw) > line_longest.get(line_id, 0):
                        line_longest[line_id] = len(kw)
            except re.error:
                continue
    if line_hits:
        # Sort by (longest keyword desc, total hits desc, line id asc
        # for determinism) and pick the winner.
        winner = sorted(
            line_hits.keys(),
            key=lambda lid: (-line_longest.get(lid, 0), -line_hits[lid], lid),
        )[0]
        parsed["line"] = winner

    # 2. Intent detection (first pattern that hits wins)
    for intent, pattern in _INTENT_PATTERNS:
        if pattern.search(q):
            parsed["intent"] = intent
            break

    # 3. "top N" or "前N" extraction
    m = re.search(r"(top|前|最高)\s*(\d+)|(\d+)\s*(个|位|条|项|物业|项目)", lower)
    if m:
        n = m.group(2) or m.group(3)
        try:
            parsed["top_n"] = max(1, min(10, int(n)))
        except (TypeError, ValueError):
            pass

    # 4. Threshold extraction (e.g. "低于 95%" or "less than 0.9")
    m = re.search(r"(低于|小于|<|less.?than)\s*([\d.]+)\s*(%|x|倍)?", lower)
    if m:
        try:
            v = float(m.group(2))
            if m.group(3) == "%":
                v = v / 100.0
            parsed["threshold"] = v
        except (TypeError, ValueError):
            pass

    return parsed


class MockBackend:
    """The mock LLM backend.

    It exposes the same async `complete()` and `embed()` API as the real
    backends, but `complete()` is a thin wrapper: it inspects the question
    (extracted from the prompt by the Copilot engine), picks an intent
    template, runs it, and returns the result. The Copilot engine then
    packages the citations and chart_data.

    The mock also exposes a synchronous `answer()` that the Copilot engine
    calls when it has already parsed the question — this is the testable
    surface.
    """

    name: str = "mock"

    def __init__(self) -> None:
        # Lazy import to avoid pulling copilot_engine at module load
        # (circular import risk if copilot_engine ever needs LLMBackend
        # for typing).
        from . import mock_helpers  # noqa: F401 — imported for side-effect cache
        self._helpers = mock_helpers

    async def complete(self, prompt: str, *, max_tokens: int = 1024) -> str:
        """Mock complete. Returns the answer text only (no citations).

        The Copilot engine should call `answer()` directly when it wants
        the full MockAnswer. This `complete()` is provided for symmetry
        with the real backends and for unit tests.
        """
        # Extract the LAST user turn (after "用户: " or "Question: ")
        q = self._extract_user_question(prompt)
        result = self.answer(q)
        return result.answer

    async def embed(self, text: str) -> list[float]:
        """No-op embedding. Returns empty list."""
        return []

    def answer(self, question: str, line_override: str | None = None) -> MockAnswer:
        """Return a structured answer for `question`.

        The Copilot engine calls this and uses the full structure
        (citations, chart_data, debug) to build the CopilotResponse.

        `line_override` is the explicit `line_id` from the request body
        (if any). When set, it wins over the in-question line parser.
        """
        parsed = parse_question(question)
        line = line_override or parsed.get("line")
        intent = parsed.get("intent", "fallback_unknown")
        top_n = int(parsed.get("top_n") or 3)
        threshold = parsed.get("threshold")

        # Dispatch to the helper. The helper knows the API base and does
        # the actual data fetch. We do NOT want to bloat this file with
        # HTTP code.
        result = self._helpers.dispatch(
            intent=intent,
            line=line,
            top_n=top_n,
            threshold=threshold,
            raw_question=question,
        )
        # Always include the parsed-question debug info so the frontend
        # can show "what intent the engine understood" — useful for demo.
        result.debug.setdefault("parsed", parsed)
        result.debug.setdefault("backend", "mock")
        if line_override is not None:
            result.debug["line_override"] = line_override
        return result

    @staticmethod
    def _extract_user_question(prompt: str) -> str:
        """Pull the user's last question out of a composed prompt.

        Supports several delimiters used by the engine prompt builder.
        Falls back to the last non-empty line.
        """
        for marker in ("\n用户:", "\nUser:", "\nQ:", "用户:", "Question:"):
            idx = prompt.rfind(marker)
            if idx >= 0:
                tail = prompt[idx + len(marker):].strip()
                # Stop at the next section marker.
                for stop in ("\n\n", "\nAssistant:", "\nA:"):
                    s = tail.find(stop)
                    if s >= 0:
                        tail = tail[:s]
                return tail.strip()
        # Fallback: last non-empty line.
        for line in reversed(prompt.splitlines()):
            if line.strip():
                return line.strip()
        return prompt.strip()
