# Fin BP Portal — 架构概览

> 生成于 2026-09-03，依据架构审计报告（`docs/architecture-audit-2026-09-03.md`）。
> **10/11 架构承诺 PASS · 通用性已验证 · 0 P0/P1 问题**。

本文档是审计报告的可视化伴侣。五张图，每张都标注了实现该承诺的代码位置。

---

## 图 1 — 系统分层架构（整体分层）

整个系统，从浏览器到数据库，标注代码所在、数据流动、密钥接入的边界。

```mermaid
graph TB
    subgraph Browser["Browser (user)"]
        U[User]
    end

    subgraph FE["Frontend - Next.js 14 prod server :3000"]
        WEB_DASH["(dashboard)/<br/>dashboard / sensitivity / copilot<br/>forecast / alerts / scrapers / [line] / [line]/[page]"]
        WEB_BFF["app/api/* (BFF proxies)<br/>forward to NEXT_PUBLIC_API_BASE_URL"]
    end

    subgraph BE["Backend - FastAPI on uvicorn :8000"]
        API_MAIN["app/main.py - lifespan mounts routers"]
        API_REG["routers/registry.py<br/>importlib dynamic discovery"]
        API_LINES["routers/lines/ - one per business line<br/>mounted at /api/lines/&lt;line&gt;"]
        API_ENG["routers/<br/>sensitivity · copilot · forecast · alerts · scrapers"]
        SVC_ENG["services/<br/>sensitivity_engine · copilot_engine<br/>forecast_engine · alert_engine · scrapers/registry"]
    end

    subgraph ENGINES["Universal engines - read business_lines/&lt;line&gt;/*.yaml"]
        E_SENS["Sensitivity<br/>2D heatmap + tornado + scenarios"]
        E_COP["Copilot<br/>14 mock intents + LLM pluggable"]
        E_FCST["Forecast<br/>4 methods + MAPE + attribution"]
        E_ALT["Alerts<br/>6 operators + ack + history"]
        E_SCR["Scrapers<br/>NBS real + Lianjia + policy"]
    end

    subgraph PLUG["Plugin layer - business_lines/ (the only place business-line code lives)"]
        REG["registry.yaml<br/>(10 lines)"]
        TPL["_template/<br/>(5-step scaffold)"]
        LINES["residential · retail · retail-leasing<br/>valuation · advisory · office-leasing<br/>investment · project-management · industrial<br/>+ my-line (demo)"]
    end

    subgraph DATA["Data layer"]
        PG[("PostgreSQL 16 :5432<br/>raw.uploads + DBT marts")]
        CH[("ClickHouse 24 :8123 :9100<br/>analytics (optional)")]
        RD[("Redis 7 :6379<br/>cache / queue")]
        MN[("MinIO :9000 :9001<br/>S3 file storage")]
        AIR[("Airflow 2.8 :8080<br/>ingest_daily + scrape_weekly")]
    end

    subgraph EXTERNAL["External"]
        NBS["stats.gov.cn<br/>70-city house price"]
        LJ["Lianjia public<br/>deals data"]
        DS["DeepSeek V3<br/>api.deepseek.com/v1"]
        OL["Ollama local<br/>:11434 (optional)"]
    end

    U -->|"HTTPS"| WEB_DASH
    WEB_DASH -->|"/api/* (same-origin)"| WEB_BFF
    WEB_BFF -->|"fetch(NEXT_PUBLIC_API_BASE_URL)"| API_REG
    WEB_BFF -->|"fetch"| API_ENG

    API_MAIN --> API_REG
    API_REG -->|"importlib.util"| LINES
    API_REG -->|"loads registry.yaml"| REG
    API_REG -->|"scaffolds from"| TPL
    API_MAIN --> API_ENG

    API_LINES -.->|HTTP /api/lines/&lt;line&gt;/...| SVC_ENG
    API_ENG --> SVC_ENG

    SVC_ENG --> E_SENS
    SVC_ENG --> E_COP
    SVC_ENG --> E_FCST
    SVC_ENG --> E_ALT
    SVC_ENG --> E_SCR

    E_SENS -.->|reads sensitivity.yaml| LINES
    E_FCST -.->|reads forecast.yaml| LINES
    E_ALT -.->|reads alerts.yaml| LINES
    E_COP -.->|reads line aliases| REG

    E_SCR -->|"http_get"| NBS
    E_SCR -->|"http_get"| LJ
    E_SCR -->|fallback mock| LINES

    E_COP -->|"if DEEPSEEK_API_KEY"| DS
    E_COP -->|"elif OLLAMA_BASE_URL"| OL
    E_COP -->|"else"| MOCK["MockBackend<br/>(rule engine)"]
    DS -.->|"on 401/network error"| MOCK
    OL -.->|"on error"| MOCK

    AIR -->|"reads /data/landing/*"| PG
    AIR -->|"reads business_lines/*"| LINES
    AIR -->|"dbt run"| PG
    E_SCR -->|"persists to raw.uploads"| PG

    API_REG -->|SQLAlchemy| PG
    SVC_ENG -->|"reads marts"| PG
    SVC_ENG -->|"reads marts"| CH

    MN <-.->|"file uploads"| AIR
    RD <-.->|"cache"| API_MAIN

    classDef boundary stroke:#333,stroke-width:2px,fill:#f5f5f5
    class FE,BE,ENGINES,PLUG,DATA,EXTERNAL boundary
    classDef secret stroke:#c00,stroke-width:2px,fill:#fee
    class DEEPSEEK_API_KEY secret
```

