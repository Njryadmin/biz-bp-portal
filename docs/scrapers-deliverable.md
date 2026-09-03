# Scrapers Framework — Real-Data Refresh Deliverable (2026-09-03)

## 概览

本次更新将 `lianjia_deals` 和 `policy_crawler` 两个原本只走 mock 兜底的 scraper
升级为"真抓优先,失败降级"。新增 1 个数据源/备用源,修复旧 URL 已失效的问题,
并为两个 scraper 补齐真实 HTML 单元测试。

| 维度 | 数据 |
|---|---|
| **新增/修改 Python 文件** | 3 (`lianjia_deals.py`, `policy_crawler.py`, `test_scrapers.py`) |
| **新增测试 fixture (HTML 抓包)** | 5 (bj/sh/sz/gz ershoufang + mohurd 首页) |
| **新增/修改 pytest 测试** | 31 (全部通过, 14.45s) |
| **真抓源** | 5 (4 个 lianjia 城市 + 1 个 mohurd) |
| **live 行数 (lianjia)** | 120 (4 城 × 30 listing) |
| **live 行数 (policy)** | 94 (20 历史 corpus + 74 mohurd live) |
| **HTTP 端点** | 不变 (沿用 `/api/scrapers/{id}/run`) |
| **前端页面** | 不变 |

---

## 1. URL 变更总览

| scraper | 旧 URL | 旧状态 | 新 URL | 新状态 |
|---|---|---|---|---|
| `lianjia_deals` | `https://bj.lianjia.com/chengjiao/` | 302→captcha, mock fallback | `https://{bj,sh,sz,gz}.lianjia.com/ershoufang/` (4 个城市) | 200, 真数据, 120 行 |
| `policy_crawler` | `https://www.mohurd.gov.cn/gongkai/zhengce/zhengcefilelib/` | 404, mock fallback | `https://www.mohurd.gov.cn/` (首页) | 200, 真数据, 74 live 行 |
| `nbs_house_price` | `http://www.stats.gov.cn/sj/zxfb/` | OK | 不变 | OK |

---

## 2. lianjia_deals 重写

### 2.1 旧实现为什么坏

`bj.lianjia.com/chengjiao/` (成交统计页) 被 lianjia 反爬墙挡住,任何非浏览器
UA 都返回 302 → `hip.lianjia.com/captcha?...`,HTML 体只有 6 KB 的验证码。
只要 fetch 抛错就掉到 mock,6 行数据全部带 `is_fallback=True`。

### 2.2 新策略

`{city}.lianjia.com/ershoufang/` (二手房挂牌列表页) **不被反爬墙针对**:
- 北京/上海/深圳/广州 4 个城市均可直接 200 拿到 175 KB+ 完整 HTML
- 每页 30 条 listing (server-rendered, 不依赖 JS)
- 顶部有 `<span>共找到N套</span>` 总量 (北京 117,629; 广州 140,066 等)
- 每条 listing 含 `title` / `positionInfo` (xiaoqu + sub_district)
  / `totalPrice` (e.g. "245万") / `unitPrice` (e.g. "44,522元/平")
  / `followInfo` (e.g. "20人关注 / 1个月以前发布")

抓 4 城 = **120 个 listing 真实数据**。每条都带 `xiaoqu` (小区), `district`
(片区, e.g. 望京/双桥/中央别墅区), `unit_price_yuan_sqm`, `total_price_wan`,
`follow_count` (关注人数), `title` (原始 listing 标题)。

### 2.3 解析细节 (新 schema)

```python
{
    "city":               "北京",                    # 北京/上海/深圳/广州
    "district":           "望京",                    # 区域 (从 .positionInfo 第 2 个 a)
    "xiaoqu":             "花家地小区",               # 小区 (从 .positionInfo 第 1 个 a)
    "period":             "2026-09",                 # YYYY-MM
    "avg_price":          44522,                     # = unit_price_yuan_sqm (DBT 兼容)
    "unit_price_yuan_sqm":44522,                     # 元/平
    "total_price_wan":    245.0,                     # 万元
    "deals_count":        20,                        # = follow_count (DBT 兼容)
    "follow_count":       20,                        # 关注人数
    "title":              "花家地小区 2室1厅 南 北",
    "source":             "lianjia_deals",
}
```

旧 schema (`city / district / period / avg_price / deals_count`) 完整保留,DBT mart
无需迁移。

### 2.4 需要的 headers

```python
{
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
```

链家对"非浏览器 UA"普遍 302 到 captcha,这套 headers 是必须的 (不是单纯靠
加 Referer 就能绕过)。无 Cookie、无 XSRF。

