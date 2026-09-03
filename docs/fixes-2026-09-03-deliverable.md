# fin-bp-portal — 2026-09-03 修复交付

**范围**：修 2 个真实遗留问题——(1) `init_db()` 挂起导致 API 启动看起来"卡死"，(2) Copilot mock 的 `_LINE_KEYWORDS` 硬编码 4 条原业务线，新加的 6 条业务线被识别到 line_id 但 intent 派发回退到 `fallback_unknown`（其实更精确的描述：line_id 已能从 registry 读到，但**新 6 条业务线在 `_LINE_KEYWORDS` 里完全不存在**，导致"估价"等中文别名无法映射到 `valuation`，因而只能借 `line_override` 才走对派发）。

**根分支**：`fin-bp-portal/`（本仓库的工作树）

**Result: PASS**（两个修法均验证通过；6+ 条 curl 输出已采集）

---

## 问题 1 — `init_db()` 挂起

### 症状
- `apps/api/app/main.py` 的 `lifespan` 调 `await init_db()`，进而 `app.db.bootstrap.ensure_raw_schema()` 走 `create_async_engine(database_url)` 建连接。
- 当 PostgreSQL 未启动（`localhost:5432` 无响应）时，asyncpg 的默认 `connect()` **无 TCP 超时**——按 OS 默认 TCP 重传策略卡 60–120 秒；uvicorn 的 lifespan 在 `await init_db()` 上挂着，整个启动看起来死锁。
- 复现命令：杀掉所有 python → 启动 API（PG 没起）→ 永远看不到 `Uvicorn running on http://...`。

### 根因
1. `apps/api/app/db/session.py:28` 的 `create_async_engine(...)` 缺 `connect_args`，asyncpg 走默认无界超时。
2. `ensure_raw_schema()` 内部没有 `asyncio.wait_for` 兜底；`init_db()` 的 try/except 也只捕获 `Exception`，但**连接挂起不是 Exception，是 OS-level 阻塞**。
3. `main.py` 的 `lifespan` 把 `init_db()` 失败与否直接抛回 uvicorn——一旦失败整个 startup 异常退出。

### 修法（三层防御）
1. **`apps/api/app/db/session.py`** — 给 `create_async_engine` 加 `connect_args={"timeout": 2}`，让 asyncpg 在 2 秒内放弃 TCP connect。
   ```python
   def _build_connect_args() -> dict:
       return {"timeout": 2}   # asyncpg 客户端 connect timeout (秒)
   ```
2. **`apps/api/app/db/bootstrap.py`** — 新增 `DB_BOOTSTRAP_TIMEOUT_S: float = 2.0` 常量；保留函数本身只做 DDL，由调用方在 `init_db` 里 `asyncio.wait_for` 套上。
3. **`apps/api/app/db/session.py` 的 `init_db()`** — 用 `asyncio.wait_for(ensure_raw_schema(), timeout=DB_BOOTSTRAP_TIMEOUT_S)` 兜底；超时分两路处理：`asyncio.TimeoutError` → `logger.warning("init_db: ensure_raw_schema timed out after %.1fs (DB unreachable, continuing without DB)", ...)`；其他异常 → 走原 `except Exception`。
4. **`apps/api/app/main.py` 的 `lifespan`** — 在 `await init_db()` 外面再包一层 try/except，最终防线。

### 验证
**复现命令**（PG 没起的状态下）：
```
Get-Process -Name python | Stop-Process -Force
$env:PYTHONPATH = "C:\Users\mozzi\...\apps\api"
$env:FIN_BP_PROJECT_ROOT = "C:\Users\mozzi\...\fin-bp-portal"
Start-Process -FilePath python -ArgumentList -m,uvicorn,app.main:app,--port,8769,--log-level,info ...
```

**实测**：
| 指标 | 修复前 | 修复后 | 目标 |
|---|---|---|---|
| Uvicorn ready 时间 | 60–120s 挂死 | **3.35s** | < 5s ✅ |
| init_db 失败表现 | startup 崩溃 | `WARNING` 后继续 | 不杀 API ✅ |
| 日志样例 | (无) | `WARNING [app.db.session] init_db: ensure_raw_schema timed out after 2.0s (DB unreachable, continuing without DB)` | 见上 ✅ |

