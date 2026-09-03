# Troubleshooting — 故障排查手册

> 读者：在生产或本地 dev 遇到 bug 的工程师。
> 配套：[`MAINTENANCE.md`](../../MAINTENANCE.md) §8；[`operations.md`](operations.md)。

按症状 → 诊断 → 修法 组织。

---

## 0. 排查流程

```powershell
# 1. 看 API 健康
curl http://127.0.0.1:8769/healthz

# 2. 看 pgserver
python apps\api\pgserver_runner.py --status

# 3. 看最近 50 条审计日志（看错误码分布）
psql -h 127.0.0.1 -p 11667 -U finbp -d finbp -c "
  SELECT status_code, COUNT(*)
  FROM raw.audit_log
  WHERE \"timestamp\" > NOW() - INTERVAL '10 minutes'
  GROUP BY status_code
  ORDER BY status_code;
"

# 4. 看 API 启动 log（uvicorn 进程 console）
#    重点找 "WARNING" / "ERROR" / "init_db failed"
```

---

## 1. "AI Model 404 from DeepSeek" — api_key 列值错乱

**症状**：管理后台配置好 DeepSeek 模型，"测试" 按钮报 404 或 "Invalid token"，
但 DeepSeek 官方 API 状态正常。

**根因**：`ai_models.api_key` 列里存了**错误的**值，常见情况：
1. 误填了 `env:DEEPSEEK_API_KEY` 但生产环境没设该环境变量
2. 复制粘贴时多了空格 / 换行
3. dev 模式（`BIZ_BP_AI_SECRET_KEY` 未设）下历史存了 `plain:sk-xxx` 密文
   —— 后来开启 Fernet 加密后无法解密
4. Fernet key 轮换后未迁移（参见 [`operations.md`](operations.md) §7）

**诊断**：

```sql
SELECT id, name, provider,
  CASE
    WHEN api_key LIKE 'env:%' THEN 'env-ref'
    WHEN api_key LIKE 'plain:%' THEN 'plaintext-fallback'
    WHEN api_key LIKE 'gAAAAA%' THEN 'fernet-ciphertext'
    ELSE 'unknown-format'
  END AS key_format,
  LENGTH(api_key) AS key_len
FROM ai_models
WHERE provider = 'deepseek';
```

**修法**：

1. 用管理 UI 重新粘贴 key（**不要**改后端 SQL）
2. 确认 `BIZ_BP_AI_SECRET_KEY` 在所有实例上一致
3. 如果是 env 引用，确认目标环境变量已设
4. 测试按钮应该返回 `ok=true`

**参考**：`apps/api/app/core/secret.py:99-147`（三种格式处理）。

---

## 2. "Copilot 503 upstream" — pgserver 死了

**症状**：前端问"XX 项目 IRR 多少"，弹"上游不可达"（HTTP 502/503）。
但 /api/registry/lines /api/auth/me 都正常。

**根因**：Copilot 引擎内部走 HTTP 调 `/api/lines/<line>/...`（参见
`apps/api/app/services/llm/mock_helpers.py:42-44`）。如果 pgserver 死了，
这些内部调用就 503。

**诊断**：

```powershell
python apps\api\pgserver_runner.py --status
# 如果 ready=False → pgserver 死了
```

**修法**：

```powershell
python apps\api\pgserver_runner.py --stop
python apps\api\pgserver_runner.py --bg
```

不需要重启 API——`pgserver_runner.py --bg` 启动后，asyncpg 连接池会在下次请求时
自动重连（参见 `apps/api/app/db/session.py:48` 的 `pool_pre_ping=True`）。

如果重连后 Copilot 仍然 503，看 audit log：
```sql
SELECT path, status_code, COUNT(*)
FROM raw.audit_log
WHERE path LIKE '/api/lines/%'
  AND "timestamp" > NOW() - INTERVAL '5 minutes'
GROUP BY path, status_code
ORDER BY COUNT(*) DESC;
```

---

## 3. "Audit middleware 静默失败" — DB 连接池陈旧

**症状**：API log 里出现
```
audit_log write failed for GET /api/.../something (attempt 1/2, will reset engine and retry): ...
```
紧跟 `attempt 2/2` 的 `WARNING`。