**关键边界不变量**（已由审计验证）：

1. **`apps/api/app/routers/registry.py` 之外没有任何 `import business_lines.X`**
2. **`apps/web/app/` 中没有 `from @fin-bp/...` 形式的业务线组件** —— 只能通过 `[line]/[page]` 动态路由
3. **引擎在运行时从 `business_lines/<line>/` 读取 YAML**，不进行 Python import
4. **密钥仅通过环境变量进入** —— 仓库中没有 `.env`，`.env.example` 是模板

---

## 图 2 — 插件机制（业务线插件机制）

`business_lines/<line>/` 如何在不动核心代码的前提下成为活的 API + UI。
三件事共同发挥作用：注册表文件、importlib 加载器、模板。

```mermaid
sequenceDiagram
    autonumber
    participant Dev as Developer
    participant BL as business_lines/test-line/
    participant Reg as business_lines/registry.yaml
    participant Code as apps/api/app/routers/registry.py
    participant FastAPI as uvicorn (startup)
    participant Browser

    Note over Dev,BL: 1. 复制模板（5 步新增业务线工作流）
    Dev->>BL: cp -r business_lines/_template business_lines/test-line<br/>edit manifest.yaml + indicators.yaml<br/>+ sensitivity/forecast/alerts.yaml<br/>+ api/router.py (one FastAPI router)

    Note over Dev,Reg: 2. 注册（一行）
    Dev->>Reg: Append: - id: test-line<br/>&nbsp;&nbsp;manifest: business_lines/test-line/manifest.yaml

    Note over FastAPI: 3. 重启 API（核心代码 0 改动）
    FastAPI->>Code: lifespan → load_business_lines()
    Code->>Reg: yaml.safe_load
    Reg-->>Code: lines: [{id: test-line, ...}]
    loop for each line
        Code->>BL: importlib.util.spec_from_file_location(<br/>&nbsp;&nbsp;f"business_lines.{id}.router",<br/>&nbsp;&nbsp;f"business_lines/{id}/api/router.py")
        Code->>BL: module_from_spec + exec_module
        Code->>FastAPI: app.include_router(router, prefix=api_prefix)
    end
    FastAPI-->>FastAPI: Routers mounted at /api/lines/&lt;id&gt;/*

    Note over Browser: 4. 使用
    Browser->>FastAPI: GET /api/registry/lines
    FastAPI-->>Browser: 11 lines (incl. test-line)
    Browser->>FastAPI: GET /api/lines/test-line/ping
    FastAPI-->>Browser: {"status":"ok",...}
```

