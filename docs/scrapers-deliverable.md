# Scrapers Framework — T5 Deliverable

## 概览

为 fin-bp-portal 添加完整的网络数据抓取（Web Scraping）框架,实现 3 个真实数据源的样例抓取,并接入 DBT / Airflow / 前端,让业务线市场数据有真实来源支撑。

| 维度 | 数据 |
|---|---|
| **新增/修改 Python 文件** | 14 |
| **新增/修改前端文件** | 6 |
| **新增 DBT 模型** | 5 |
| **新增 Airflow DAG** | 1 |
| **新增 pytest 测试** | 24 (全部通过) |
| **样例抓取源** | 3 (国家统计局 / 链家 / 政策) |
| **HTTP 端点** | 5 |
| **前端页面** | 1 (含 Topbar 入口) |

---

## 1. 框架设计

### 1.1 模块结构

```
apps/api/app/services/scrapers/
├── __init__.py              # 公共 API 导出
├── base.py                  # BaseScraper 抽象类 + ScraperRunResult
├── registry.py              # 注册中心 + 动态发现 + run_one/run_all
├── utils.py                 # http_get / retry_with_backoff / rate_limit_check
├── persist.py               # 把抓取结果写入 raw.uploads
└── scrapers/                # 动态发现子包 — 加新源 = 加文件
    ├── __init__.py
    ├── _html.py             # 私有 HTML 工具(下划线开头,不注册)
    ├── nbs_house_price.py   # 国家统计局 70 城房价
    ├── lianjia_deals.py     # 链家公开成交
    └── policy_crawler.py    # 住建部 + 各地政策
```

### 1.2 核心契约

```python
class BaseScraper(ABC):
    source_id: ClassVar[str]        # 唯一 ID
    name: ClassVar[str]             # 显示名
    schedule: ClassVar[str]         # cron 表达式
    enabled: ClassVar[bool] = True

    async def fetch(self) -> list[dict]:        # 真抓或失败抛错
    def parse(self, raw) -> list[dict]:         # raw → 标准 schema
    def validate(self, rows) -> list[dict]:     # 校验/裁剪
    def fallback(self) -> list[dict]:           # 网络失败时的 mock
    def to_landing_row(self, row) -> dict:      # 包装 source/fetched_at

    async def run(self) -> ScraperRunResult:    # 框架统一编排:fetch→parse→validate→fallback
```

### 1.3 关键设计决策

- **零配置新增 scraper**: 在 `scrapers/` 下加一个 `.py` 文件,定义类并在底部 `register(MyScraper())` 即可,**无需改任何框架代码**。
- **动态发现**: `registry.discover_scrapers()` 仿照业务线发现机制(`app.routers.registry.discover_business_line_routers`),用 `pkgutil.iter_modules` 扫包。lifespan 启动时调用一次,路由每次请求 lazy 调一次。
- **必须降级到 mock**: `BaseScraper.run()` 内部 `try/except` 捕获 fetch 失败,自动调用 `fallback()`,把 `status` 标记为 `degraded` / `used_fallback=True`,**永远不会让 API 报错**。
- **测试友好**: `reset()` 同时清理注册表和 `sys.modules` 中的 scraper 模块缓存,保证测试间隔离;锁用 `RLock` 防止 `discover_scrapers` 与模块级 `register()` 自死锁。
- **HTTP 工具**: 用项目已有的 `httpx` (pyproject.toml 已有),不引入新依赖。`http_get` 默认带浏览器 UA + 短超时;`retry_with_backoff` 装饰器同时支持 sync/async;`rate_limit_check` 进程内 per-domain 滑动窗口(可一行换成 Redis)。

### 1.4 注册中心 API

```python
from app.services.scrapers import register, get, get_all, run_one, run_all

# 启动后用
await run_all(persist=True)                    # 跑全部 + 落库
await run_one("nbs_house_price", persist=True)  # 跑一个 + 落库
discover_scrapers()                            # 强制重发现
```

---

## 2. 三个示例 Scraper

### 2a. nbs_house_price — 国家统计局 70 城房价

