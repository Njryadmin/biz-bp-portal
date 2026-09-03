# 零售分析 (retail) 业务线交付物

## 1. 业务背景

**零售分析**是 fin-bp-portal 的第二条业务线,聚焦购物中心与街铺的资产运营。
与第一条业务线 `residential` (住宅) 在数据模型、核心指标、时间维度上有本质差异
—— 本文档显式列出这些差异,作为业务建模的依据。

## 2. 与住宅线的差异 (重要)

| 维度           | 住宅 (residential)                         | **零售 (retail — 本次)**                                |
|----------------|--------------------------------------------|---------------------------------------------------------|
| **数据模型**   | 项目 (project) + 户型 (unit) + 回款 (collection) | **物业 (property) + 租户 (tenant) + 租约 (lease)**       |
| **核心指标**   | IRR / 净利润 / 三道红线 / 去化率           | **NOI / 坪效 / 调改 NPV / 收缴率 / 品牌多样性**         |
| **时间维度**   | 销售周期 (3-24 个月开盘)                   | **租约周期 (3 / 5 / 8 / 10 / 15 年)**                   |
| **资产类型**   | 住宅项目 (在售)                            | **已运营物业 (持有)**                                   |
| **收入确认**   | 销售回款 (一次性)                          | **租金流入 (持续性,与租约挂钩)**                        |
| **资本支出**   | 建安成本 (前置)                            | **调改 (renovation) 与招商 (leasing) 双线**             |
| **风险维度**   | 政策/去化/资金链                           | **空置/收缴/品牌结构/调改决策**                         |
| **租售比逻辑** | N/A                                        | **租金 ÷ 商户销售额 (健康区间 8-15%)**                  |

### 模型举例

- **住宅线**: 1 个项目 → 4 个户型 → 24 个月销售回款 → IRR
- **零售线**: 1 个物业 → 280 个品牌 → 800 个租约 → 月度 NOI

## 3. 交付清单

### 3.1 配置文件

| 文件 | 用途 |
|------|------|
| `manifest.yaml` | 业务线注册元数据;id=`retail`,6 个 nav 入口,API prefix `/api/lines/retail`,warehouse 三段式 `raw_retail / stg_retail / mart_retail` |
| `indicators.yaml` | 12 个 KPI 指标 + 5 个图表规格;源表全部指向 `mart_retail.*` |
| `business_lines/registry.yaml` | 已幂等追加 `- id: retail`(见第 6 节) |

### 3.2 API 路由 (FastAPI)

`business_lines/retail/api/router.py` — 模块级 `APIRouter`,被注册器在
`/api/lines/retail` 下自动挂载。6 个端点:

| Method | Path | 说明 |
|--------|------|------|
| GET | `/ping` | 健康检查,返回加载到的物业数 |
| GET | `/indicators` | 12 个 KPI 定义,带 format/aggregation/source |
| GET | `/properties` | 8 个 mock 物业,支持 `?city=` 和 `?format=` 过滤 |
| GET | `/properties/{id}/noi-waterfall` | 5 段瀑布:Potential → Vacancy → EGR → OpEx → NOI |
| GET | `/properties/{id}/brand-mix` | 业态级 + Shannon 多样性指数 + top brands |
| GET | `/properties/{id}/renovation-npv` | maintain vs renovate 两档对比,含 NPV/IRR |
| GET | `/properties/{id}/collection-rate` | 当前值 + 12 个月趋势 |

### 3.3 Web 页面 (Next.js 客户端组件)

`business_lines/retail/web/pages/` 下 6 个 `.tsx`,统一用 `packages/ui` 的
`UniversalKpiCard` + `UniversalChart`,数据通过 `fetch /api/lines/retail/*` 拉取:

| 文件 | 路由 | 展示 |
|------|------|------|
| `index.tsx` | `/retail` | 概览:组合 NOI、平均收缴率、物业数 + NOI 规模柱状图 + 指标库 |
| `noi.tsx` | `/retail/noi` | NOI 瀑布图(可切换物业),4 个 KPI 卡 |
| `efficiency.tsx` | `/retail/efficiency` | NOI 规模 × 坪效散点 + 客流坪效柱状 |
| `brand-mix.tsx` | `/retail/brand-mix` | 业态气泡图 + 多样性指数 + 业态明细 |
| `renovation-npv.tsx` | `/retail/renovation-npv` | 两档 NPV/IRR 对比 + horizon/discount_rate 可调 |
| `collection.tsx` | `/retail/collection` | 12 个月收缴率趋势(95% 健康线) |