完整日志路径：`apps/api/api_final.log` + `apps/api/api_final.log.err`，关键行：
```
2026-09-03T10:17:26 INFO  Mounted business line 'industrial' ...
2026-09-03T10:17:26 INFO  Discovered 3 scraper(s): ...
2026-09-03T10:17:28 WARNING init_db: ensure_raw_schema timed out after 2.0s (DB unreachable, continuing without DB)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8769 (Press CTRL+C to quit)
```

---

## 问题 2 — `_LINE_KEYWORDS` 硬编码 4 条原业务线

### 症状
- `apps/api/app/services/llm/mock.py:111` 的 `_LINE_KEYWORDS` 是写死的 4 条 dict：`residential / retail / retail-leasing / my-line`。
- 新加的 6 条业务线（`valuation / advisory / office-leasing / investment / project-management / industrial`）在 `business_lines/registry.yaml` 已注册（`/api/registry/lines` 返回 10 条），但**中文别名完全无法被解析**——例如问"估价部 IRR 最高"时，line 解析为 `None`，引擎落进 `compare` 意图（再回退到 fallback），前端拿到 `fallback_unknown`。
- 即使显式传 `line_id=valuation`，因为 `_LINE_KEYWORDS` 不含 `valuation`，auto-detect 还是识别不到，mock 引擎得靠 `line_override` 才能走对派发。

### 根因
`_LINE_KEYWORDS` 是字面常量 dict，没有任何从 `load_registry()` 动态生成的逻辑。Manifest 里的 `name` 字段（"估价部"/"地产投资部"/"工业地产部"）其实**够用**——只要按 "id + name + 短名后缀拆分 + description 关键词" 动态拼一下就成。

### 修法
1. **`mock.py` 文件顶部加**：
   - `_LINE_ALIAS_SEEDS: dict[str, list[str]]` ——给 6 条新业务线**手工 seed** 真实用户会打的简短中文/英文别名（"估价"/"评估"/"顾问"/"咨询"/"写字楼"/"租赁代理"/"投资"/"基金"/"REITs"/"代建"/"项目管理"/"工业"/"厂房"/"仓库"/"物流"）。保留原 4 条 seeds。
   - `_extract_description_keywords(description, max_n=3)` ——从 manifest `description` 抽 2–3 个短 token，跳过 "围绕/展开/分析/业务/全流程" 等停用词，cap 长度 ≤ 8 字符。
   - `_short_name(name)` ——把"写字楼租赁部"拆成 `["写字楼租赁部", "写字楼租赁"]`（去"部"后缀）。
2. **`build_line_keywords_from_registry()` 函数**——动态构建逻辑：
   - 对每条 line，先放 seeds，再放 `line.id`（"valuation"），再放去连字符变体（"officeleasing"），再放 `_short_name(line.name)` 的所有变体，再放 2–3 个 description 关键词。
   - 注册表加载失败时 fallback 到 seed-only map（保持向后兼容）。
3. **`_LINE_KEYWORDS: dict[str, list[str]] = build_line_keywords_from_registry()`** ——模块导入时构建一次。
4. **修了一个 tie-breaker bug**（顺带）：原 `parse_question` 用 `max(line_hits, key=line_hits.get)`，当多个 line 同时 hit（比如"投资部 IRR 最高的项目"：residential 的"项目" + investment 的"投资" 各 1 次）会按 dict 插入序挑第一——错的。新逻辑是 `sorted by (-最长匹配, -hit 数, line id asc)`，**最长匹配胜**——确保"投资"（长度 2）击败"项目"（长度 2 但都 2，所以平手……）。Wait — 实际"投资"和"项目"都是 2 字符，平手后按 `line id` 升序：`investment` < `residential` 字典序，所以 investment 胜。验证用例 #7（"投资部 IRR 最高"）→ `line=investment` ✅。

### 验证

| # | curl / 调用 | 期望 line | 期望 intent | 实测 line | 实测 intent | 结果 |
|---|---|---|---|---|---|---|
| 4 | `POST /api/copilot/ask {"question":"估价部 IRR 最高的是哪个报告？"}` | `valuation` | `irr_top` | `valuation` ✅ | `irr_top` ✅ | **PASS** |
| 5 | `POST /api/copilot/ask {"question":"写字楼租赁成交面积最大的是哪个？"}` | `office-leasing` | (任意, 只要不是错配) | `office-leasing` ✅ | `fallback_unknown`（预期，因"成交面积"不是已注册的 13 个 intent 之一） | **PASS** |
| 6 | `POST /api/copilot/ask {"question":"工业地产出租率最高的项目"}` | `industrial` | (任意) | `industrial` ✅ | `fallback_unknown`（同上，"出租率"未在硬编码 intent 列表） | **PASS** |
| 7 | `POST /api/copilot/ask {"question":"投资部 IRR 最高的项目"}` | `investment` | `irr_top` | `investment` ✅ | `irr_top` ✅ | **PASS**（验证 tie-breaker fix） |
| 8 | `POST /api/copilot/ask {"question":"住宅三道红线触发情况"}`（回归） | `residential` | `redlines` | `residential` ✅ | `redlines` ✅ | **PASS**（原 4 条业务线未破坏） |

