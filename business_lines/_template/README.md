# business_lines/_template — 新业务线 5 步脚手架

> 把这个目录复制成 `business_lines/<your_line_id>/`，按下面 5 步修改即可。
> **承诺**：0 行核心代码改动。
> 完整指南参见 [`../../docs/maintenance/extending.md`](../../docs/maintenance/extending.md) §1 与
> [`../../docs/plugin-howto.md`](../../docs/plugin-howto.md)。

---

## 5 步流程

| 步骤 | 操作 |
|---|---|
| 1 | `cp -r business_lines\_template business_lines\<line_id>` |
| 2 | 编辑 `manifest.yaml`（id / name / nav / api_prefix / warehouse） |
| 3 | 编辑 `indicators.yaml`（8-10 个 KPI + 图表） |
| 4 | 把 `api\router.py.example` 重命名为 `api\router.py`，写 FastAPI router |
| 5 | 在 [`business_lines/registry.yaml`](../registry.yaml) 加 1 行 |

可选 6-9 步（推荐）：

| 步骤 | 文件 |
|---|---|
| 6 | `sensitivity.yaml` — 4 个输入 × N 个输出 + 系数 |
| 7 | `forecast.yaml` — 时间序列定义 |
| 8 | `alerts.yaml` — 规则 + 阈值 |
| 9 | `data\seed\*.json` — 初始 mock 数据 |

---

## 模板文件清单

```
_template/
├── README.md                   ← 你正在读
├── manifest.yaml.example       ← 复制为 manifest.yaml，编辑
├── indicators.yaml.example     ← 复制为 indicators.yaml，编辑
├── api/router.py.example       ← 复制为 api/router.py，编辑
├── dbt/dbt_project.yml.example ← 复制为 dbt/dbt_project.yml
├── dbt/models/example.sql      ← dbt 模型样例
├── web/pages/_example.tsx      ← Next.js 页面样例
└── data/seed/.gitkeep          ← seed JSON 占位
```

`.example` 后缀**故意**保留——避免被 `_template/` 自身的扫描识别为业务线。

---

## 字段契约（manifest.yaml）

```yaml
id: <line_id>                  # URL-safe slug，必须等于目录名
name: "显示名"                   # 人类可读
version: 0.1.0
description: "业务描述"
owner: "bp@example.com"
icon: "HomeOutlined"           # @ant-design/icons 名称
nav:
  - path: "/<line_id>"         # RELATIVE 到 (dashboard) 根
    title: "概览"
  - path: "/<line_id>/trends"
    title: "趋势"
api_prefix: "/api/lines/<line_id>"  # 必须以 / 开头
warehouse:
  schema: "raw_<line_id>"
  dbt_schema: "stg_<line_id>"
  mart_schema: "mart_<line_id>"
refresh:
  schedule: "0 2 * * *"        # cron
  enabled: true
features:
  universal_kpi: true
  universal_chart: true
  ag_grid: true
```

Pydantic 校验在 `apps/api/app/core/registry.py:54-72`。校验失败 → API 启动 fail-fast。

---

## 最小可工作示例

最快的"端到端可工作"业务线：

### 步骤 1：复制

```powershell
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
Copy-Item -Recurse business_lines\_template business_lines\my_new_line
```

### 步骤 2：重命名 + 编辑 manifest

把 `business_lines/my_new_line/manifest.yaml.example` 复制为 `manifest.yaml`，
把里面的 `change-me` 全部替换为 `my_new_line`：

```yaml
id: my_new_line
name: "我的新业务线"
version: 0.1.0
description: "测试用的业务线"
owner: "you@example.com"
icon: "AppstoreOutlined"
nav:
  - path: "/my_new_line"
    title: "概览"
api_prefix: "/api/lines/my_new_line"
warehouse:
  schema: "raw_my_new_line"
  dbt_schema: "stg_my_new_line"
  mart_schema: "mart_my_new_line"
refresh:
  schedule: "0 2 * * *"
  enabled: true
features:
  universal_kpi: true
  universal_chart: true
  ag_grid: true
```

### 步骤 3：编辑 indicators.yaml

