# Operations — 日常运维手册

> 读者：值守 dev stack 或生产 stack 的工程师。
> 配套：[`MAINTENANCE.md`](../../MAINTENANCE.md) §3 快速开始；[`troubleshooting.md`](troubleshooting.md)。

---

## 1. 启动 / 重启服务

### 1.1 本地 dev（无 Docker）

**三个独立服务必须按顺序启动**。

```powershell
# --- 窗口 1：嵌入式 Postgres ---
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
python apps\api\pgserver_runner.py --bg
# 看到 "pgserver ready at 127.0.0.1:11667" 即可

# --- 窗口 2：API（dev 端口 8769） ---
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
$env:PYTHONPATH = "$(pwd)\apps\api"
python -m uvicorn app.main:app --app-dir apps\api --port 8769 --reload

# --- 窗口 3：Web（dev 端口 3000） ---
cd 'C:\Users\mozzi\.mavis\workspace\biz-bp-portal'
npm run web:dev
```

| 服务 | 健康检查 | 期望 |
|---|---|---|
| pgserver | `python apps\api\pgserver_runner.py --status` | `ready=True` |
| API | `curl http://127.0.0.1:8769/healthz` | `{"status":"ok"}` |
| Web | 浏览器打开 <http://localhost:3000> | 重定向到 `/login` |

### 1.2 重启单个服务

| 服务 | 命令 |
|---|---|
| **pgserver** | `python apps\api\pgserver_runner.py --stop` 然后 `--bg` |
| **API** | Ctrl-C uvicorn 进程，再重新启动 |
| **Web** | Ctrl-C `next dev`，再 `npm run web:dev` |

### 1.3 重置 dev 数据库（永久删除数据）

```powershell
python apps\api\pgserver_runner.py --stop
python apps\api\pgserver_runner.py --reset   # 删 .pgdata/，永久丢失
python apps\api\pgserver_runner.py --bg
```

**这会丢失**：
- 所有 9 个 BP 用户 + 1 个 admin
- 所有审计日志
- 所有 ai_models 配置
- 所有 raw.uploads（爬虫 / 上传的历史数据）

**保留**：
- `business_lines/<line>/data/seed/`（业务线 seed JSON，不在 DB 里）
- `data/landing/`（落地区文件，磁盘上）

### 1.4 生产（docker compose）

参见 [`DEPLOY.md`](../../DEPLOY.md) §3.2：

```bash
# 重启单个服务
docker compose -f infra/docker-compose.yml restart api
# 重新构建
docker compose -f infra/docker-compose.yml up -d --build api web
# 全部停止
docker compose -f infra/docker-compose.yml down
# 停止 + 删数据卷（危险）
docker compose -f infra/docker-compose.yml down -v
```

---

## 2. 查看日志

### 2.1 本地 dev

| 服务 | 日志位置 |
|---|---|
| **pgserver** | `<cwd>\.pgdata\postgresql.log`（二进制格式，需要看 stderr 才直观） |
| **API** | stdout（uvicorn `--reload` 模式打 console） |
| **Web** | stdout（`next dev` 打 console） |

API 的关键日志事件：
- `Discovered N scraper(s): ...` — 启动时扫描到几个爬虫
- `Mounted business line 'X' at /api/lines/X` — 业务线 router 挂载
- `seed_initial_users: created ...` — 首次启动的 admin/BP 用户
- `audit_log write failed for ... attempt 1/2, will reset engine and retry` — 审计重试（**正常**）
- `init_db failed at lifespan level (continuing without DB)` — DB 不可达但 API 仍起（**预期**）

### 2.2 生产

```bash
# 所有服务
docker compose -f infra/docker-compose.yml logs -f
# 只看 app
docker compose -f infra/docker-compose.yml logs -f api web
# 只看 1 个
docker compose -f infra/docker-compose.yml logs -f api
# 100 行
docker compose -f infra/docker-compose.yml logs --tail 100 api
```

生产 Postgres 慢查询：
```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U finbp -d finbp -c "SELECT pid, now() - pg_stat_activity.query_start AS duration, query FROM pg_stat_activity WHERE state='active' ORDER BY duration DESC;"
```