`registry.yaml` 现注册 10 条业务线，`_LINE_KEYWORDS` 长度也是 10（每条 9–14 个 keyword），覆盖率 100%。

---

## 修改的文件清单

| 文件 | 改动摘要 |
|---|---|
| `apps/api/app/db/session.py` | 加 `_build_connect_args()`，传入 `create_async_engine`；`init_db()` 用 `asyncio.wait_for` 包 `ensure_raw_schema()`，TimeoutError 走单独 warning 分支。 |
| `apps/api/app/db/bootstrap.py` | 新增 `DB_BOOTSTRAP_TIMEOUT_S = 2.0` 常量；为 `ensure_raw_schema()` 添加文档说明依赖调用方 wait_for。 |
| `apps/api/app/main.py` | `lifespan` 在 `await init_db()` 外面再包一层 try/except（最终防线）。 |
| `apps/api/app/services/llm/mock.py` | (a) 用 `build_line_keywords_from_registry()` 动态构建 `_LINE_KEYWORDS`；(b) 加 `_LINE_ALIAS_SEEDS`（含 6 条新业务线的中文/英文别名）、`_extract_description_keywords()`、`_short_name()` 三个辅助函数；(c) `parse_question()` 的 line tie-breaker 改用"最长匹配 + 命中数 + id 字典序" 排序。 |
| `docs/fixes-2026-09-03-deliverable.md` | 本文件。 |
| `docs/fixes-2026-09-03-validation-evidence.txt` | 9 项 HTTP 验证的原始 stdout。 |
| `apps/api/api_start3.log` | 启动日志（10 条业务线 + init_db timeout warning）。 |
| `apps/api/api_final.log` + `.err` | 最终验证启动日志（uvicorn ready @ 3.35s）。 |
| `apps/api/smoke_keywords.py` | 离线 keyword 生成 + parse_question 烟囱测试（12 个 query）。 |
| `apps/api/final_validation.py` | 在线 HTTP 验证脚本（9 项 curl 等价）。 |
| `apps/api/validation.py` | 早期版（不直接使用，保留作 debug）。 |
| `apps/api/run_curl_validation.ps1` | 早期 PowerShell 版（被 UTF-8 编码问题坑过，仅留作"PowerShell + 中文 POST body"参考）。 |
| `apps/api/pytest_out.txt` | pytest 进度——跑到 53% / 113 tests passed 时挂死（环境级 hang，非本次改动引入）。 |

---

## 验证输出（6+ 条 curl）

完整 evidence 见 `docs/fixes-2026-09-03-validation-evidence.txt`。摘要：

### 1) API 5 秒内 ready（PG 未起）

```
2026-09-03T10:17:26 INFO  Mounted business line 'industrial' ...
2026-09-03T10:17:26 INFO  Discovered 3 scraper(s): lianjia_deals, nbs_house_price, policy_crawler
2026-09-03T10:17:28 WARNING init_db: ensure_raw_schema timed out after 2.0s (DB unreachable, continuing without DB)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8769
```
**实测启动耗时 3.35s**（目标 < 5s）。 ✅

### 2) `GET /api/registry/lines` 返回 10 条
```
HTTP 200 | version=0.1.b4923bec | count=10
  - residential: 住宅分析
  - retail: 零售分析
  - retail-leasing: 零售租赁与市场报告
  - my-line: 我的测试业务线
  - valuation: 估价部
  - advisory: 地产顾问部
  - office-leasing: 写字楼租赁部
  - investment: 地产投资部
  - project-management: 地产项目管理部
  - industrial: 工业地产部
```

### 3) `GET /api/scrapers` 返回 3 条
```
HTTP 200 | count=3
  - lianjia_deals: 链家城市成交数据 (公开页面)
  - nbs_house_price: 国家统计局 70 城房价指数
  - policy_crawler: 房地产政策抓取 (住建部 + 各地住建委)
```

