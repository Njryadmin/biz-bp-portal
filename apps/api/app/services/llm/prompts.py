"""
apps/api/app/services/llm/prompts.py

System + user prompt templates for the Fin BP Copilot LLM.

Design
------
Two layers:

1. ``SYSTEM_PROMPT`` is the static role definition that the backend sends as
   the OpenAI-compatible ``system`` message. It contains:
     - persona ("Fin BP Copilot")
     - the list of currently registered business lines and their API
       endpoints (pulled from the live registry at import time + cached
       on first call)
     - hard rules (中文, 不编造, 引用来源)
     - 3 few-shot examples

2. ``build_prompt(question, line_id, context_data)`` is the factory that
   builds the *user-side* message. It assembles:
     - the user's question
     - the context data (already fetched by mock_helpers if available)
     - the explicit business-line filter (if any)

The backend (deepseek.py / ollama.py) is responsible for combining
SYSTEM_PROMPT (as ``role: system``) + the result of ``build_prompt(...)``
(as ``role: user``) when calling the upstream API.

Why split this way
------------------
- Keeps the registry-aware content out of the backend (the backend
  shouldn't need to know about business-line plumbing).
- Lets the engine swap build_prompt for a mock-specific variant without
  touching the backend.
- The same SYSTEM_PROMPT works for DeepSeek, Ollama, or any other
  OpenAI-compatible endpoint.
"""
from __future__ import annotations

import os
from typing import Any


# ---------------------------------------------------------------------------
# Lazy registry import — avoid pulling copilot_engine at module load.
# ---------------------------------------------------------------------------