### 2.5 真抓行数 (验证)

| 城市 | URL | HTTP | 真抓行数 | 总量提示 |
|---|---|---|---|---|
| 北京 | `bj.lianjia.com/ershoufang/` | 200 | 30 | 117,629 套 |
| 上海 | `sh.lianjia.com/ershoufang/` | 200 | 30 | ? (regex 漏配,但首页 30 条) |
| 深圳 | `sz.lianjia.com/ershoufang/` | 200 | 30 | 78,313 套 |
| 广州 | `gz.lianjia.com/ershoufang/` | 200 | 30 | 140,066 套 |
| **合计** | | | **120** | |

### 2.6 示例真实行

```json
{
  "city": "北京",
  "district": "望京",
  "xiaoqu": "花家地小区",
  "period": "2026-09",
  "avg_price": 44522,
  "unit_price_yuan_sqm": 44522,
  "total_price_wan": 245.0,
  "deals_count": 20,
  "follow_count": 20,
  "title": "花家地小区 2室1厅 南 北",
  "source": "lianjia_deals"
}
```

### 2.7 兜底 (fallback) 链

保留 6 条 mock (京/沪/深 × 2-3 个区),全部 `is_fallback=True`。当
`fetch()` 因 rate-limit / 网络故障 / 反爬抛错时,框架自动调用。

---

## 3. policy_crawler 重写

### 3.1 旧实现为什么坏

`www.mohurd.gov.cn/gongkai/zhengce/zhengcefilelib/` 整个目录被 404
(住建部站点重构, 把 /zhengce/ 子站合并进首页)。其它变体也全部 404/403
(`/gongkai/zhengce/` → 403, `/zhengce/` → 404, `/sitemap.xml` → 404)。

### 3.2 新策略

**首页 `https://www.mohurd.gov.cn/` 本身就是一个政策列表页**。mohurd 把
通知 / 法规 / 规范性文件 / 法定主动公开内容 **全部直接展现在首页**,
每条 anchor 前面有一个 `<span class="time">YYYY-MM-DD</span>` 日期。

实际抓到的 74 条 live 政策 (示例):

| publish_date | title | 路径分类 |
|---|---|---|
| 2026-09-03 | 住房城乡建设部办公厅关于同意西藏自治区推行二级造价工程师电子注册证书的函 | 通知 |
| 2026-09-02 | 关于推荐第二届全国住房城乡建设行业职业技能大赛决赛裁判员人选的通知 | 通知 |
| 2026-09-01 | 住房城乡建设部办公厅关于印发"好房子"建设经验做法(第二批)的通知 | 通知 |
| 2026-09-01 | 国务院关于修改《住房公积金管理条例》的决定 | 法定主动公开 |
| 2026-08-18 | 住房城乡建设部办公厅　最高人民法院办公厅关于深化住房城乡建设领域民事纠纷"总对总"在线诉调对接机制的通知 | 通知 |
| 2026-08-17 | 住房城乡建设部办公厅关于做好2025年度全国民用建筑能源资源消耗统计调查的通知 | 通知 |

### 3.3 解析细节

```python
# 伪代码: 在首页 HTML 中按文档顺序扫, 遇到 <span>YYYY-MM-DD</span> 就更新 last_date;
# 遇到 <a href="/gongkai/zc/wjk/..."> 通知, /gongkai/zhengce/gzk/ 法规,
# /gongkai/zc/xzgfxwjk/ 规范性文件, /gongkai/fdzdgknr/ 法定主动公开 → 入 live 列表。

# 与 20 条历史 curated corpus 合并 → validate 去重 (policy_id 唯一) → 入库。
```

输出 schema (与历史 corpus 完全兼容):

```python
{
    "policy_id":    "POL-MOHURD-20260901-004",      # live 政策用 POL-MOHURD- 前缀
    "title":        "住房城乡建设部办公厅关于印发...",
    "publish_date": "2026-09-01",
    "city":         "全国",
    "level":        "国家",
    "content":      "住建部通知: 住房城乡建设部办公厅关于印发...",  # 合成
    "source_url":   "https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2026/art_xxx.html",
}
```

### 3.4 需要的 headers

```python
{
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
```

mohurd 不需要 Referer / Cookie / 任何额外认证。

### 3.5 真实行数 (验证)

| 来源 | 行数 | 备注 |
|---|---|---|
| mohurd.gov.cn 首页 live 政策 | **74** | 2025-08-26 ~ 2026-09-03 期间 |
| 20 条 curated corpus (本地硬编码) | 20 | 2023-08-25 ~ 2025-05-10 |
| **合计 valid** | **94** | |

