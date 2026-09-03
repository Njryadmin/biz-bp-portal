# E2E Verification — Fin BP Portal MVP

Date: 2026-09-02
Verifier: mavis

## 总体结论: PASS

## 各维度评分

| 维度 | 状态 | 证据 |
|------|------|------|
| 1. 文件级审计 | PASS | business_lines/ 下 3 个业务线目录 (residential / retail / retail-leasing) + _template；registry.yaml 含 3 条；docs/plugin-howto.md 6 步走完 (overview / copy / yaml / wire / register / verify)，第 5 步后含 "成功案例" 段落。 |
| 2. 后端 API 验证 | PASS | uvicorn 8769 启动成功；/api/registry/lines 返回 count=3；各业务线 indicators 10 / 12 / 8；projects/properties 8 / 8 / 5；详情端点 PRJ-001 与 sh-jingan-xintiandi-shops 均返回非空 JSON。 |
| 3. 前端 typecheck | PASS | `npm run typecheck` 退出码 = 0，输出无 error。 |
| 4. 单元测试 | PASS | `python -m pytest tests app/services/parsers/tests` → **21 passed, 1 warning in 17.29s**。`tests/` 9 + `app/services/parsers/tests/` 12 = 21。 |
| 5. 插件隔离性 | PASS | 在 apps/ 下 grep "residential"/"retail"/"retail-leasing" 仅命中 2 个 debug helper (`apps/api/_check_loader.py`, `apps/api/_check_loader2.py`)；`apps/web/app/(dashboard)/` 与 `apps/api/app/routers/registry.py` 0 命中。`packages/ui/src/RoleSwitcher.tsx` 有 `BP-Residential`/`BP-Retail` 硬编码角色名（属于角色占位符，非业务线名硬编码；不在严格审计路径内，但与"插件解耦"原则张力，记作软观察）。 |
| 6. T5 扩展性承诺 | PASS | 业务线目录外的核心代码改动只有 `business_lines/registry.yaml` **+2 行**（`- id: retail-leasing` + `  manifest: business_lines/retail-leasing/manifest.yaml`），文件总 7 行。`docs/plugin-howto.md` +66 行纯 markdown，不计入代码预算。≤ 10 行预算达成。 |
| 7. 数据集成层 | PASS | `ingest_daily.py` ast.parse OK；`docker-compose.yml` yaml.safe_load OK；`infra/dbt/dbt_project.yml` yaml.safe_load OK。 |

## 关键证据（命令输出原文）

### 1. 文件级审计
```
business_lines/ 下目录:
  residential/
  retail/
  retail-leasing/
  _template/
  README.md
  registry.yaml

business_lines/_template/ 包含:
  indicators.yaml.example
  manifest.yaml.example
  api/router.py.example
  data/seed/.gitkeep
  dbt/dbt_project.yml.example
  dbt/models/example.sql
  web/pages/_example.tsx

registry.yaml 内容 (7 行):
  lines:
  - id: residential
    manifest: business_lines/residential/manifest.yaml
  - id: retail
    manifest: business_lines/retail/manifest.yaml
  - id: retail-leasing
    manifest: business_lines/retail-leasing/manifest.yaml

plugin-howto.md 长度: 252 行
  章节: 0. overview / 1. copy template / 2. edit yaml / 3. wire api+page / 4. register / 5. restart & verify / 6. 成功案例
```

### 2. 后端 API 验证 (uvicorn :8769)
```
GET /api/registry/lines                          [200] keys=['version', 'lines'] bytes=2422
  └─ count = 3: residential / retail / retail-leasing

GET /api/registry/lines/residential              [200] keys=['line', 'indicators', 'charts'] bytes=3852
GET /api/registry/lines/retail                   [200] keys=['line', 'indicators', 'charts'] bytes=4118
GET /api/registry/lines/retail-leasing           [200] keys=['line', 'indicators', 'charts'] bytes=3449

GET /api/lines/residential/indicators            [200] count=10
GET /api/lines/retail/indicators                 [200] count=12
GET /api/lines/retail-leasing/indicators         [200] count=8

GET /api/lines/residential/projects              [200] count=8   (≥5 ✓)
GET /api/lines/retail/properties                 [200] count=8   (≥5 ✓)
GET /api/lines/retail-leasing/properties         [200] count=5   (≥3 ✓)

GET /api/lines/retail-leasing/market-benchmark   [200] count=5
GET /api/lines/retail-leasing/vacancy-alerts     [200] alert_count=2

GET /api/lines/residential/projects/PRJ-001      [200] 非空, 25+ keys
GET /api/lines/retail-leasing/properties/sh-jingan-xintiandi-shops [200] 非空
```