```yaml
indicators:
  - id: daily_count
    title: "日活"
    unit: ""
    format: "number"
    aggregation: "sum"
    source: "mart_my_new_line.daily_count"
    description: "每日活跃量。"

charts:
  - id: daily_trend
    title: "日活趋势"
    type: "line"
    x: "date"
    y: ["daily_count"]
    source: "mart_my_new_line.daily_count"
    description: "过去 30 天。"
```

### 步骤 4：写 api/router.py

把 `api/router.py.example` 复制为 `api/router.py`，编辑：

```python
from fastapi import APIRouter
router = APIRouter()

@router.get("/ping")
async def ping() -> dict:
    return {"status": "ok", "line": "my_new_line"}

@router.get("/daily-count")
async def daily_count() -> dict:
    # 真实数据走 DBT marts；这里返 mock
    return {"line_id": "my_new_line", "items": [{"indicator_id": "daily_count", "value": 42}]}
```

### 步骤 5：注册

在 `business_lines/registry.yaml` 末尾加：

```yaml
- id: my_new_line
  manifest: business_lines/my_new_line/manifest.yaml
```

### 重启 API

```powershell
# 停止 + 重启 uvicorn
# 看到 "Mounted business line my_new_line" 即成功
```

### 验证

```powershell
# 1. 业务线数 +1
curl -b cookies.txt http://127.0.0.1:8769/api/registry/lines

# 2. 路由可访问
curl -b cookies.txt http://127.0.0.1:8769/api/lines/my_new_line/ping
# → {"status":"ok","line":"my_new_line"}

# 3. 浏览器打开 /my_new_line，应该看到通用 dashboard
```

---

## 通用性测试

加 / 删业务线后跑：

```powershell
cd apps\api
python -m pytest tests\test_p2_universality.py -v
```

这个测试**自动**在临时目录加 / 删 `test-line`（**不动** `registry.yaml`），
验证：
- API 启动 0 报错
- 9 → 10 → 9 个业务线
- 4 个引擎的 profile 数量同步
- 移除后**无残留**（关键）

---

## 常见错误

| 错误 | 原因 | 修法 |
|---|---|---|
| `manifest id 'X' does not match registry id 'Y'` | 目录名 vs manifest id 不一致 | 改其中之一 |
| `api_prefix must start with '/', got: 'X'` | manifest 的 `api_prefix` 漏了 / | 加 / |
| API 启动时 `/api/lines/X/__error__` 500 | `api/router.py` 顶层 import 抛错 | 检查 import；把重资源放 lazy / lifespan |
| 4 个引擎的 profile 数量对不上 | 某 YAML 缺了 | 复制 `residential/<engine>.yaml` 改 line_id |
| Web 端 404 业务线页面 | `manifest.yaml` 的 `id` ≠ 目录名 | 同步两者 |

---

## 完整字段文档

- `manifest.yaml` 全部字段：[`../../docs/plugin-howto.md`](../../docs/plugin-howto.md) §2
- `indicators.yaml` 全部字段：[`../../docs/plugin-howto.md`](../../docs/plugin-howto.md) §3
- `sensitivity.yaml` 4 输入 × N 输出：[`../../docs/maintenance/extending.md`](../../docs/maintenance/extending.md) §4
- `forecast.yaml` 方法（sma/ema/linear_trend/seasonal_naive）：[`../residential/forecast.yaml`](../residential/forecast.yaml)
- `alerts.yaml` 操作符：[`../../docs/maintenance/extending.md`](../../docs/maintenance/extending.md) §4.2
- DBT 项目样板：[`../../docs/plugin-howto.md`](../../docs/plugin-howto.md) §6

---

## "不要做"清单

- ❌ 不要 import `business_lines.<other_line>.X`（每个 line 独立）
- ❌ 不要在 `apps/` 改任何代码"为了支持新 line"
- ❌ 不要把 `change-me` 留在 manifest 里（一定漏改会失败）
- ❌ 不要在 `api_prefix` 用大写 / 空格
- ❌ 不要跳过 indicators.yaml（4 个引擎都需要 indicator_id）
- ❌ 不要让 router 顶层 import 阻塞（Redis / HTTP client → lifespan）