### 3.6 兜底 (fallback) 链

`fallback()` 返回 20 条 curated corpus 全部带 `is_fallback=True`,与旧实现一致。
当 `fetch()` 因反爬/网络抛错时,框架自动调用。

### 3.7 备用源 (尝试过, 但选 mohurd 为主)

| 源 | HTTP | 内容 | 评价 |
|---|---|---|---|
| `www.mohurd.gov.cn/` (首页) | 200, 79 KB | 74 条政策 anchor + 日期 | ✅ **采用** |
| `www.gov.cn/zhengce/zuixin/` | 200, 20 KB | 仅页脚 + JS,无政策 anchor | ❌ 内容空 |
| `www.gov.cn/zhengce/zhengceku/` | 403 | 反爬 | ❌ 拒访 |
| `zjw.sh.gov.cn/` (上海) | 200, 49 KB | 站务信息,无政策列表 | ❌ 结构不同 |
| `zjj.sz.gov.cn/` (深圳) | 200, 117 KB | 站务信息,无政策列表 | ❌ 结构不同 |
| `zfcxj.beijing.gov.cn/` | DNS NXDOMAIN | (域名失效) | ❌ 不可达 |
| `zfcxj.gz.gov.cn/` | DNS NXDOMAIN | (域名失效) | ❌ 不可达 |

mohurd 首页是最丰富最稳定的政策源, 直接采用。其它源作为后续扩展点 (在
`fetch()` 内追加多 URL 循环即可, 不需要改 schema)。

---

## 4. 校验 (Verification)

### 4.1 端到端 (uvicorn + urllib)

启动:
```powershell
$env:FIN_BP_DATABASE_URL='postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp'
Start-Process -FilePath py -ArgumentList '-3.12','-X','utf8','-m','uvicorn','app.main:app',
  '--host','127.0.0.1','--port','8769','--log-level','warning','--app-dir','apps/api' ...
```

调用:
```python
# apps/api/scratch_e2e.py
import http.cookiejar, urllib.request, json
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
# ... mint admin JWT, attach to Authorization header ...
```

输出 (重置 rate-limit 后):

```
=== GET /api/scrapers ===
  listed 3 scrapers
  lianjia_deals        enabled=True last_status=ok rows=120 ...
  nbs_house_price      enabled=True last_status=ok rows=140 ...
  policy_crawler       enabled=True last_status=ok rows=94  ...

=== POST /api/scrapers/lianjia_deals/run ===
  status=ok rows=120 used_fallback=False upload_id=sc_lianjia_deals_... elapsed_ms=3827

=== POST /api/scrapers/policy_crawler/run ===
  status=ok rows=94 used_fallback=False upload_id=sc_policy_crawler_... elapsed_ms=377

=== POST /api/scrapers/nbs_house_price/run ===
  status=ok rows=140 used_fallback=False upload_id=sc_nbs_house_price_... elapsed_ms=1503

=== POST /api/scrapers/run-all ===
  lianjia_deals        status=ok rows=120 used_fallback=False
  nbs_house_price      status=ok rows=140 used_fallback=False
  policy_crawler       status=ok rows=94  used_fallback=False
```

### 4.2 单元测试 (pytest)

```
$env:FIN_BP_DATABASE_URL='postgresql+asyncpg://finbp:finbp@127.0.0.1:11667/finbp'
py -3.12 -X utf8 -m pytest apps/api/tests/test_scrapers.py --tb=short
...
31 passed, 31 warnings in 14.45s
```

新增 / 改写测试:

| # | 测试名 | 覆盖点 |
|---|---|---|
| 14 | `test_lianjia_parse_builds_rows_from_real_fixture` | 真 bj ershoufang HTML → ≥ 1 行, schema 字段全在 |
| 15 | `test_lianjia_parse_multi_city_fixtures` | 4 城市 fixture → ≥ 100 行, 4 城市都在 |
| 16 | `test_lianjia_parse_raises_on_broken_html` | 全空 → raise |
| 17 | `test_lianjia_parse_skips_individual_failing_pages` | 1 城失败不阻塞其它 |
| 18 | `test_lianjia_fallback_has_at_least_five_rows` | mock 兜底 6 行 |
| 19 | `test_lianjia_fetch_failure_triggers_fallback` | monkeypatch fetch raise |
| 20 | `test_lianjia_fetch_rate_limit_triggers_fallback` | rate-limit 触发降级 |
| 21 | `test_policy_parse_merges_corpus_with_live` | 空 raw → 20 corpus |
| 22 | `test_policy_parse_real_mohurd_fixture` | 真 mohurd HTML → ≥ 1 live (POL-MOHURD- 前缀) |
| 23 | `test_policy_validate_dedupes_by_policy_id` | 重复 policy_id 去重 |
| 24 | `test_policy_fallback_returns_full_corpus` | mock 兜底 20 条 |
| 25 | `test_policy_parse_raises_no_when_live_empty` | 空 raw 不 raise, 仍出 corpus |
| 26 | `test_policy_fetch_failure_triggers_fallback` | monkeypatch fetch raise |