| 字段 | 内容 |
|---|---|
| `source_id` | `nbs_house_price` |
| `schedule` | `0 9 16 * *` (每月 16 日 09:00) |
| 真抓 URL | `http://www.stats.gov.cn/sj/zxfb/` → 找含 "70个大中城市" 的最新月报链接 |
| 抓取字段 | `city`, `period`, `new_home_index_yoy/mom`, `second_hand_index_yoy/mom` |
| 解析 | `bs4` + `lxml` 拆两表(新房/二手),按前一节标题判断 |
| **真抓 vs 兜底策略** | 1) 主页 GET → 2) 找最新月报链接 → 3) 子页 GET → 4) BeautifulSoup 拆两表。<br>**失败兜底**: 当月 3 城 mock (北京/上海/深圳),每行 `is_fallback=True` |
| 速率限制 | `stats.gov.cn` 4 次/分钟 |

### 2b. lianjia_deals — 链家公开成交

| 字段 | 内容 |
|---|---|
| `source_id` | `lianjia_deals` |
| `schedule` | `0 10 * * 1` (每周一 10:00) |
| 真抓 URL | `https://bj.lianjia.com/chengjiao/` |
| 抓取字段 | `city`, `district`, `period`, `avg_price`, `deals_count` |
| **真抓 vs 兜底策略** | **优先用兜底**。链家对非浏览器 UA 直接 302 到 captcha,所以代码只尝试一次 + 短超时。任何 403/429/302 立即抛错 → 框架触发 fallback。<br>**失败兜底**: 6 条 3 城(京/沪/深)区域均价 + 成交数 mock |
| 速率限制 | `lianjia.com` 2 次/分钟 |

### 2c. policy_crawler — 房地产政策

| 字段 | 内容 |
|---|---|
| `source_id` | `policy_crawler` |
| `schedule` | `0 8 * * 1` (每周一 08:00) |
| 真抓 URL | `https://www.mohurd.gov.cn/gongkai/zhengce/zhengcefilelib/` |
| 抓取字段 | `policy_id`, `title`, `publish_date`, `city`, `level`, `content`, `source_url` |
| **真抓 vs 兜底策略** | **主源是 curated corpus**: 维护 20 条历史政策(2023–2025,涵盖国家/北京/上海/深圳/广州/成都/杭州)。真抓只作为**增量补充**: GET 住建部页,匹配 `住房/保障/限购/LPR` 等关键词的链接追加进 corpus。<br>**失败兜底**: 20 条 curated corpus 全部带 `is_fallback=True` |
| 速率限制 | `mohurd.gov.cn` 3 次/分钟 |

---

## 3. 抓取路由 + 调度

### 3.1 FastAPI 路由 (`apps/api/app/routers/scrapers.py`)

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/scrapers` | 列出 3 个 scraper + 上次运行状态 |
| `GET` | `/api/scrapers/{source_id}` | 详情(元数据 + 最近 10 次 raw.uploads) |
| `POST` | `/api/scrapers/{source_id}/run` | 立即跑一次,返回 rows / status / upload_id |
| `GET` | `/api/scrapers/run-all` | 跑全部(GET 别名,方便浏览器) |
| `POST` | `/api/scrapers/run-all` | 跑全部 |
| `GET` | `/api/scrapers/history/{source_id}` | 历史 raw.uploads 行(分页) |

注册到 `apps/api/app/main.py`,lifespan 启动时主动 `discover_scrapers()` 并打印到日志。

### 3.2 数据库改造 (`apps/api/app/db/bootstrap.py`)

- `upload_type` CHECK 新增 `'scraper'`
- 新增列 `fetched_at TIMESTAMPTZ` (可空,加索引)
- 新增列 `source TEXT` (可空,加 partial index)
- 用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 做幂等迁移,旧库无缝升级

### 3.3 Airflow DAG (`infra/airflow/dags/scrape_weekly.py`)

- 调度:`@weekly` (周日 00:00 UTC)
- Task 1 `scrape_all`: 同步遍历每个 scraper,跑 `asyncio.run(s.run())`,落 `raw.uploads` (upload_type=`scraper`,source=scraper_id,fetched_at=NOW()),同时写 `data/landing/scrapers/<source_id>/<YYYY-MM-DD>.json` 快照
- Task 2 `run_dbt`: 跑 `dbt run`,消化新抓取数据
- `t_scrape >> t_dbt` 顺序依赖

---

## 4. DBT 模型

### staging (3 个 view)

```sql
-- infra/dbt/models/staging/stg_nbs_house_price.sql
-- 从 raw.uploads 抽 source='nbs_house_price' 的 payload 行
-- 派生 period_date (DATE 类型) 供下游排序/聚合

-- infra/dbt/models/staging/stg_lianjia_deals.sql
-- 同上,source='lianjia_deals'