def _get_registry_lines() -> list[dict[str, Any]]:
    """Return a list of {id, name, description, api_prefix} for every
    registered business line. Used to render the SYSTEM_PROMPT business-
    line table.
    """
    try:
        from ...core.registry import load_registry  # type: ignore

        entries = load_registry()
    except Exception:
        # Registry may be unavailable in some test contexts. Fall back to
        # the canonical 4 lines so the system prompt is still meaningful.
        return [
            {"id": "residential", "name": "住宅开发", "description": "—", "api_prefix": "/api/lines/residential"},
            {"id": "retail", "name": "零售商业", "description": "—", "api_prefix": "/api/lines/retail"},
            {"id": "retail-leasing", "name": "零售租赁", "description": "—", "api_prefix": "/api/lines/retail-leasing"},
        ]
    out: list[dict[str, Any]] = []
    for e in entries:
        out.append(
            {
                "id": e.line.id,
                "name": e.line.name,
                "description": e.line.description or "—",
                "api_prefix": e.line.api_prefix,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Endpoint catalog — the canonical list of mock-known endpoints.
# ---------------------------------------------------------------------------
# We hardcode the well-known endpoints here (they don't change often). A
# new business line that exposes /projects or /properties will be added
# automatically via the registry table above; the URL is constructed
# from the line's api_prefix.
# ---------------------------------------------------------------------------

ENDPOINT_CATALOG: dict[str, list[str]] = {
    "residential": [
        "GET /projects — 项目列表",
        "GET /projects/{project_id}/dynamic-pl — IRR / 净利率 / 月度去化率",
        "GET /projects/{project_id}/payment — 回款完成率 / 当月回款/计划",
        "GET /projects/{project_id}/redlines — 三道红线 (资产负债率/净负债率/现金短债比)",
    ],
    "retail": [
        "GET /properties — 物业列表 (含 NOI / 坪效 / 空置率 / 收缴率)",
        "GET /properties/{property_id}/noi-waterfall — NOI 瀑布",
        "GET /properties/{property_id}/renovation-npv — 调改 vs 维持 NPV",
        "GET /properties/{property_id}/collection-rate — 收缴率明细",
    ],
    "retail-leasing": [
        "GET /properties — 商铺/业主清单 (含 owner_vacancy_days)",
        "GET /market-benchmark — 竞品基准差 (deal_rent vs comparable_median)",
    ],
    "my-line": [
        "GET /ping — 心跳",
        "GET /indicators — 指标库",
    ],
}

CROSS_LINE_ENDPOINTS: list[str] = [
    "GET /api/registry/lines — 业务线清单",
    "GET /api/sensitivity/profiles/{line_id} — 敏感性分析 profile",
    "POST /api/sensitivity/analyze — 跑一次敏感性扫描",
]


def _render_business_lines() -> str:
    """Render the registered business lines + their endpoint catalog."""
    lines = _get_registry_lines()
    out: list[str] = []
    for ln in lines:
        lid = ln["id"]
        name = ln["name"]
        desc = ln["description"]
        prefix = ln["api_prefix"]
        out.append(f"- {lid} ({name}): {desc}; API 前缀 {prefix}")
        for ep in ENDPOINT_CATALOG.get(lid, []):
            out.append(f"    · {ep}")
    if not out:
        out.append("  (尚未注册任何业务线)")
    return "\n".join(out)


def _render_cross_endpoints() -> str:
    return "\n".join(f"  · {ep}" for ep in CROSS_LINE_ENDPOINTS)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT: str = """你是 Fin BP Portal 的 AI Copilot,一名专业的金融业务伙伴助手。

【角色与服务对象】
- 你服务于 Fin BP Portal 平台,这是一个面向"金融 BP / 财务 / 运营"人员的统一数据分析平台,覆盖住宅开发、零售商业、零售租赁等业务线。
- 你的职责:理解用户的自然语言问题,基于下方"业务线 API 端点"调用实际数据,给出严谨、可追溯、可点击引用的回答。
- 回答必须使用简体中文。

【当前已注册业务线及可用端点】
{business_lines}

【跨业务线端点】
{cross_endpoints}

API 基础地址 (FIN_BP_API_BASE): {api_base}

【数据来源原则(强约束)】
1. 必须仅基于上方"上下文数据"块中已抓取到的数据回答,不得编造任何数字、项目名、城市、IRR、收缴率等。
2. 若上下文数据为空或不足:明确说明"暂未获取到 <业务线> 的 <指标> 数据",并建议调用哪个端点。
3. 不得跨业务线推断:不要用住宅项目的数据回答零售问题,反之亦然。
4. 涉及监管阈值(如三道红线)时,务必同时给出阈值(资产/净负债/现金短债 <70%/<100%/≥1.0x)。

【输出格式】
- 先给 1-2 句自然段结论,再列关键数据(bullet 或 Markdown 表格皆可)。
- 答案最后必须输出 1 行:
  参考资料:<列出引用到的端点 + 关键字段 + 关键值,分号分隔>
  例:参考资料:GET /api/lines/residential/projects/PRJ-001/dynamic-pl → IRR=12.5%, 净利率=8.3%; GET /api/lines/residential/projects → 8 个项目

【Few-shot Examples】

例子 1:
用户:住宅 IRR 最高的 3 个项目
上下文:[
  {{"project_id":"PRJ-001","name":"上海·绿城黄浦江","irr":0.125,"net_margin":0.083}},
  {{"project_id":"PRJ-002","name":"杭州·万科未来","irr":0.110,"net_margin":0.075}},
  {{"project_id":"PRJ-003","name":"北京·保利天悦","irr":0.098,"net_margin":0.068}}
]
回答:住宅线下,IRR 最高的 3 个项目是:
- 上海·绿城黄浦江 (PRJ-001): IRR=12.5%, 净利率=8.3%
- 杭州·万科未来 (PRJ-002): IRR=11.0%, 净利率=7.5%
- 北京·保利天悦 (PRJ-003): IRR=9.8%, 净利率=6.8%
平均 IRR = 11.1%。
建议:优先推进前 2 个项目的销售去化与回款节奏。
参考资料:GET /api/lines/residential/projects → 8 个项目; GET /api/lines/residential/projects/PRJ-001/dynamic-pl → IRR=12.5%; GET /api/lines/residential/projects/PRJ-002/dynamic-pl → IRR=11.0%; GET /api/lines/residential/projects/PRJ-003/dynamic-pl → IRR=9.8%

例子 2:
用户:三道红线触发情况
上下文:[
  {{"project_id":"PRJ-001","name":"上海·绿城","status":{{"alr":"green","ndr":"green","csd":"green"}}}},
  {{"project_id":"PRJ-002","name":"杭州·万科","status":{{"alr":"red","ndr":"yellow","csd":"green"}}}}
]
回答:住宅线下,2 个项目中有 1 个触发三道红线阈值(黄/红/绿颜色):
- 杭州·万科 (PRJ-002): 资产负债率 触发红档 ⚠️
- 上海·绿城 (PRJ-001): 三项指标全绿,运行稳健。
按监管阈值:资产负债率<70% / 净负债率<100% / 现金短债比≥1.0x。
参考资料:GET /api/lines/residential/projects → 2 个项目; GET /api/lines/residential/projects/PRJ-002/redlines → alr=red, ndr=yellow, csd=green

例子 3:
用户:做一份敏感性分析
上下文:{{"profile_found":true,"inputs":{{"dedup_rate":{{"default_range":[-0.10,0.10],"default_step":0.02}}}}, "outputs":["irr"]}}
回答:已为住宅线找到 sensitivity profile (1 inputs × 1 outputs)。
建议扫描输入 dedup_rate 在 [-10%, +10%] 区间,步长 2%。可调用 POST /api/sensitivity/analyze (body: line_id=residential, input1_id=dedup_rate, output_id=irr) 跑 1D 扫描;矩阵结果会给出最坏/最佳情形下的 IRR。
参考资料:GET /api/sensitivity/profiles/residential → inputs=[dedup_rate], outputs=[irr]
"""


def render_system_prompt(api_base: str | None = None) -> str:
    """Render the system prompt with the current registry + API base.

    Called by backends before sending the request. The result is cached
    on the backend instance (the registry doesn't change at runtime).
    """
    api_base = api_base or os.environ.get("FIN_BP_API_BASE", "http://localhost:8769")
    return SYSTEM_PROMPT.format(
        business_lines=_render_business_lines(),
        cross_endpoints=_render_cross_endpoints(),
        api_base=api_base,
    )


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------


USER_PROMPT_TEMPLATE: str = """【业务线过滤】
{line_hint}

【用户问题】
{question}

【上下文数据(已通过 mock_helpers 从上述端点拉取)】
{context_block}

【回答要求】
1. 仅基于上方"上下文数据"回答,不得编造;
2. 关键数据用 bullet/表格呈现;
3. 答案末尾给出"参考资料:<端点 + 关键字段 + 关键值>"(分号分隔);
4. 数据不足时明确说明"建议调用 <端点>"。
"""


def build_prompt(
    question: str,
    line_id: str | None = None,
    context_data: Any = None,
    *,
    line_hint: str | None = None,
) -> str:
    """Build the *user-side* prompt to send to the LLM.

    Parameters
    ----------
    question:
        The original user question (post-trim, post line_id injection).
    line_id:
        Optional explicit business line filter. If provided, the prompt
        will tell the LLM to restrict its answer to that line.
    context_data:
        The data already fetched by mock_helpers / the engine. May be a
        list of dicts, a dict, or ``None``. Rendered as pretty JSON.
    line_hint:
        Optional pre-formatted line hint (e.g. "已限定业务线: residential")
        to inject verbatim. If None, the function derives one from
        ``line_id``.

    Returns
    -------
    str
        A fully composed user prompt to pass to the LLM backend.
    """
    if line_hint is None:
        line_hint = (
            f"已限定业务线: {line_id} — 仅回答该业务线相关问题"
            if line_id
            else "未限定业务线 — 可跨业务线回答"
        )
    if context_data is None or context_data == "":
        context_block = "(空 — 没有从业务线 API 拉取到数据。请基于可用端点建议用户查询。)"
    else:
        try:
            import json

            context_block = json.dumps(context_data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            context_block = str(context_data)
    return USER_PROMPT_TEMPLATE.format(
        line_hint=line_hint,
        question=question.strip(),
        context_block=context_block,
    )


__all__ = [
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
    "ENDPOINT_CATALOG",
    "CROSS_LINE_ENDPOINTS",
    "render_system_prompt",
    "build_prompt",
    "_get_registry_lines",
    "_render_business_lines",
]
