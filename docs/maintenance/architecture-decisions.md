# Architecture Decisions — 关键设计决策的"为什么"

> 读者：想知道"为什么要这样设计"而不是"它怎么工作"的工程师 / 架构师。
> 配套：[`MAINTENANCE.md`](../../MAINTENANCE.md) §9 约定；[`docs/architecture-overview.md`](../architecture-overview.md) 5 张架构图。

每条决策包含：**问题**（当时的约束）→ **决定**（选了哪条路）→ **后果**（接受了什么、放弃什么）→ **代码位置**。

---

## 1. 为什么用 BFF 代理（而不是浏览器直连 API）

**问题**：浏览器在 `:3000`，API 在 `:8769`（或生产 `:8000`）。
两者**不同主机**——现代浏览器（Chrome 80+）默认不再发送第三方 cookie。

**决定**：所有浏览器 → API 的请求走 `apps/web/app/api/**/route.ts` 的 BFF。
BFF 在同源（`:3000`）被调用，内部 fetch 转发到 API。
`request.headers.get("cookie")` 透传到上游 API，让 RBAC 正常工作。

**后果**：
- ✅ 浏览器永远只跟 `:3000` 说话，cookie 同源工作
- ✅ API 与 Web 可以独立部署 / 伸缩
- ❌ 多了一层网络跳；调试多一层
- ❌ 每个 API 端点都要有对应的 BFF（catch-all 缓解但非万能）

**代码位置**：
- `apps/web/middleware.ts:1`（cookie 守卫）
- `apps/web/app/api/ai-models/[[...path]]/route.ts:1`（catch-all 模板）
- `apps/web/app/api/lines/[[...path]]/route.ts:1`（动态业务线 catch-all）
- `apps/web/app/api/auth/login/route.ts:35-38`（Set-Cookie 复制）

**反事实**：如果浏览器仍允许第三方 cookie（2019 之前），这套可以省掉。

---

## 2. 为什么用 `X-Service-Token` 头（而不是 mTLS / service mesh）

**问题**：Copilot 引擎内部通过 HTTP 调 `/api/lines/<line>/...` 拉数据
（参见 `apps/api/app/services/llm/mock_helpers.py:42-44`）。这是**进程内
mock 引擎 → API** 的调用，不是浏览器。

**决定**：用 `X-Service-Token: $BIZ_BP_SERVICE_TOKEN` 头。
API 端读这个头，跳过 RBAC（因为是已知的内部调用方）。

**后果**：
- ✅ 零依赖（不需要 mTLS / Vault Agent / Istio）
- ✅ token 在环境变量里，rotate 简单
- ✅ mock 引擎可以走"完整 HTTP 路径"测试（不止单元测试）
- ❌ 同一 secret 在所有 API 实例必须一致
- ❌ 比 mTLS 弱（任何能读环境变量的人能伪造）

**代码位置**：
- `infra/docker-compose.yml:67`（`BIZ_BP_SERVICE_TOKEN` 默认值）
- `apps/api/app/services/llm/mock_helpers.py:42-44`（设置 header）
- `apps/api/app/core/auth.py:378-388`（读 header 跳过 RBAC）

**反事实**：服务网格（Istio / Linkerd）会更安全，但本地 dev 与小规模生产过重。

---

## 3. 为什么 `bp:<line>` 角色字符串（而不是 role × line 二维表）

**问题**：用户与业务线的关系是 M:N（一个用户能看多个 line，一个 line 有多个用户）。
两种建模：
- **方案 A**：role 字符串 `bp:<line>`，每个 line 一行
- **方案 B**：独立的 `(user, role, line)` 三列

**决定**：方案 A + **同步**一张 `user_business_lines` 表（冗余但快）。

**后果**：
- ✅ role 检查只查 `user_roles`（`has_role("bp:residential")` 一步）
- ✅ `accessible_lines` 直接从 role 解析（`role[3:]` 切片）
- ✅ `user_business_lines` 表的 EXISTS 子查询仍然可作 fallback
- ❌ 改一个用户的业务线要 INSERT + DELETE 两步
- ❌ 同一 line 的两种角色（如 `bp:residential` + `viewer`）会冲突