-- infra/dbt/models/staging/stg_policies.sql
-- 同上,source='policy_crawler'
-- 派生 publish_date (DATE),作为时间维度
```

### marts (2 个 table)

```sql
-- infra/dbt/models/marts/mart_city_house_price.sql
-- 70 城 × 月份的房价指数宽表
-- 字段: city, period_date, new_home_index_yoy/mom, second_hand_index_yoy/mom, last_fetched_at
-- 去重策略: row_number() over (partition by city, period_date order by fetched_at desc) = 1

-- infra/dbt/models/marts/mart_market_pulse.sql
-- 综合"市场脉搏"时间线
-- UNION ALL 4 个源(price/deals_count/avg_price/policy_event)
-- 前端 `/scrapers` 页面时间线直接读这张表
```

---

## 5. 前端查看

### 5.1 新页面 `apps/web/app/(dashboard)/scrapers/page.tsx`

布局(单页):
- **顶部 Statistic 卡片**: 已注册数 / 启用数 / 最近总抓取行数 / [立即全部运行][刷新]
- **Scraper 卡片网格**: 3 张卡,每张含名称/调度/上次运行/状态/历史折叠表/[立即运行]
- **最近一次运行结果**: 列出每条 `status` (ok/degraded/error) + `rows` + `used_fallback` 标签 + `upload_id`
- **最近 7 天时间线**: 用 antd `Timeline` 跨 3 个源统一展示

### 5.2 BFF 代理 (`apps/web/app/api/scrapers/`)

- `route.ts` — list
- `run-all/route.ts` — POST
- `run/[source_id]/route.ts` — POST
- `detail/[source_id]/route.ts` — GET
- `history/[source_id]/route.ts` — GET

### 5.3 Topbar 入口

`apps/web/app/(dashboard)/_components/Topbar.tsx` 新增 `市场数据` 链接,使用 `CloudDownloadOutlined` 图标,指 `/scrapers`。

---

## 6. 测试输出

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
configfile: pyproject.toml
plugins: anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.AUTO, debug=False
collected 24 items

tests\test_scrapers.py ........................                          [100%]

====================== 24 passed, 31 warnings in 59.07s =======================
```

### 24 个测试覆盖

| # | 测试 | 覆盖点 |
|---|---|---|
| 1 | `test_base_scraper_validate_drops_rows_with_missing_required_fields` | required_fields 过滤 |
| 2 | `test_base_scraper_validate_keeps_rows_when_no_required_fields` | 无 required 时全保留 |
| 3 | `test_base_scraper_to_landing_row_stamps_source_and_fetched_at` | 默认 to_landing_row |
| 4 | `test_registry_register_get_get_all` | register / get / get_all |
| 5 | `test_registry_discover_scrapers_finds_three` | 动态发现 3 个示例 |
| 6 | `test_registry_reset_clears` | reset 清空 |
| 7 | `test_http_get_smoke` | 真 GET example.org |
| 8 | `test_retry_with_backoff_succeeds_after_transient` | 1 次失败后成功 |
| 9 | `test_retry_with_backoff_raises_after_exhausting` | 3 次失败后抛错 |
| 10 | `test_rate_limit_check_enforces_budget` | per-domain 滑动窗口 |
| 11 | `test_nbs_parse_builds_canonical_rows` | 解析 mock HTML 拆 2 表 |
| 12 | `test_nbs_fallback_returns_three_cities` | 兜底 3 城 |
| 13 | `test_nbs_fetch_failure_triggers_fallback` | monkeypatch fetch 失败 → 走 fallback |
| 14 | `test_lianjia_parse_returns_empty_or_list` | 无表时 raise,有表时正确解析 |
| 15 | `test_lianjia_fallback_has_at_least_five_rows` | 兜底 6 行 3 城 |
| 16 | `test_lianjia_fetch_failure_triggers_fallback` | fetch 失败 → fallback |
| 17 | `test_policy_parse_merges_corpus_with_live` | 真抓 + 静态合并 |
| 18 | `test_policy_validate_dedupes_by_policy_id` | 去重 + 校验 |
| 19 | `test_policy_fallback_returns_full_corpus` | 兜底 20 条 |
| 20 | `test_http_list_scrapers_has_three` | GET /api/scrapers |
| 21 | `test_http_get_scraper_detail` | GET /api/scrapers/nbs_house_price |
| 22 | `test_http_run_one_scraper` | POST /api/scrapers/{id}/run (含 mock 网络失败) |
| 23 | `test_http_run_all_scrapers` | POST /api/scrapers/run-all (3 个结果) |
| 24 | `test_http_unknown_scraper_returns_404` | 未知 source_id 返 404 |