**已通过审计验证**（通用性测试通过）：

- 新增 `business_lines/test-line/` + 在 `registry.yaml` 添加 1 行 → 0 核心代码改动
- 4 个引擎全部自动发现 test-line：
  - `/api/registry/lines` 数量从 10 → 11
  - `/api/sensitivity/profiles` 数量从 9 → 10
  - `/api/forecast/profiles` 数量从 9 → 10
  - `/api/alerts/profiles` 数量从 9 → 10
- 移除 → API 回到 10 条业务线，无遗留

---

## 图 3 — 通用引擎（4 引擎通用性）

同一份引擎代码服务 10 条业务线。唯一的差异是 YAML。

```mermaid
graph LR
    subgraph SHARED["apps/api/app/services/ (zero business-line knowledge)"]
        SENS["sensitivity_engine.py<br/>~460 LOC"]
        COP["copilot_engine.py<br/>+ llm/{base,mock,deepseek,ollama}"]
        FCST["forecast_engine.py<br/>~600 LOC"]
        ALT["alert_engine.py<br/>~700 LOC"]
    end

    subgraph CFG["business_lines/&lt;line&gt;/*.yaml (per-line config)"]
        SENS_Y["sensitivity.yaml<br/>4 inputs × N outputs<br/>+ coefficients"]
        FCST_Y["forecast.yaml<br/>3-4 series + methods"]
        ALT_Y["alerts.yaml<br/>3-5 rules + thresholds"]
    end

    subgraph ENDPOINTS["routers/"]
        R_SENS["sensitivity.py<br/>profiles · analyze · scenarios"]
        R_COP["copilot.py<br/>ask · suggestions · health"]
        R_FCST["forecast.py<br/>profiles · run · compare"]
        R_ALT["alerts.py<br/>rules · check · history · ack"]
    end

    SENS <-->|"load_profile()"| SENS_Y
    FCST <-->|"load_profile()"| FCST_Y
    ALT <-->|"load_rules()"| ALT_Y

    R_SENS --> SENS
    R_COP --> COP
    R_FCST --> FCST
    R_ALT --> ALT

    COP -->|"optional"| LLM[("DeepSeek · Ollama<br/>via env var factory")]

    style SHARED fill:#e8f5e9
    style CFG fill:#fff3e0
    style ENDPOINTS fill:#e3f2fd
```

**契约经审计验证**（`grep -r "residential\|retail" apps/api/app/services/`）：

| 引擎 | 读取自 | 写入至 | 是否硬编码业务线名称？ |
|---|---|---|---|
| Sensitivity | `business_lines/<line>/sensitivity.yaml` | 仅响应 | ❌ 否 |
| Copilot | `load_registry()` + `_LINE_ALIAS_SEEDS` | 响应 + debug.parsed | ⚠️ 仅 alias 字典（P2） |
| Forecast | `business_lines/<line>/forecast.yaml` | 仅响应 | ❌ 否 |
| Alerts | `business_lines/<line>/alerts.yaml` | 内存存储 | ❌ 否 |

审计中标记的 **3 处 P2 问题** 都与 UX 完整性相关
（LLM 提示的目录、建议问题、UI 页面映射），而非核心代码知识。不破坏通用性。

---

## 图 4 — AI Copilot 降级链

LLM 后端如何被选中，以及失败时如何兜底。