**这是设计**——不是 bug。

**机制**（`apps/api/app/middleware/audit.py:140-175`）：

1. asyncpg 缓存的连接池可能持有"已关闭"的连接
2. 第一次写失败 → 调 `_db_session.reset_engine()` 丢掉旧池
3. 用新池重试一次
4. 二次失败仅 WARNING log，**绝不抛异常**

**何时该担心**：如果 `attempt 1/2` 警告**持续 5 分钟以上**，
那是 Postgres 整体不可用，应用层会先报错（不是审计）——这才是真问题。

**永远不要**：
- 把这个重试逻辑去掉（DB 临时抖动会阻塞响应）
- 把 `WARNING` 升成 `ERROR`（会触发 Sentry 误报）

---

## 4. "Scrapers show degraded=true for hours" — 上游站点变了

**症状**：爬虫面板 3 个爬虫全部 `run_status=degraded`，持续几个小时。

**根因**：
- 链家 / 住建部改了 HTML 结构（CSS selector 失配）
- 上游站点启用了反爬（IP 黑名单 / captcha）
- 上游站点暂时不可达

**诊断**：

```powershell
# 单跑看具体错误
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/scrapers/run/lianjia_deals
# 看响应里的 degraded 原因
```

**修法**：

1. 短期内：等上游恢复；或者把对应爬虫 `enabled=false`（admin UI）
2. 长期：更新 `apps/api/app/services/scrapers/scrapers/<source>.py` 的解析规则
3. 提交 git commit 引用 `bb3dc05`（real-data refresh commit）作为参考

**参考**：[`docs/scrapers-deliverable.md`](../scrapers-deliverable.md) — 含 3 个真实源的解析策略与降级链。

---

## 5. "401 on BFF calls" — 跨主机 cookie 丢失

**症状**：本地 dev（API:8769 + Web:3000 同主机）一切正常；
切到生产（API:8000 在另一台机器）后所有 BFF 调用 401。

**根因**：现代浏览器**不再发送**第三方 cookie。生产部署 API 与 Web 分主机
时，浏览器到 API 的 cookie 被浏览器拦截，BFF 拿到的是空 cookie。

**修法**（BFF 必须转发 cookie）：

```typescript
// apps/web/app/api/<feature>/route.ts
const upstream = await fetch(url, {
  method,
  headers: {
    cookie: request.headers.get("cookie") ?? "",  // ← 这行必须有
  },
  // ...
});
```

参考：`apps/web/app/api/ai-models/[[...path]]/route.ts:36-43`。

**如果已经写了转发**：检查 Next.js 14 build 后的产物里 `request.headers.get("cookie")`
返回的不是空字符串（用 `console.log` 调试）。

**部署侧的额外配置**：API 与 Web 必须共享 cookie domain（`Domain=portal.example.com`），
或者用反代让两者看起来同源。

---

## 6. "STALE: git status shows biz-bp-portal as untracked" — reparse-point 假象

**症状**：
```
$ git status
Untracked files:
  biz-bp-portal/
```

`biz-bp-portal/` 是一个目录，名字跟当前仓库**一模一样**——指向自己。

**根因**：`C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 是 Windows
**reparse-point**（符号链接），真实路径在 `C:\Users\mozzi\.minimax\workspace\biz-bp-portal`。
Git 偶尔把 reparse-point 自身当成 untracked 目录。

**修法**：

```powershell
# 选项 1：忽略（推荐）
#    啥都不做，git 不会把它当真的 untracked（除了这条 status 消息）
#    提交时不会被卷入

# 选项 2：在真实路径下工作
cd 'C:\Users\mozzi\.minimax\workspace\biz-bp-portal'
# 此处 git status 完全正常
```

**永远不要**：
- `rm -rf biz-bp-portal`（会删真实目录）
- `mv biz-bp-portal X`（会破坏 symlink 链）
- 改 `.gitignore` 加 `biz-bp-portal/`（会掩盖真问题）

**参考**：commit `cf2d8f1`（修复 ctypes+kernel32 workaround）。

---

## 7. "Pydantic EmailStr rejects empty string" — 用 clear_email 标志

**症状**：用户编辑资料想"清空邮箱"，前端发 `email: ""`，后端 422。

**根因**：Pydantic `EmailStr`（来自 `pydantic[email]`）把 `""` 视为非法格式。

**修法**（已采用）：

```python
# Schema（apps/api/app/schemas/auth.py）
class UpdateUserRequest(BaseModel):
    email: Optional[EmailStr] = None   # 仍然校验
    clear_email: bool = False           # ← 显式清空标志