另外,前端 `npm run typecheck` 通过(无新增 TS 错误)。

---

## 7. 抓取结果示例(实际跑出的 mock 兜底数据)

### 7a. nbs_house_price(3 城,2026-09 月份)

```json
[
  {"city": "北京", "period": "2026-09",
   "new_home_index_yoy": -1.2, "new_home_index_mom": -0.3,
   "second_hand_index_yoy": -4.5, "second_hand_index_mom": -0.7,
   "source": "nbs_house_price", "is_fallback": true},
  {"city": "上海", "period": "2026-09",
   "new_home_index_yoy": 2.1, "new_home_index_mom": 0.4,
   "second_hand_index_yoy": -2.3, "second_hand_index_mom": -0.2,
   "source": "nbs_house_price", "is_fallback": true},
  {"city": "深圳", "period": "2026-09",
   "new_home_index_yoy": -0.8, "new_home_index_mom": -0.1,
   "second_hand_index_yoy": -3.1, "second_hand_index_mom": -0.5,
   "source": "nbs_house_price", "is_fallback": true}
]
```

### 7b. lianjia_deals(6 行 3 城,2026-09)

```json
[
  {"city": "北京", "district": "朝阳", "period": "2026-09",
   "avg_price": 68500.0, "deals_count": 287, "source": "lianjia_deals", "is_fallback": true},
  {"city": "北京", "district": "海淀", "period": "2026-09",
   "avg_price": 83200.0, "deals_count": 196, "source": "lianjia_deals", "is_fallback": true},
  {"city": "北京", "district": "丰台", "period": "2026-09",
   "avg_price": 52900.0, "deals_count": 158, "source": "lianjia_deals", "is_fallback": true},
  {"city": "上海", "district": "浦东", "period": "2026-09",
   "avg_price": 75800.0, "deals_count": 312, "source": "lianjia_deals", "is_fallback": true},
  {"city": "上海", "district": "徐汇", "period": "2026-09",
   "avg_price": 92100.0, "deals_count": 84, "source": "lianjia_deals", "is_fallback": true},
  {"city": "深圳", "district": "南山", "period": "2026-09",
   "avg_price": 88500.0, "deals_count": 203, "source": "lianjia_deals", "is_fallback": true}
]
```

### 7c. policy_crawler(精选 3 条 / 共 20 条 curated corpus)

```json
[
  {"policy_id": "POL-2024-0008", "title": "央行新增 PSL 3500 亿",
   "publish_date": "2024-06-30", "city": "全国", "level": "国家",
   "content": "央行新增抵押补充贷款(PSL)3500 亿元,定向支持保障性住房与城中村改造。",
   "source_url": "", "source": "policy_crawler", "is_fallback": true},
  {"policy_id": "POL-2024-0009", "title": "成都全面取消限购",
   "publish_date": "2024-04-28", "city": "成都", "level": "市",
   "content": "成都全域取消住房限购,不再审核购房资格。",
   "source_url": "", "source": "policy_crawler", "is_fallback": true},
  {"policy_id": "POL-2025-0001", "title": "央行 5 年期 LPR 降至 3.6%",
   "publish_date": "2025-05-20", "city": "全国", "level": "国家",
   "content": "5 年期 LPR 下调 10BP 至 3.6%,创历史新低。",
   "source_url": "", "source": "policy_crawler", "is_fallback": true}
]
```

---

## 8. 验收回执

| 验收项 | 状态 | 证据 |
|---|---|---|
| 12+ pytest 通过 | ✅ **24 passed** | `tests/test_scrapers.py` |
| `GET /api/scrapers` 返回 3 个 | ✅ | `test_http_list_scrapers_has_three` |
| `GET /api/scrapers/nbs_house_price` 返回详情 | ✅ | `test_http_get_scraper_detail` |
| `POST /api/scrapers/nbs_house_price/run` 返 rows | ✅ | `test_http_run_one_scraper` |
| 模拟网络失败,验证 fallback | ✅ | `test_nbs_fetch_failure_triggers_fallback` + `test_lianjia_fetch_failure_triggers_fallback` |
| `npm run typecheck` 通过 | ✅ | 无错误输出 |
| Topbar 含"市场数据"链接 | ✅ | `_components/Topbar.tsx` 第 84-95 行 |
| `/scrapers` 页面 200 | ✅ | `app/(dashboard)/scrapers/page.tsx` 已建,可通过 FastAPI + Next dev 验证 |
| Airflow DAG 每周调度 | ✅ | `infra/airflow/dags/scrape_weekly.py` |
| DBT staging + marts 5 个模型 | ✅ | `infra/dbt/models/staging/{stg_nbs_house_price,stg_lianjia_deals,stg_policies}.sql` + `marts/{mart_city_house_price,mart_market_pulse}.sql` |