```mermaid
graph TB
    REQ["POST /api/copilot/ask<br/>{question, line_id?}"]

    REQ --> ENGINE["copilot_engine.ask()"]

    ENGINE --> PARSE["parse_question()<br/>extract line + intent<br/>(dynamic from _LINE_ALIAS_SEEDS)"]

    PARSE --> DECIDE{"Which backend?"}

    DECIDE -->|"DEEPSEEK_API_KEY set"| DSE["DeepSeekBackend<br/>POST api.deepseek.com/v1<br/>deepseek-chat"]
    DECIDE -->|"OLLAMA_BASE_URL set"| OLL["OllamaBackend<br/>POST :11434/api/chat"]
    DECIDE -->|"else"| MOCK["MockBackend<br/>14 intent rules<br/>(always available)"]

    DSE -->|"HTTP call"| DSE_OK{"2xx?"}
    DSE_OK -->|"yes"| DSE_RESP["return LLM answer"]
    DSE_OK -->|"no (401/network)"| FB["FallbackBackend<br/>(auto)"]

    OLL -->|"HTTP call"| OLL_OK{"2xx?"}
    OLL_OK -->|"yes"| OLL_RESP["return LLM answer"]
    OLL_OK -->|"no"| FB

    FB --> MOCK
    MOCK --> RULE["Dispatch to intent handler<br/>(irr_top / redlines / line_indicators ...)<br/>calls /api/lines/&lt;line&gt;/* (HTTP)"]
    RULE --> CITE["Build citations[]<br/>+ chart_data"]
    CITE --> MOCK_RESP["return MockAnswer<br/>with used_fallback=true"]

    DSE_RESP --> WRAP["Wrap in CopilotResponse<br/>(line_id, intent, answer, citations,<br/>backend, used_fallback, model)"]
    MOCK_RESP --> WRAP
    OLL_RESP --> WRAP
    WRAP --> OUT["HTTP 200<br/>(never 500)"]

    style DECIDE fill:#fff9c4
    style MOCK fill:#c8e6c9
    style FB fill:#ffccbc
    classDef warn stroke:#c00
    class DSE_OK,OLL_OK warn
```

**契约**：Copilot 端点**绝不会因 LLM 失败而返回 HTTP 500**。要么真实 LLM 回答，
要么规则引擎以 `used_fallback=true` 加 `fallback_reason` 解释哪里出错来回答。

已线上验证：
- `DEEPSEEK_API_KEY=fake-key` → `used_fallback=true`，`fallback_reason="DeepSeekHTTPError: HTTP 401: ..."`，HTTP 200。
- 无 key → 从一开始就是 mock，`used_fallback=false`，`backend=mock`。

---

## 图 5 — 数据流

数据从哪里来，落在哪里，如何到达用户。

```mermaid
graph LR
    subgraph SOURCES["Sources"]
        S1["Excel / CSV / 银行流水<br/>(T4 parsers)"]
        S2["NBS / Lianjia / Policy<br/>(scraper framework)"]
        S3["Business seed JSON<br/>(business_lines/&lt;line&gt;/data/seed/)"]
    end

    subgraph LANDING["Landing zone"]
        LZ["data/landing/*.csv<br/>(host) ↔ /data/landing/<br/>(container)"]
        RU[("raw.uploads table<br/>(source, payload JSONB,<br/>fetched_at)")]
    end

    subgraph TRANSFORM["Transform (DBT)"]
        STG["stg_* models<br/>(staging)"]
        INT["int_* models<br/>(intermediate)"]
        MART["mart_* models<br/>(facts / dims)"]
    end

    subgraph SERVE["Serve"]
        SVC["/api/lines/&lt;line&gt;/*<br/>(business line endpoints)"]
        ENG["4 engines<br/>(sensitivity/copilot/<br/>forecast/alerts)"]
        SVC --> ENG
    end

    subgraph CONSUME["Consume"]
        W["Next.js BFF proxies<br/>(/api/*)"]
        U[Browser]
    end

    S1 -->|POST /api/upload| LZ
    S2 -->|HTTP fetch| RU
    S3 -->|loaded by FastAPI| SVC

    LZ -->|Airflow ingest_daily| RU
    RU -->|dbt run| STG
    STG --> INT --> MART

    MART -->|read by| SVC
    MART -->|read by| ENG

    SVC --> W --> U

    classDef source fill:#fff3e0
    classDef landing fill:#e1f5fe
    classDef transform fill:#f3e5f5
    classDef serve fill:#e8f5e9
    class S1,S2,S3 source
    class LZ,RU landing
    class STG,INT,MART transform
    class SVC,ENG serve
```