# Router
if payload.clear_email:
    await session.execute(text("UPDATE users SET email = NULL WHERE id = :id"), {"id": user_id})
elif payload.email is not None:
    await session.execute(text("UPDATE users SET email = :e WHERE id = :id"), {"e": payload.email}, {"id": user_id})
```

前端发送逻辑：
- 用户**清空**邮箱 → 发 `{clear_email: true}`
- 用户**设置**新邮箱 → 发 `{email: "new@x.com"}`

**不要**发 `{email: ""}` 触发后端 422。

---

## 8. "AI model api_key clear" — 空字符串翻译成 NULL

**症状**：用户点管理 UI 的"清空"链接，前端发 `api_key: ""`，后端应该把数据库
里这行的 `api_key` 设为 NULL。

**机制**（`apps/api/app/routers/ai_models.py`）：

```python
# "" → NULL（"显式清空" 约定）
# None → 不动（"未传" 约定）
if payload.api_key == "":
    await session.execute(text("UPDATE ai_models SET api_key = NULL WHERE id = :id"), {"id": id})
elif payload.api_key is not None:
    encrypted = encrypt_secret(payload.api_key)
    await session.execute(text("UPDATE ai_models SET api_key = :k WHERE id = :id"), {"id": id, "k": encrypted})
```

**前端约定**：UI 永远不发 `null`，只发 `""` 或实际值。

**诊断**（如果 api_key 仍然有值）：

```sql
SELECT id, name, LENGTH(api_key) AS key_len, LEFT(api_key, 8) AS prefix
FROM ai_models
WHERE provider = 'deepseek';
```

如果 `key_len > 0` → 后端没收到 `""`（前端 bug）
如果 `key_len IS NULL` → 后端清了，但 UI 还在显示旧值（前端缓存问题）

---

## 9. "The 2 always-stale background tasks in system reminders"

**症状**：Mavis / Claude Code 偶尔在 `<system-reminder>` 里显示：
- "Task X is still running"
- "Task Y never completed"

这些 task 名字看起来**与当前任务无关**（比如 `python pgserver_runner.py --bg` /
`git fetch` / 之前的爬虫 run）。

**修法**：**直接忽略**。这些 task 来自**之前**的 session / 之前的工作。
当前 session 重启时它们会自然清理。

**不要**：
- 试图 cancel 它们
- 把它们当"真问题"报 bug
- 在回复里"follow up" 它们

**如果是当前 session 启的 task**（名字在 5 分钟内的命令）——
才需要排查。

---

## 10. "gh push warns 'repository moved'" — 大小写重定向

**症状**：
```
remote: Repository not found.
fatal: repository 'https://github.com/njryadmin/biz-bp-portal.git/' not found
```
或
```
warning: redirecting to https://github.com/Njryadmin/biz-bp-portal.git/
```

**根因**：HTTPS URL 的大小写不一致。GitHub 会自动重定向，但首次 push 会警告。

**修法**：**无害**。后续 push 会用正确 URL。不想看警告就把 origin URL 改成正确大小写：

```bash
git remote set-url origin https://github.com/Njryadmin/biz-bp-portal.git
```

---

## 11. "The reparse-point breaks if you rename the on-disk directory"

**症状**：尝试 `mv C:\Users\mozzi\.minimax\workspace\biz-bp-portal` 到别的名字，
之后 `C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 失效。

**根因**：`C:\Users\mozzi\.mavis\workspace\biz-bp-portal` 是 hard-coded reparse-point
指向 `biz-bp-portal` 目录。改名会断链。

**修法**：**不要重命名**。目录名 `biz-bp-portal` 是固定契约。