---

## 3. 手动运行爬虫

**先登录拿 cookie**：

```powershell
curl -c cookies.txt -X POST http://127.0.0.1:8769/api/auth/login `
  -H "Content-Type: application/json" `
  -d '{"username":"admin","password":"admin123"}'
```

**单跑 1 个爬虫**（admin 权限）：

```powershell
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/scrapers/run/nbs_house_price
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/scrapers/run/lianjia_deals
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/scrapers/run/policy_crawler
```

**一次跑全部**：

```powershell
curl -b cookies.txt -X POST http://127.0.0.1:8769/api/scrapers/run-all
```

**看历史**：

```powershell
curl -b cookies.txt http://127.0.0.1:8769/api/scrapers/history/lianjia_deals?limit=5
```

返回每行的 `run_status`：`ok` / `degraded` / `error`。
- `ok` — 真抓成功
- `degraded` — 上游部分数据回退到 mock（仍然写入 `raw.uploads`）
- `error` — 全部失败

爬虫框架的设计与降级细节见 [`docs/scrapers-deliverable.md`](../scrapers-deliverable.md)。

---

## 4. 添加 / 修改管理员用户

### 4.1 通过管理 UI（推荐）

1. 用 `admin` 登录
2. 顶部菜单 → **用户管理**
3. 点击 **新建用户** → 填用户名 / 显示名 / 邮箱 / 初始密码 / 勾选角色

UI 调用的 API：`POST /api/auth/users`（admin 角色）

### 4.2 通过 SQL（紧急情况）

```sql
-- 1) 创建用户
INSERT INTO users (username, email, password_hash, display_name, is_active)
VALUES (
  'newadmin',
  'newadmin@finbp.local',
  -- bcrypt hash for "ChangeMe123!"（用 python -c "from passlib.hash import bcrypt; print(bcrypt.hash('ChangeMe123!'))" 生成）
  '$2b$12$...',
  'New Admin',
  TRUE
) RETURNING id;

-- 2) 加 admin + auditor 角色
INSERT INTO user_roles (user_id, role) VALUES
  (<id>, 'admin'),
  (<id>, 'auditor')
ON CONFLICT DO NOTHING;

-- 3) （可选）让这个用户能看全部业务线
INSERT INTO user_business_lines (user_id, line_id)
SELECT <id>, line_id FROM business_lines_registry
ON CONFLICT DO NOTHING;
-- 实际上 admin 角色已经能看全部；这步不需要
```

**更简单**：直接走 §4.1。

### 4.3 重置某用户的密码（admin 操作）

```powershell
curl -b admin-cookies.txt -X POST `
  http://127.0.0.1:8769/api/auth/users/<user_id>/reset-password `
  -H "Content-Type: application/json" `
  -d '{"new_password":"NewSecure#2026"}'
```

---

## 5. 轮换 `JWT_SECRET`

`JWT_SECRET`（注意：没有 `BIZ_BP_` 前缀）用于签发 + 验证 JWT。轮换会让所有现有 cookie 失效。

**步骤**：

1. **生成新 secret**（至少 32 字符）：
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. **更新 .env / docker-compose.yml**：
   ```yaml
   # infra/docker-compose.yml
   JWT_SECRET: ${JWT_SECRET:-<paste-new-secret-here>}
   ```
   ```bash
   # .env
   JWT_SECRET=<paste-new-secret-here>
   ```

3. **重启 API**：
   ```bash
   docker compose -f infra/docker-compose.yml up -d --build api
   ```

4. **广播"请重新登录"通告**给所有用户。

5. **不需要** 跑数据库迁移——`JWT_SECRET` 不进 DB。

**反向操作（回滚到旧 secret）**：同样更新 + 重启。

**注意**：`BIZ_BP_AI_SECRET_KEY` 改了会让所有加密的 ai_models.api_key 解不出来。
参见 §7。

---

## 6. 轮换 `BIZ_BP_SERVICE_TOKEN`