**延迟预算**：
- Excel 上传 → 落地区：< 1s
- Airflow DAG → raw.uploads：按计划（或手动触发）
- DBT 运行：1-5 分钟
- 从 marts 读取的 API 响应：< 100ms（Postgres 索引）

---

## 图 6 — Docker Compose 部署

7 个服务在生产中如何连接。

```mermaid
graph TB
    subgraph finbp-net["network: finbp-net"]
        WEB["web<br/>finbp/web:0.1.0<br/>:3000<br/>Next.js prod"]
        API["api<br/>finbp/api:0.1.0<br/>:8000<br/>FastAPI/uvicorn"]
        PG[("postgres:16<br/>:5432")]
        RD[("redis:7<br/>:6379")]
        CH[("clickhouse:24<br/>:8123 :9100")]
        MN[("minio<br/>:9000 :9001")]
        AF["airflow:2.8<br/>:8080<br/>webserver + scheduler"]
    end

    USER[Browser] -->|":3000"| WEB
    WEB -->|"BFF → :8000"| API
    API -->|asyncpg| PG
    API -->|optional| CH
    API -->|optional| RD
    API -->|file upload| MN
    AF -->|psycopg2| PG
    AF -->|"dbt run"| PG
    AF -.->|reads| API
    AF -.->|"read /data/landing/*"| MN

    USER -.->|":8080 admin"| AF
    USER -.->|":9001 finbp"| MN
    USER -.->|":8123"| CH

    classDef web fill:#e3f2fd
    classDef api fill:#e8f5e9
    classDef db fill:#fff3e0
    classDef task fill:#f3e5f5
    class WEB web
    class API api
    class PG,RD,CH,MN db
    class AF task
```

**启动顺序由 `depends_on: condition: service_healthy` 强制**：
`postgres` → `api` → `web`，`redis/minio/clickhouse/airflow` 为兄弟节点。

---

## 审计总结表

| # | 承诺 | 实现 | 证据 |
|---|---|---|---|
| A1 | 核心代码无业务线硬编码 | ✅ | `grep` 除 alias dict 外 0 命中 |
| A2 | 业务线自动发现 | ✅ | importlib 真实工作 |
| A3 | 加业务线零代码改动 | ✅ | universality test: add/remove test-line |
| B1 | engine 读业务线 YAML | ✅ | sensitivity_engine / forecast_engine / alert_engine |
| B2 | LLM 抽象层 | ✅ | base.py + 3 backend + Fallback |
| B3 | scraper 框架 | ✅ | base.py + 3 scrapers + RLock |
| C1 | 前端动态路由 | ✅ | `[line]/page.tsx` + `[line]/[page]/page.tsx` |
| C2 | 5 个引擎页 | ✅ | /sensitivity /copilot /forecast /alerts /scrapers |
| C3 | Topbar 完整 | ✅ | 含业务线 + 5 引擎 + scrapers |
| D | 业务线 8 文件齐 | ✅ | 10 业务线 × 8-10 文件（含 _sources.yml）|
| E | 配置一致 | ✅ | registry.yaml + .env.example + docker-compose |
| F | env 变量覆盖所有可配置 | ⚠️ P2 | 3 处 catalog 硬编码（不影响功能）|

**通用性得分**：10/11 PASS，1 PASS-with-P2-notes。
**P0/P1 问题数**：0。
**是否可交付**：✅