如果必须改（极不建议）：
1. 关闭所有引用该路径的 IDE / shell
2. 删除 reparse-point：`fsutil reparsepoint delete C:\Users\mozzi\.mavis\workspace\biz-bp-portal`
3. 移动目录
4. 重新创建 reparse-point：`mklink /J ...`
5. 重新跑 `git status` 验证

更安全的做法：在 `package.json` / `pyproject.toml` 的 metadata 里改 name
（"Biz-BP Portal"），但**不动**磁盘目录。

**参考**：commit `cf2d8f1`。

---

## 12. "bcrypt version conflict" — passlib 不兼容 bcrypt 5.x

**症状**：
```
ValueError: password cannot be longer than 72 bytes
```
或
```
AttributeError: module 'bcrypt' has no attribute 'hashpw'
```

**根因**：`passlib 1.7.4` 内部假设 `bcrypt<5`。任何 `pip install -U bcrypt` 会装 5.x，
破坏 `hash_password` / `verify_password`。

**修法**：

```bash
pip install 'bcrypt<5'
```

`pyproject.toml` 已经固定 `bcrypt<5`，**不要**改。

---

## 13. "API 启动卡在 'Loading business line routers...'" — 某个业务线 import 死锁

**症状**：uvicorn 启动 1 分钟后还在 `Loading business line routers...`，
最后 timeout 退出。

**根因**：某个业务线 `api/router.py` 里有**顶层 import 阻塞**（比如 import 了
没启的 Redis client / 没启的外部服务）。

**诊断**：

```powershell
# 单独 import 看哪个卡
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
$env:PYTHONPATH = "apps\api"
python -c "
import sys
sys.path.insert(0, 'business_lines/residential')
import importlib.util
spec = importlib.util.spec_from_file_location('test', 'business_lines/residential/api/router.py')
m = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(m)
    print('OK')
except Exception as e:
    print(f'FAIL: {e}')
"
```

**修法**：

业务线 router 的**顶层**只放轻量 import（`fastapi` / `pydantic`）。
重资源（DB client / HTTP client）放 **lazy import**（函数内 import） 或 **lifespan**。

参考 `business_lines/residential/api/router.py` 的实现。

---

## 14. "Lifespan 'Mounted business line X' 之后 init_db failed" — DB 不可达

**症状**：API 启动 log：
```
Mounted business line residential
init_db failed at lifespan level (continuing without DB): ...
Application startup complete
```

**这是设计**——**不是 bug**。

**机制**（`apps/api/app/main.py:51-68`）：

- DB 不可达时 `init_db` 抛异常
- lifespan 捕获，WARNING log，**继续启动**
- API 起来了，但任何 DB 操作会 500

**何时该担心**：

- API 启动后所有 DB 操作（登录、查业务线）都 500——确认 pgserver 状态
- 之后 pgserver 起来了，下一个请求会**自动**重连（`pool_pre_ping=True`）

**修法**：

```powershell
python apps\api\pgserver_runner.py --status
# 如果 ready=False
python apps\api\pgserver_runner.py --start
# 不用重启 API
```

---

## 15. "Admin 用户被锁在登录页" — JWT secret 漂移

**症状**：明明密码正确，登录后立即被踢回 `/login`。

**根因**：`JWT_SECRET` 在 Web / API / 不同部署实例间不一致。
登录的 API 签发了 secret=A 的 token，但 `/api/auth/me` 用的 secret=B，验证失败。

**诊断**：

```powershell
# 查所有实例的 JWT_SECRET
# 1. .env
Get-Content .env | Select-String "JWT_SECRET"
# 2. docker-compose.yml
Select-String "JWT_SECRET" infra\docker-compose.yml
# 3. k8s secrets / Vault
kubectl get secret -o yaml | Select-String "jwt"
```

**修法**：

让所有实例的 `JWT_SECRET` 完全一致（字符串级）。参见 [`operations.md`](operations.md) §5
的轮换流程。

---

## 16. "敏感性 / 预测 / 告警 4 个引擎数量不匹配" — 某个 YAML 缺了