`BIZ_BP_SERVICE_TOKEN`（`infra/docker-compose.yml:67`）用于**进程内 mock 引擎**
通过 HTTP 调用 API 自身（参见 `apps/api/app/services/llm/mock_helpers.py:42-44`）。

**轮换步骤**：

1. 生成新 token：
   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. 更新 `infra/docker-compose.yml`（或 `.env`）的 `BIZ_BP_SERVICE_TOKEN:`

3. **同时** 更新所有引用方（mock 引擎直接读环境变量，不进 DB）：
   - `infra/docker-compose.yml` 的 api 服务 environment 块
   - 任何部署侧的 secrets manager

4. **重启 API**。

5. **不需要** 走用户登录流程——这跟 JWT cookie 无关。

---

## 7. 轮换 `BIZ_BP_AI_SECRET_KEY`（Fernet）

`BIZ_BP_AI_SECRET_KEY` 用于加密 `ai_models.api_key` 列。
**改完这个会让所有现有的加密 api_key 都无法解密**——必须配合数据迁移。

**步骤**：

1. **生成新 Fernet key**（必须是 32 字节 URL-safe base64）：
   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. **临时**把 API 设为 dev 模式（不加密）跑一次，把所有密文转回明文：
   ```bash
   # 取消环境变量
   unset BIZ_BP_AI_SECRET_KEY
   # 重启 API
   docker compose -f infra/docker-compose.yml up -d --build api
   ```

3. **用新 key 重启 API**：
   ```bash
   export BIZ_BP_AI_SECRET_KEY=<new-key>
   docker compose -f infra/docker-compose.yml up -d --build api
   ```

4. **让管理员重新输入所有 API key**（管理 UI → AI 模型 → 编辑 → 重新粘贴 key）。
   这是因为旧的密文已经无法用新 key 解密——`apps/api/app/core/secret.py:114-147`
   对无效密文返回 `None`，触发 `is_active` 自动失效。

5. **保留旧 key 一段时间**（例如 7 天），以防有回滚需求。

**或者**：写一个 migration 脚本，循环 `ai_models.api_key`，旧 key 解密 → 新 key 加密。
**当前项目没有这个脚本**——记录在 `apps/api/app/core/secret.py:27-29` 的"intentionally tiny"注释里。

---

## 8. 查 / 备份 / 还原 Postgres

### 8.1 查表

```powershell
# 嵌入式 dev pgserver
psql -h 127.0.0.1 -p 11667 -U finbp -d finbp
# 或 docker compose 内
docker compose -f infra/docker-compose.yml exec postgres psql -U finbp -d finbp
```

### 8.2 查审计日志

```sql
-- 最近 50 条
SELECT id, "timestamp", user_id, username, method, path, status_code, duration_ms
FROM raw.audit_log
ORDER BY "timestamp" DESC
LIMIT 50;

-- 某个用户的活动
SELECT "timestamp", method, path, status_code
FROM raw.audit_log
WHERE username = 'admin'
ORDER BY "timestamp" DESC
LIMIT 100;

-- 404 错误
SELECT "timestamp", path, COUNT(*) AS hits
FROM raw.audit_log
WHERE status_code >= 400
GROUP BY "timestamp", path
ORDER BY "timestamp" DESC
LIMIT 20;
```

### 8.3 备份（dev 嵌入式）

```powershell
# 用 pg_dump 导出（生产 compose 已装 postgres-client）
pg_dump -h 127.0.0.1 -p 11667 -U finbp -d finbp -Fc -f backup_2026-09-03.dump
```

### 8.4 备份（生产 compose）

```bash
docker compose -f infra/docker-compose.yml exec -T postgres \
  pg_dump -U finbp -d finbp -Fc > backup_$(date +%Y%m%d).dump
```

### 8.5 还原

```bash
# 先停 API（避免写入冲突）
docker compose -f infra/docker-compose.yml stop api web

# 还原
cat backup_20260903.dump | docker compose -f infra/docker-compose.yml exec -T postgres \
  pg_restore -U finbp -d finbp --clean --if-exists

# 启动
docker compose -f infra/docker-compose.yml start api web
```