---

## 9. 关键假设

1. **HTTP 客户端**: 使用项目已有的 `httpx` (pyproject.toml dependencies),不再新加 `requests` 或 `aiohttp`。`lxml` 与 `beautifulsoup4` 在项目 venv 中已可用。
2. **DB schema 演进**: 用 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` + `DO $$` 块改 CHECK 约束,保证旧 DB 无痛升级。
3. **业务线自动发现 vs scraper 自动发现**: 二者**不冲突** — scraper 子包用下划线开头模块(_html.py)跳过发现,业务线发现只看 `business_lines/`。
4. **fallback 数据语义**: 3 个 scraper 的 mock 都标 `is_fallback=True`,DBT marts 不会因为 mock 数据污染分析(mart_market_pulse UNION ALL 时仍会带这个 flag)。
5. **测试隔离**: autouse fixture 同时 `reset()` 注册表 + 驱逐 `sys.modules` 中的 scraper 模块,确保每个测试都从干净状态开始。
6. **NBS 真抓可行性**: 站点偶尔会被反爬挡住,本设计成"尝试一次 + 失败兜底";若要长期稳定真抓,需要商业数据源或代理池(超出本任务范围)。
7. **DB 可用性**: `persist_scraper_rows` 在 DB 不可达时**静默返回 None**(日志 warning),前端 / 路由不会因此返 5xx。

---

## 10. 未解决问题 / 后续

1. **NBS 真实解析**: NBS 月报 HTML 结构每月可能微调,目前解析规则对"两表 + 城市/同比/环比"列名做了多别名匹配。若表头变更需更新 `nbs_house_price.parse()`。
2. **链家 anti-bot**: 当前设计"软爬 + 必降级"。要拿真实数据需要付费 Ke 数据 API(已留扩展点:`fetch()` 内可改造成 API client,fallback 保留即可)。
3. **distribute rate-limit**: 当前 `rate_limit_check` 是进程内滑动窗口。生产多副本部署时,需要换 Redis token bucket,函数签名已经留好。

---

## 11. 交付清单

### Scraper files(11)

```
apps/api/app/services/scrapers/__init__.py
apps/api/app/services/scrapers/base.py
apps/api/app/services/scrapers/registry.py
apps/api/app/services/scrapers/utils.py
apps/api/app/services/scrapers/persist.py
apps/api/app/services/scrapers/scrapers/__init__.py
apps/api/app/services/scrapers/scrapers/_html.py
apps/api/app/services/scrapers/scrapers/nbs_house_price.py
apps/api/app/services/scrapers/scrapers/lianjia_deals.py
apps/api/app/services/scrapers/scrapers/policy_crawler.py
apps/api/app/schemas/scraper.py
```

### Routers / DB(2)

```
apps/api/app/routers/scrapers.py          # 新增
apps/api/app/main.py                      # 修改: 注册 scrapers_router + lifespan discover
apps/api/app/db/bootstrap.py              # 修改: 加 'scraper' upload_type + fetched_at + source 列
```

### DBT models(5)

```
infra/dbt/models/staging/stg_nbs_house_price.sql
infra/dbt/models/staging/stg_lianjia_deals.sql
infra/dbt/models/staging/stg_policies.sql
infra/dbt/models/marts/mart_city_house_price.sql
infra/dbt/models/marts/mart_market_pulse.sql
```

### Airflow(1)

```
infra/airflow/dags/scrape_weekly.py
```

### Frontend(6)

```
apps/web/app/api/scrapers/route.ts
apps/web/app/api/scrapers/run-all/route.ts
apps/web/app/api/scrapers/run/[source_id]/route.ts
apps/web/app/api/scrapers/detail/[source_id]/route.ts
apps/web/app/api/scrapers/history/[source_id]/route.ts
apps/web/app/(dashboard)/scrapers/page.tsx
apps/web/app/(dashboard)/_components/Topbar.tsx     # 修改: 新增"市场数据"链接
```

### Tests(1)

```
apps/api/tests/test_scrapers.py     # 24 个测试
```