**代码位置**：
- `apps/api/app/core/auth.py:25-35`（角色命名空间文档）
- `apps/api/app/core/rbac.py:181-198`（`filter_accessible_lines` 用两表 union）
- `apps/api/app/db/seed_users.py:197-206`（创建 `bp-<line>` 用户时同时插 role + business_line）

**反事实**：方案 B 更"规范化"，但每次查 accessible_lines 要 JOIN。10 条业务线规模下不值得。

---

## 4. 为什么审计中间件"重试一次"

**问题**：asyncpg 缓存的连接池在 `pgserver` 重启 / 进程崩溃恢复后，
可能持有"已关闭"的 connection。`session.execute()` 第一次会失败（`NoneType has no
attribute 'send'`），第二次重连就好了。

**决定**：
1. audit 写入失败 → `reset_engine()` 丢掉旧池
2. 重新建会话，重试一次
3. 二次失败仅 WARNING log，**绝不抛**

**后果**：
- ✅ DB 临时抖动不影响 API 响应（audit 是 sidecar，不是 gate）
- ✅ `pool_pre_ping=True` 解决 99% 的场景（剩下 1% 由 retry 兜底）
- ❌ 真的"DB 不可达"会被 audit 静默吞掉——但应用层会先报错
- ❌ 失败事件不重试太多次（2 次封顶），不会撑爆 event loop

**代码位置**：`apps/api/app/middleware/audit.py:140-175`

**反事实**：每次失败都重试 → DB 真挂时 event loop 被 audit 任务堆满。
每次失败都抛 → 任何 audit 失败都 500 用户。

---

## 5. 为什么 `BIZ_BP_AI_SECRET_KEY`（Fernet 加密 api_key）

**问题**：管理后台的 LLM api_key 是**写一次的 secret**——用户粘到 UI，
**从不读回**。但万一数据库泄露，攻击者能调真实 LLM → 烧钱 / 钓鱼。

**决定**：
- Fernet（AES-128-CBC + HMAC-SHA256，32 字节 URL-safe base64 key）
- 数据库存密文，读时解密
- `env:VAR` 引用：完全不进数据库，运行时读环境变量
- `plain:` 标记：dev 模式无 key 时的 fallback（不是"明文安全"，是"开发体验"）

**后果**：
- ✅ DB dump 泄露 ≠ secret 泄露
- ✅ `env:VAR` 模式适合 CI / k8s secret
- ❌ key 轮换 = 重新输所有 key（当前**没有**自动迁移脚本）
- ❌ 弱 secret（如 8 字符）= Fernet 仍是 32 字节，但 secret 弱没救

**代码位置**：
- `apps/api/app/core/secret.py:1`（实现 + 三种格式处理）
- `apps/api/app/core/config.py:38`（settings.ai_secret_key 字段）
- `infra/docker-compose.yml:71`（环境变量名 `BIZ_BP_AI_SECRET_KEY`）

**反事实**：HSM / Vault 更好，但本地 dev / 小规模生产不值得。

---

## 6. 为什么 `clear_email` / `api_key: ""` 显式清空

**问题**：
- Pydantic `EmailStr` 把 `""` 当非法（前端"清空"按钮发空串 → 422）
- Pydantic `Optional[str]` 不区分 `""`（前端发 null 才是"清空"）和 `""`（前端"未传"）

如果用 `Optional[str] = None`：
- 前端想"清空" → 必须发 `null`（违反直觉，多数前端默认发 `""`）
- 前端想"保留不变" → 不传字段

**决定**：用**两个**字段：
- 值字段：`email: Optional[EmailStr] = None`（校验格式）
- 标志字段：`clear_email: bool = False`（显式清空）

ai_models.api_key 同理（`api_key: ""` = 清空，`api_key: null` = 不动）。

**后果**：
- ✅ 前端 UI 简单（清空链接 = 发 `""`）
- ✅ Pydantic 校验还能跑（`""` 不进 `EmailStr` 字段）
- ❌ schema 字段多一个（每个"可选且可清空"字段都要 1 个标志）
- ❌ router 必须 if/elif 三路（清空 / 改值 / 不动）