### 8.6 嵌入式 pgserver 的"备份"

`pg_dump` 可用（见上），但**更简单**是直接复制 `.pgdata/` 目录
**前提是 Postgres 已 stop**：

```powershell
python apps\api\pgserver_runner.py --stop
# 复制
Copy-Item .pgdata .pgdata.backup_20260903 -Recurse
python apps\api\pgserver_runner.py --bg
```

不要在 pgserver 运行时复制 `.pgdata/`——会得到损坏的快照。

---

## 9. 清理审计日志（保留策略）

`raw.audit_log` 表无限增长。生产环境**必须**设保留策略。

**手动清理**：

```sql
DELETE FROM raw.audit_log
WHERE "timestamp" < NOW() - INTERVAL '90 days';
```

**自动化**：在 Airflow 加一个 cron（参见 `infra/airflow/dags/ingest_daily.py`）：

```python
from datetime import datetime, timedelta
from airflow.operators.python import PythonOperator

def prune_audit_log():
    cutoff = datetime.utcnow() - timedelta(days=90)
    # 通过 psycopg2 删
    ...
```

**当前项目没有这个 DAG**——记录在 `DEPLOY.md:87` 提示里。

---

## 10. 嵌入式 pgserver 完整操作

`apps/api/pgserver_runner.py` 是本地 dev 的 PG 控制脚本。

```powershell
# 查看状态
python apps\api\pgserver_runner.py --status
# 启动（前台 + Ctrl-C 停止）
python apps\api\pgserver_runner.py
# 后台启动（用于 IDE 调试）
python apps\api\pgserver_runner.py --bg
# 停止
python apps\api\pgserver_runner.py --stop
# 重置（删 .pgdata/）
python apps\api\pgserver_runner.py --reset
```

**端口冲突**：默认 11667。改 `BIZ_BP_PGPORT` 环境变量。

**locale 强制为 C**（脚本开头 `os.environ.setdefault("LANG", "C")`），
防止中文 Windows 环境的 initdb 失败。**不要**去掉这一行。

**杀残留进程**（Windows 专用）：脚本在 `--reset` 时调 `taskkill /F /IM postgres.exe`
杀掉所有 `postgres.exe` 进程。**仅在 dev 用**——生产用 compose 的 stop。

---

## 11. 完整的"零到 hero"dev 重置

如果整个 dev 环境被搞坏：

```powershell
# 1. 杀掉所有相关进程
Get-Process -Name "next-server", "uvicorn", "python" -ErrorAction SilentlyContinue | Stop-Process -Force
Get-Process -Name "postgres" -ErrorAction SilentlyContinue | Stop-Process -Force

# 2. 删除 .next（强制重新构建）
Remove-Item -Recurse -Force apps\web\.next -ErrorAction SilentlyContinue
# 或用 python 删（更安全）
py -X utf8 -c "import shutil; shutil.rmtree(r'C:\...\apps\web\.next', ignore_errors=True)"

# 3. 重置 DB
python apps\api\pgserver_runner.py --stop
python apps\api\pgserver_runner.py --reset
python apps\api\pgserver_runner.py --bg

# 4. 重新安装依赖（如果也坏了）
#   pip install -e ".[dev]" in apps\api\
#   npm install in root

# 5. 按 §1.1 顺序启动 3 个服务
```

---

## 12. 监控 / 告警（生产建议）

当前项目**没有**内置监控。生产部署建议接 Prometheus / Grafana：

| 指标 | 采集点 |
|---|---|
| API 响应时间 | uvicorn 中间件 → Prometheus |
| 审计日志写入失败率 | `_write_audit_row` 的 WARNING log → ELK |
| pgserver 连接数 | `pg_stat_activity` |
| LLM API 错误率 | `copilot_engine` 的 `used_fallback=true` 计数 |
| 爬虫 degraded 比例 | `raw.uploads.run_status = 'degraded'` 计数 |

详细的可观测性建议见 `DEPLOY.md §5`（生产环境加固）。