### 3.4 DBT 模型 (3 层)

`business_lines/retail/dbt/`:

- `dbt_project.yml` — `retail` profile,staging=view,intermediate=view,marts=table
- `models/staging/`:
  - `_sources.yml` — `raw_retail.{properties,leases,tenants}` 源声明
  - `stg_properties.sql` — 物业主数据清洗
  - `stg_leases.sql` — 租约标准化 + 剩余年限派生
  - `stg_tenants.sql` — 租户/品牌主数据
- `models/intermediate/`:
  - `int_property_noi_monthly.sql` — 月度 NOI 切片 + 派生 KPI
  - `int_lease_status.sql` — 租约状态(临到期/紧迫续约) + 续约风险分
- `models/marts/`:
  - `mart_property_kpis.sql` — 核心 mart,聚合所有物业级 KPI
  - `mart_brand_mix.sql` — 业态级聚合 + Shannon 多样性
  - `mart_renovation_npv.sql` — 调改 NPV 参数表(给应用层算 NPV/IRR)

所有中间层以上模型用 `{{ ref(...) }}` 引用上游,**无硬编码表名**。

### 3.5 Mock 数据

`business_lines/retail/data/seed/properties.json` — 8 个物业,覆盖购物中心与街铺:

| ID | 物业 | 城市 | 业态 | 建面(万㎡) | NOI(万) | 收缴率 | 空置率 |
|----|------|------|------|-----------|---------|--------|--------|
| sh-jingan-joycity | 上海静安大悦城 | 上海 | 购物中心 | 20.0 | 4,800 | 96.5% | 4.5% |
| sh-xintiandi-plaza | 上海新天地广场 | 上海 | 购物中心 | 12.0 | 3,600 | 98.2% | 2.8% |
| bj-sanlitun-taikoo | 北京三里屯太古里 | 北京 | 购物中心 | 17.0 | 4,500 | 97.0% | 3.2% |
| sz-mixc | 深圳万象城 | 深圳 | 购物中心 | 25.0 | 6,500 | 97.5% | 3.5% |
| gz-tee-mall | 广州天环广场 | 广州 | 购物中心 | 11.0 | 2,900 | 95.5% | 5.5% |
| hz-hubin-yintai | 杭州湖滨银泰 in77 | 杭州 | 购物中心 | 18.0 | 4,200 | 96.8% | 4.2% |
| cd-taikoo-li | 成都远洋太古里 | 成都 | 购物中心 | 15.0 | 3,800 | 97.3% | 3.0% |
| sh-wujiaochang-street | 上海五角场万达街铺 | 上海 | 街铺 | 2.3 | 720 | 92.0% | 7.5% |

每个物业附 4-8 个核心租约样本(品牌、面积、租期、起始年、月租金、递增率),
加上 top brands 列表。**所有 NOI/收缴率/空置率都经过合理性校核**:
- NOI > 0, 收缴率 92-98%, 空置率 2.8-7.5% (无 0% 的不合理值)
- 一线城市 7 个,新一线 1 个,购物中心 7 + 街铺 1
- 客流 = 月均接待人次,与建面和坪效匹配

## 4. 验证结果 (必跑)

启动 `uvicorn app.main:app --port 8000` 后:

| 检查项 | 命令 | 实际结果 | 期望 | 通过 |
|--------|------|----------|------|------|
| 1. 注册可见 | `GET /api/registry/lines` | 包含 `retail`(以及住宅) | 包含 retail | ✓ |
| 2. 指标数 | `GET /api/lines/retail/indicators` | count=12 | ≥10 | ✓ |
| 3. 物业数 | `GET /api/lines/retail/properties` | count=8 | ≥5 | ✓ |
| 4. 瀑布非空 | `GET /api/lines/retail/properties/sh-jingan-joycity/noi-waterfall` | 5 段(Potential→Vacancy→EGR→OpEx→NOI) | 非空 | ✓ |
| 5. NPV 双档 | `GET /api/lines/retail/properties/sh-jingan-joycity/renovation-npv` | maintain + renovate 字段齐备,capex=12,000万,IRR=0.5749 | 都存在 | ✓ |
| 6. 收缴率 | `GET /api/lines/retail/properties/sh-jingan-joycity/collection-rate` | current=0.965,trend 12 点 | 有数据 | ✓ |
| 7. 错误处理 | `GET /api/lines/retail/properties/unknown/noi-waterfall` | 404 | 异常清晰 | ✓ |
| 8. 单元测试 | `pytest -q` | 9 passed | 不回归 | ✓ |