### 4) 估价部 → valuation
```
POST /api/copilot/ask {"question":"估价部 IRR 最高的是哪个报告？"}
HTTP 200 | intent=irr_top | line=valuation | conf=0.85 | backend=mock
```

### 5) 写字楼租赁 → office-leasing
```
POST /api/copilot/ask {"question":"写字楼租赁成交面积最大的是哪个？"}
HTTP 200 | intent=fallback_unknown | line=office-leasing | conf=0.3 | backend=mock
```

### 6) 工业地产 → industrial
```
POST /api/copilot/ask {"question":"工业地产出租率最高的项目"}
HTTP 200 | intent=fallback_unknown | line=industrial | conf=0.3 | backend=mock
```

### 7) 投资部 → investment（tie-breaker 修复证据）
```
POST /api/copilot/ask {"question":"投资部 IRR 最高的项目"}
HTTP 200 | intent=irr_top | line=investment | conf=0.85 | backend=mock
```

### 8) 回归：原 4 条业务线仍 OK
```
POST /api/copilot/ask {"question":"住宅三道红线触发情况"}
HTTP 200 | intent=redlines | line=residential | conf=0.8
```

### 9) 健康检查
```
GET /api/copilot/health  -> HTTP 200 | backend=mock | available_lines=10
GET /healthz             -> HTTP 200 | {"status":"ok"}
```

---

## 假设 (Assumptions)

1. **DB_BOOTSTRAP_TIMEOUT_S = 2.0s 是合理上限**——满足"5s 内 uvicorn ready"的硬目标（实测 3.35s，含 2s wait_for + ~1s 业务线挂载 + scraper 发现）。如果将来需要更紧的上限（如 1s），可以把 `connect_args` 的 `timeout` 也降到 1。
2. **关键词生成用 6 条手工 seeds 是可接受的**——这些 aliases 在 manifest 的 `name` 字段里没有，但都是 BP 日常用语，长期看建议把它们写进每个 line 的 `manifest.yaml` 新增字段 `keywords: [...]`，本次先在 mock 层覆盖。
3. **tier-breaker 用"最长匹配 + 命中数 + id 字典序"**——`line id` 字典序作最终 tiebreak 是 deterministic 但语义中性。后续如果要给某条 line 加权重，扩展点就是这里。
4. **未触动 `mock_helpers.py` 的 intent 派发表**（`_DISPATCH` dict）——新增业务线（office-leasing、industrial、investment、advisory、project-management、valuation）目前**没有专属的 intent handler**；line 解析正确，但落到 `intent_fallback`（即 `fallback_unknown`）由用户重述问题。这是 Pre-existing 行为，不在本次 fix 范围（fix 2 解决的是 line 识别，不是 intent 覆盖）。

## 未解决 (Blockers / Leftovers)

1. **`mock_helpers.py` 缺新 6 条业务线的 intent handler**。举例：问"投资部 IRR 最高的项目" → line 正确=investment，intent 正确=irr_top，但 `intent_residential_irr_top` 内 `if line != "residential": line = "residential"`，实际仍取 residential 数据。前端调试时能看到 line=investment + intent=irr_top，但 answer 文本会写"在住宅线下"，**是误导**。修法：在 `_DISPATCH` 里加 `intent_investment_irr_top` / `intent_office_leasing_top` 等 handler（沿用 `intent_residential_irr_top` 的模板，调对应的 line API），并把硬编码 `if line != X: line = X` 删掉。建议单独立项。
2. **`pytest` 跑 `apps/api/tests/` 排除 `test_copilot.py` 后仍卡在 53%（113 tests passed）**——属于本机/环境级 hang（`tests/test_copilot.py` 早就被排除；卡的是其他 test file）。task 指令明确说"看是否还卡（之前在本环境卡；如果卡就 fallback 到不跑）"，已 fall back 跳过完整 suite。113 tests passed before hang 是 _pre-existing_ 环境问题，与本次改动无关。
3. **`_LINE_ALIAS_SEEDS` 是硬编码 dict，未来加新 line 还得在 seeds 里加**——长期应迁到 manifest YAML 的 `keywords: [...]` 字段，让 registry loader 自动读，但当前 mock 引擎读 manifest 的范围有限（只读 `id / name / description`），改动面较大。
4. **未在 Postgres 真起的状态下回归测** init_db 成功路径——本次环境没 PG，按任务"PG 没起也行"执行；本地有 PG 时建议再跑一次完整 lifespan。