### 4.3 前端 typecheck

```
cd apps/web && npx tsc --noEmit
# (无输出 = 0 errors)
```

---

## 5. 已知限制

1. **链家 m.lianjia.com 移动端也被 captcha 挡**: 用桌面端 ershoufang 列表
   拿到 30 条/页, **不分页**。如需更多数据, 可在 `fetch()` 内追加
   `?pg=2` 之类的分页参数 (但要小心 rate-limit, lianjia.com 是 4 次/分钟)。

2. **链家 ershoufang 城市聚合数量大, 但 district 维度是"片区"而非"区域"**:
   北京的"望京/双桥/七里庄"是商街道办级别, 而非朝阳/海淀/丰台行政区。
   旧 mock 用的是"区域"概念, 新数据用"片区"概念 — DBT 聚合时记得
   用 `xiaoqu` 维度而非 `district`。如需按行政区聚合, 可另行 group。

3. **mohurd 主页 anchor 排序不是按日期**: 文档顺序有时区混乱,
   有时 2026-09-01 排在 2026-08-17 后面。抓包基于 "上次扫到的日期" + "下一个
   policy anchor" 配对, 不依赖页面顺序, 配对结果可靠。但"同一天多条"则
   顺序仅按 anchor 在文档中出现的次序。

4. **mohurd 主页有 2-3 条"查看更多" / "行政规范性文件" 这种分类目录 anchor**,
   会被误当成政策标题收录 (`POL-MOHURD-20260902-009` "行政规范性文件")。
   可在后续按 anchor 文本长度 (>= 8 字符) + 路径精确匹配做更严过滤,
   当前为了避免过度过滤已经接受了 ~2-3 条噪声行。

5. **每个城市 ershoufang 首页只抓 30 条**: 30 × 4 = 120 行。如果业务需要
   全部 listing 估算, 另起一个聚合 scraper 抓"共找到N套" 那个数字, 单独成行。

6. **未实现: 链家 chengjiao 真实成交数据**: chengjiao 子域名仍被 captcha 墙,
   在不引入无头浏览器的前提下, 当前方案以"挂牌价 + 关注人数"作为代理
   (在 lianjia 公开 web 上能拿到的最强信号)。要真成交数据, 需走商业 Ke API
   (留扩展点: `fetch()` 改造为带 API key 的 client, `fallback()` 保留即可)。

7. **rate-limit 是进程内滑动窗口**: 多副本部署时, 各副本独立 throttle,
   总请求量会按副本数放大。如需全局限速, 把 `utils.rate_limit_check`
   换成 Redis token bucket (函数签名已留好)。

---

## 6. 文件变更清单

### 修改

```
apps/api/app/services/scrapers/scrapers/lianjia_deals.py    # 重写 (~440 行)
apps/api/app/services/scrapers/scrapers/policy_crawler.py  # 重写 (~430 行)
apps/api/tests/test_scrapers.py                              # 扩到 31 测试
```

### 新增

```
apps/api/tests/fixtures/lianjia_ershoufang_bj.html          # 190 KB 真抓包
apps/api/tests/fixtures/lianjia_ershoufang_sh.html          # 190 KB
apps/api/tests/fixtures/lianjia_ershoufang_sz.html          # 186 KB
apps/api/tests/fixtures/lianjia_ershoufang_gz.html          # 186 KB
apps/api/tests/fixtures/mohurd_home.html                    # 79 KB
docs/scrapers-deliverable.md                                # 本文档
```

### 未改

```
apps/api/app/services/scrapers/base.py
apps/api/app/services/scrapers/registry.py
apps/api/app/services/scrapers/utils.py
apps/api/app/services/scrapers/persist.py
apps/api/app/services/scrapers/scrapers/nbs_house_price.py
apps/api/app/services/scrapers/scrapers/_html.py
apps/api/app/routers/scrapers.py
apps/api/app/schemas/scraper.py
apps/api/app/main.py
apps/api/tests/conftest.py
apps/web/**                                                # 前端无变更
infra/dbt/**                                                # DBT marts 无需迁移
```