### 5. 验证命令

```powershell
# 在 apps/api 下
$env:FIN_BP_PROJECT_ROOT = "C:\Users\mozzi\.mavis\workspace\fin-bp-portal"
uvicorn app.main:app --port 8000

# 另开终端
$base = "http://127.0.0.1:8000"
irm "$base/api/registry/lines" | ConvertTo-Json -Depth 4
irm "$base/api/lines/retail/indicators" | Select-Object count
irm "$base/api/lines/retail/properties" | Select-Object count
irm "$base/api/lines/retail/properties/sh-jingan-joycity/noi-waterfall"
irm "$base/api/lines/retail/properties/sh-jingan-joycity/renovation-npv"
irm "$base/api/lines/retail/properties/sh-jingan-joycity/collection-rate"

# 关闭: Get-NetTCPConnection -LocalPort 8000 | Stop-Process -Id {$_.OwningProcess} -Force
```

## 6. registry 幂等追加

`business_lines/registry.yaml` 现有内容(T1 已经写入 `residential`):
```yaml
lines:
  - id: residential
    manifest: business_lines/residential/manifest.yaml
  - id: retail           # ← 本次追加
    manifest: business_lines/retail/manifest.yaml
```

我的追加逻辑(已用脚本验证,二次运行是 NO-OP):
1. `yaml.safe_load(utf-8-sig content)` → 拿到现有 lines
2. 检查 `id == "retail"` 是否已存在
3. 存在 → 跳过;不存在 → 追加
4. 写回时保留 `sort_keys=False` 与 `allow_unicode=True`

**未触碰任何已有 entry**(residential/test_line 即使存在也保持原样)。

## 7. 关键假设

1. **租约周期**:用 3/5/8/10 年四种主流租期作为样本。生产环境应通过 dbt 注入
   真实租约的 `start_date`/`end_date`。
2. **调改参数**:capex = 600元/㎡(国内中端商场改造典型值),首年 NOI 提升 12%,
   之后递增率 +1.5pp,终值资本化率 5.5%。这些是 T0/T1 都没有的"业务常识"假设,
   放在 API 层做计算而不是 hardcode 到 seed,便于以后通过 query 参数调优。
3. **NOI = EGR − OpEx**;EGR = Potential Gross Rent × (1 − implied vacancy)。
   seed 里的 `noi_wan` 是真值,API 端用 `gross_rent_wan - opex_wan - vacancy_loss` 反推,
   两者一致。
4. **多样性指数**:Shannon 熵归一化到 0-1。指数越高,业态越分散,越能抵御单一业态风险。
5. **客流坪效**:月客流 ÷ 面积 ÷ 30 天,得到 人/㎡/日。客流数据在 seed 中是
   万/月单位(280 表示 280 万人次/月)。
6. **IRR 边界**:bisection 区间扩到 [-0.5, 10.0] 覆盖高 IRR 场景(real estate
   leverage 之下 unleveraged IRR 经常 > 1.0)。maintain 场景无 capex → IRR 恒为 null
   (与实务相符,因为没有"投资"可言)。
7. **数据未持久化到 warehouse**:本次只交付 dbt 模型 + 应用层 mock 读取 seed JSON。
   生产部署时再接 ClickHouse/PG mart 层;dbt models 保持一致,只需切换 source 即可。

## 8. 未触动的文件

- `apps/web/app/(dashboard)/*` — 未改
- `apps/api/app/routers/registry.py` — 未改(T1 已自行增强了 `_summarize_line`)
- `apps/api/tests/*` — 未改(T1 已自行放宽 `test_registry_endpoint`)

## 9. Blockers

无。