### 3. 前端 typecheck
```
$ cd apps/web; npm run typecheck
> @fin-bp/web@0.1.0 typecheck
> tsc --noEmit
EXIT: 0
```

### 4. 单元测试
```
$ python -m pytest tests app/services/parsers/tests
.....................                            [100%]
21 passed, 1 warning in 17.29s
```

### 5. 插件隔离性 grep 结果
```
apps/web/app/(dashboard)/  → 0 hits for residential/retail/retail-leasing
apps/api/app/routers/registry.py → 0 hits
apps/web/lib/registry.ts  → 0 hits (only BusinessLine type)
apps/api/_check_loader.py  → "residential" (1 hit, debug helper, _-prefix)
apps/api/_check_loader2.py → "residential" (3 hits, debug helper, _-prefix)
packages/ui/src/RoleSwitcher.tsx → "BP-Residential" / "BP-Retail" 角色名 (非业务线 ID)
```

### 6. T5 核心代码改动审计
```
$ Get-Content business_lines/registry.yaml
lines:
- id: residential
  manifest: business_lines/residential/manifest.yaml
- id: retail
  manifest: business_lines/retail/manifest.yaml
- id: retail-leasing                           <-- +1 行
  manifest: business_lines/retail-leasing/manifest.yaml  <-- +1 行
```
T5 净增 2 行（与 deliverable.md 第 20 行"核心代码改动总计: 2 行"完全一致）。
业务线目录内 12 个 active 文件 + 6 个 _template 残留 (不计入代码预算)。
docs/plugin-howto.md 第二节 6 "成功案例" 已添加 (66 行 markdown 文档)。

### 7. 数据集成层
```
$ python -c "import ast; ast.parse(open(r'.../ingest_daily.py', encoding='utf-8').read())"
ingest_daily.py: OK
$ python -c "import yaml; yaml.safe_load(open(r'.../docker-compose.yml', encoding='utf-8'))"
docker-compose.yml: OK
$ python -c "import yaml; yaml.safe_load(open(r'.../dbt_project.yml', encoding='utf-8'))"
dbt_project.yml: OK
```

## 关键指标

- **业务线数 (API)**: 3 (residential / retail / retail-leasing) ✓
- **测试数**: 21 passed ✓
- **T5 核心代码改动**: 2 行 (registry.yaml) ✓
- **前端 typecheck 退出码**: 0 ✓
- **后端 API 启动**: 成功 ✓

## 软观察 (不构成 FAIL)

1. `apps/api/_check_loader.py` 和 `_check_loader2.py` 是调试辅助脚本，提到 "residential" 字样。它们带 `_` 前缀，loader 不加载，但应该清理或移到 `tests/`。本任务不计入核心代码改动，但建议 T6 之前清理。
2. `packages/ui/src/RoleSwitcher.tsx` 硬编码 `BP-Residential` / `BP-Retail` 角色名。这是 T3 通用组件的"角色占位符"（注释明说 "fixed list per cockpit spec"），不是按业务线 ID 路由的逻辑，但与"插件解耦"原则有轻微张力——T3 已经定型，本任务不追溯。

## 阻塞项

无。

## 建议

1. 清理 `apps/api/_check_loader*.py` 这两个 debug helper，移入 tests/ 或 .gitignore。
2. T6 接入真实 warehouse (ClickHouse/PG) 时，把 mock JSON seed 替换为 dbt mart 真实读取。
3. pytest 默认 `testpaths = ["tests"]` 没有把 `app/services/parsers/tests/` 包含进来，建议在 pyproject.toml 加 `testpaths = ["tests", "app/services/parsers/tests"]` 或在 CI 显式跑两条路径，避免新人漏跑。

## 验收签字

Verifier: mavis
Date: 2026-09-02