**症状**：
```
GET /api/sensitivity/profiles → 9 个
GET /api/forecast/profiles    → 9 个
GET /api/alerts/profiles      → 8 个    ← 少一个
```

**根因**：某条业务线没写对应 YAML。

**诊断**：

```powershell
Get-ChildItem business_lines\*\*.yaml -Recurse | Group-Object Name | Sort-Object Count
```

**修法**：

1. 找到没写的 line
2. 复制 `business_lines/residential/<engine>.yaml` 改 `line_id` / `line_name` / 字段名
3. 重启 API

**或者**反过来：写了 YAML 但 line id 与 manifest 不一致——
`registry.py` 在 `apps/api/app/core/registry.py:204-207` 抛 ValueError，API 启动会失败。

---

## 17. "PowerShell CJK 文件被破坏" — 编码陷阱

**症状**：编辑某个含中文的 `.py` / `.md` 后，文件里出现 `?` / 乱码。

**根因**：PowerShell 5.1 默认 GBK 编码。`Get-Content` / `Set-Content` 不带 `-Encoding UTF8`
会把 UTF-8 文件当 ANSI 读，写回去时破坏多字节字符。

**修法**：

```powershell
# 读
Get-Content -Raw -Encoding UTF8 path\to\file.py
# 写
$content | Set-Content -NoNewline -Encoding UTF8 path\to\file.py
```

或者**用 Python**（更安全）：

```powershell
py -X utf8 -c "import io; print(open(r'path', encoding='utf-8').read())"
```

**永远不要** `Get-Content | Set-Content` pipeline（会破坏）。

---

## 18. "前端的角色切换不生效" — RoleSwitcher 改成了 read-only

**症状**：admin 看不到 role 切换 dropdown，只有彩色 tag。

**这是设计变更**（`packages/ui/src/RoleSwitcher.tsx` 改成 read-only）。

**原来的语义**：快速切换当前用户的角色来测试不同权限视图。
**新语义**：当前用户的真实角色展示，不可改。要改角色走管理后台（admin → 用户管理 → 编辑）。

**如果真的需要"快速试角色"**：用管理后台给测试账号分配不同角色，登录那个账号。

---

## 19. "Scraper BFF 报 404 / 500" — 旧 BFF 路由没适配新结构

**症状**：admin 调 `/api/scrapers/run/<id>` 报 500 或 404。

**根因**：之前 BFF 把 `<id>` 当 query 拼到 URL 里，**没**用 dynamic route
参数化（参见 commit `364e1f7` 修复）。

**修法**：

1. 检查 `apps/web/app/api/scrapers/run/[source_id]/route.ts` 是否存在
2. 确认 `buildUrl` 用 `params.source_id` 而不是 `request.nextUrl.searchParams.get("source_id")`
3. 重启 web

参考 `apps/web/app/api/scrapers/run-all/route.ts`（同样模式）。

---

## 20. "审计日志没记录某些请求" — skip prefix / exact 路径

**症状**：某个高频端点（如 `/api/copilot/health`）在 `raw.audit_log` 里没记录。

**机制**（`apps/api/app/middleware/audit.py:45-60`）：

```python
_AUDIT_SKIP_PREFIXES = ("/healthz", "/")
_AUDIT_SKIP_EXACT = frozenset({"/api/auth/login"})
```

**修法**：

- 故意跳过的：不要动
- 误跳的：编辑 `audit.py` 的 skip 列表，重启 API
- 登录 body 永不审计（防密码泄露）—— 永远是 skip

---

## 21. 紧急情况

| 情况 | 操作 |
|---|---|
| 生产 API 全 500 | 看 uvicorn log；如果是 DB 问题，重启 pgserver / compose postgres |
| 生产 Web 502 | 看 Next.js log；检查 `NEXT_PUBLIC_API_BASE_URL` |
| 误推了机密 | 立刻轮换所有 `*_SECRET*` / `*_KEY` / `*_TOKEN`；检查 git history |
| 业务线 id 被改了 | 立刻回滚（cookie 里的 `bp:<id>` 角色会全部失效） |
| 数据库被误删 | 从最近的 `pg_dump` 还原（参见 [`operations.md`](operations.md) §8.4） |