**代码位置**：
- `apps/api/app/routers/auth.py` PATCH users 端点（`clear_email` 标志）
- `apps/api/app/routers/ai_models.py` PATCH 端点（`api_key == ""` 检测）

**反事实**：如果前端统一用 `null` 表示"清空"，这个 hack 可以省掉。

---

## 7. 为什么 Windows reparse-point junction（`C:\Users\mozzi\.mavis\workspace\biz-bp-portal` → `C:\Users\mozzi\.minimax\workspace\biz-bp-portal`）

**问题**：Mavis 的 workspace 目录在 `C:\Users\mozzi\.mavis\workspace`，
但 `mavis` 是 mock profile（被 mavis 系统在每次 session 重启时清空）。
真实项目需要在 `C:\Users\mozzi\.minimax\workspace`（profile 持久化目录）。

**决定**：在 `.mavis/workspace` 下用 **Windows reparse-point**（NTFS junction）
指向 `.minimax/workspace` 的实际目录。

```powershell
# 创建
fsutil reparsepoint create C:\Users\mozzi\.mavis\workspace\biz-bp-portal C:\Users\mozzi\.minimax\workspace\biz-bp-portal
```

**后果**：
- ✅ Mavis 看到 `C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 存在
- ✅ 真实文件在 `.minimax`（profile 重启不丢）
- ✅ 人类在两个路径都能工作（透明）
- ❌ Git 在 junction 路径下偶尔看到"自己指向自己"的假象
- ❌ `mv` / `rm` 真实目录会断链
- ❌ 一些 PowerShell cmdlet 对 reparse-point 处理不一致

**代码位置**：commit `cf2d8f1`（加了 workaround，让某些工具正确解析 reparse-point）。

**反事实**：把 mavis profile 设成持久化更干净，但改 Mavis 配置不在项目范围。

---

## 8. 为什么只有 3 个硬编码爬虫（不是插件系统）

**问题**：数据源有无限可能（链家、NBS、住建部、自如、贝壳、统计局公报……）
两种建模：
- **方案 A**：硬编码 3 个 scraper 文件 + auto-discovery
- **方案 B**：插件系统，开发者写 `scrapers/<id>.py` 即可

**决定**：方案 A。理由：
1. 真实数据源**只**这 3 个有 ROI（链家 / NBS / 住建部）——其它源（贝壳等）反爬墙
   严重，破解一次只能撑几个月
2. 插件系统需要"如何安全地执行第三方代码"（沙箱 / 签名 / 隔离）——不解决就别做
3. 内部业务线爬虫（从业务系统抓）走 `business_lines/<line>/api/router.py`，
   **已经**是插件化的

**后果**：
- ✅ 3 个爬虫做深做透（`docs/scrapers-deliverable.md` 记录每次失败 / 降级 / 真抓的策略）
- ✅ 解析层通用（`apps/api/app/services/scrapers/base.py`）
- ❌ 新加一个公开数据源要改核心代码
- ❌ 上游一变就要 hotfix

**代码位置**：
- `apps/api/app/services/scrapers/scrapers/{nbs_house_price,lianjia_deals,policy_crawler}.py`
- `apps/api/app/services/scrapers/base.py`（通用解析 + 验证 + 落库）

**反事实**：如果业务需要 10+ 个外部源，再做插件系统。当前 3 个不值得。

---

## 9. 为什么 `audit_log` 在 `raw` schema（不是 `audit` 或 `log`）

**问题**：审计日志**也是** landing data——一次 HTTP 请求是一条"事件"。
放哪？

**决定**：放 `raw.audit_log`。
1. 与 `raw.uploads`（爬虫 / 上传）共享 retention policy
2. 已经存在的 DBT 模式可以扩展（不需要再创一个 schema）
3. 表名前缀 `audit_` 明确（不会跟业务数据混）

**后果**：
- ✅ 一个 `raw` 包含全部"未加工的事件源"
- ✅ 同一 `pg_dump` 备份包含 audit
- ❌ audit 表可能变得**比业务表还大**（每个 HTTP 请求 1 行）
- ❌ `raw` schema 名不再"raw"（是"事件落地"）

**代码位置**：`apps/api/app/db/bootstrap.py:133-150`（`AUTH_DDL` 的 audit_log DDL）。

**反事实**：单独 `audit` schema 更"语义化"，但跨 schema JOIN 更烦。

---

## 10. 为什么 `httpOnly` cookie（不是 localStorage）

**问题**：JWT 存哪？三种主流选择：
- **httpOnly cookie**——前端 JS 读不到，XSS 偷不走
- **localStorage**——前端 JS 读得到，XSS 能偷
- **sessionStorage**——同 localStorage，区别是关 tab 失效

**决定**：httpOnly cookie，名字 `finbp_token`（可配 `BIZ_BP_COOKIE_NAME`）。

**后果**：
- ✅ XSS 偷不到 token
- ✅ 后端 RBAC 完全在 cookie 上跑
- ✅ CSRF 风险靠 SameSite=Lax 缓解（默认）
- ❌ 不能用 BFF 之外的 client（mobile / CLI）共享 session——需要单独走
  `Authorization: Bearer <token>` 头（API 已支持）
- ❌ 改 cookie name 会让所有用户重新登录

**代码位置**：
- `apps/api/app/core/auth.py:9-11`（httpOnly 选择文档）
- `apps/api/app/core/config.py:31`（`cookie_name` 字段）
- `apps/web/middleware.ts:21`（middleware 读 `BIZ_BP_COOKIE_NAME`）

**反事实**：现代 SPA 多用 httpOnly + SameSite=Lax（OAuth / OIDC 默认也是）。其他方式已经被淘汰。

---

## 11. 为什么 Copilot "fallback to mock" 是 200（不是 5xx）

**问题**：用户问"XX 项目 IRR 多少"。LLM API 不可达时怎么办？
- **方案 A**：返回 503，前端显示"服务暂时不可达，请稍后"
- **方案 B**：返回 200 + `used_fallback: true` + 规则引擎答案 + `fallback_reason`

**决定**：方案 B。

**后果**：
- ✅ 用户**永远**拿到答案（"我拿不到 LLM 的回答，根据规则的兜底是 …"）
- ✅ 业务连续性比"诚实地说失败"更重要（这是内部工具）
- ❌ 用户分不清"真实 LLM 答案"和"规则引擎兜底"（用 `backend` 字段 + UI 标记）
- ❌ 隐藏了 LLM API 的健康度（需要单独的 `/api/copilot/health` 暴露）

**代码位置**：
- `apps/api/app/services/copilot_engine.py:1`（架构图）
- `apps/api/app/services/llm/factory.py`（fallback chain）
- `apps/api/app/routers/copilot.py`（端点签名）

**反事实**：To-C 产品可能更喜欢"显式失败"。To-B 内部工具的偏好是"永远工作"。

---

## 12. 为什么 `force-dynamic` 写在每个 BFF route（而不是全局）

**问题**：Next.js 14 默认尝试**静态化** API 路由（pre-render at build time）。
对 BFF 来说这是 bug——BFF 必须每次实时转发。

**决定**：每个 BFF route.ts 第一行都写：
```typescript
export const dynamic = "force-dynamic";
export const revalidate = 0;
```

**后果**：
- ✅ 显式语义（看到这行就知道是 BFF）
- ✅ 单个 route 可独立覆盖（如果某个特例需要缓存）
- ❌ 9 个 BFF route 要写 9 次
- ❌ 容易漏（新人加 BFF 不写 → 静态化失败）

**代码位置**：`apps/web/app/api/**/route.ts`（每个文件第 9-11 行）

**反事实**：`next.config.js` 可以全局禁静态化，但**所有** API 路由（包括静态资源）都受影响。

---

## 13. 为什么业务线 plugin 用 `importlib`（而不是 entry_points）

**问题**：业务线有 9 个，每个有自己的 `api/router.py`。后端怎么发现？
- **方案 A**：Python `entry_points`（`pyproject.toml` 注册）—— 适合发布到 PyPI
- **方案 B**：`importlib.util.spec_from_file_location`—— 直接 load 任意路径的 .py
- **方案 C**：用 `__import__` + `pkgutil.iter_modules`—— 标准包机制

**决定**：方案 B。

**后果**：
- ✅ 不需要 `pip install business-line-x`（开发者改 YAML + 加文件即可）
- ✅ 业务线代码**永远在仓库里**（不是 npm/PyPI 黑盒）
- ✅ 0 业务线硬编码（`registry.py` 不写任何 line id）
- ❌ 业务线**不能单独打包发布**
- ❌ 改了 router.py 必须重启 API（不像 entry_points 那样 reload）

**代码位置**：`apps/api/app/routers/registry.py:44-66`（loader 实现）。

**反事实**：如果未来业务线要卖给外部公司 → 改 entry_points。当前是内部工具不值得。

---

## 14. 为什么不用 ORM（手写 SQL + SQLAlchemy Core）

**问题**：用 SQLAlchemy ORM / SQLModel / Tortoise / Piccolo / 还是手写 SQL？

**决定**：
- **连接池**用 SQLAlchemy Core（`create_async_engine` + `async_sessionmaker`）
- **查询**全部手写 SQL（`text(...)` + 参数化）
- **结果映射**用 `.mappings().first()` 拿到 dict

**后果**：
- ✅ SQL 可读（grep 就能找）
- ✅ Pydantic schema 直接 `.model_validate(row_dict)` 一步映射
- ✅ DBT 模型可以**生成**这些 SQL（未来可以做）
- ❌ 没有类型提示（手写 SQL 编译时不检查列名）
- ❌ 重构列名要全文搜索

**代码位置**：
- `apps/api/app/db/session.py:1`（engine + session factory）
- `apps/api/app/db/bootstrap.py:1`（DDL 全部手写）
- `apps/api/app/routers/ai_models.py`（CRUD 全手写 SQL）
- `apps/api/app/middleware/audit.py:130-139`（INSERT 手写 SQL）

**反事实**：SQLAlchemy ORM 会给"列名重命名"提供静态保护，但对小团队是 over-engineering。

---

## 15. 为什么 9 条业务线（不是 1 条 "super line"）

**问题**：业务上 9 条线都是"房地产"，能否合并成 1 个大 line？

**决定**：保留 9 条独立。

**理由**：
1. **权限边界**——"住宅 BP"看不到"工业地产 BP"的数据。9 条线 × 不同人 = RBAC 简单
2. **配置独立**——每条线有自己 YAML（`sensitivity.yaml` 系数完全不同），合并会变 1 个超大 YAML
3. **schema 独立**——`raw_residential` / `raw_retail` / `raw_industrial` 物理隔离，DBT 不需要 `WHERE line_id=?` 过滤
4. **节奏独立**——9 条线有不同的 `refresh.schedule`（住宅凌晨 2 点，工业凌晨 4 点）

**后果**：
- ✅ 每条线可以独立迭代（schema 变更不影响其它线）
- ✅ BP 用户看到的是"自己那一亩三分地"
- ❌ 9 份文件改起来有点冗余（用 `_template/` 缓解）
- ❌ 9 套 seed JSON 要维护

**代码位置**：
- `business_lines/registry.yaml:1`（9 条清单）
- `business_lines/<line>/manifest.yaml:30-32`（每个 `warehouse.schema` 独立）
- `apps/api/app/db/seed_users.py:197-206`（每条线 1 个 BP 用户）

**反事实**：如果业务部门真的合并成"地产 BP 中心"，可以合并。当前分权管理是公司政策。

---

## 16. 决策的"将来怎么改"

每个决策记录了"反事实"——这是在说"如果约束变了，决策怎么变"。约束变化时再来
复审这套决策，**不要**预先优化（YAGNI）。

| 如果未来... | 当前决策 | 改成 |
|---|---|---|
| 服务规模到 100+ 实例 | `BIZ_BP_SERVICE_TOKEN` 头 | mTLS / Istio |
| 业务线卖给外部 | `importlib` 加载 | `entry_points` + PyPI |
| DB 泄露合规要求 | Fernet 加密 api_key | HSM / Vault |
| 真实 LLM 必须可用 | fallback to mock | 移除 fallback，返回 503 |
| 业务部门合并 | 9 条业务线 | 合并 + `bp:residential+retail` 多角色 |
| RBAC 复杂化（行 / 列级） | `bp:<line>` 角色字符串 | OPA / Casbin |
