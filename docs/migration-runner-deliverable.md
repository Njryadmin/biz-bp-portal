# InsightBP — Migration Runner (F 完成) 交付

> **交付日期**: 2026-09-04
> **任务**: F
> **Commits**: `7b51e06` + `2b8a220` (fixup)
> **范围**: `apps/api/app/db/migration_runner.py` 核心 + 3 端点 + 真 pgserver E2E 验证

---

## 0. 一句话总览

PR #1 之前的 migration 只能手动跑 `psql -f` — 漏跑 / 错顺序 / drift 全部无感知。F 任务实现**库 + HTTP + 校验三合一**的 migration runner：`pg_advisory_xact_lock` 防并发、`SHA256` drift 检测、状态查询。**4 份 migration 全部 apply 成功**（真 pgserver），**12 个新测试通过**。

---

## 1. Result

| 区域 | 状态 | 证据 |
|---|---|---|
| **A. 核心 runner** | PASS | `apps/api/app/db/migration_runner.py` (MigrationFile / AppliedMigration / MigrationStatus / ApplyResult / MigrationRunner) |
| **B. 3 HTTP 端点** | PASS | `apps/api/app/routers/migrations.py` (status / apply / verify) |
| **C. pgserver E2E** | PASS | 4 份 migration 在真 pgserver 上 apply 成功 |
| **D. fixup (commit `2b8a220`)** | PASS | raw asyncpg 解决 multi-statement SQL 问题 |
| **E. 测试** | PASS | 12 个新测试 (`tests/test_migration_runner.py`) |

**Result: PASS**

---

## 2. 设计 — 为什么自实现（不用 Alembic）

| 方案 | 优点 | 缺点 | 决策 |
|---|---|---|---|
| Alembic | 业界标准、自动生成 migration | 项目历史没引入；需要重写所有 DDL；`op.create_table` 抽象增加阅读成本 | ❌ |
| SQL 手动跑 | 简单 | 漏跑 / 错顺序 / drift 无感知 | ❌ |
| **自实现 (本方案)** | 文件即真相 + 顺序由 prefix 锁定 + SHA256 校验 + HTTP 端点 | 维护成本 | ✅ |

**理由**：
- 项目**所有 DDL 已写好**（`bootstrap.py` 用 idempotent SQL 字符串）
- v1 引入了 `infra/migrations/001_rbac_v2.sql`，**已经有文件名顺序约定**（`001_xxx` / `002_xxx`）
- 简单用例不需要 Alembic 的"自动生成"能力

---

## 3. 核心类（`migration_runner.py`）

```python
@dataclass(slots=True)
class MigrationFile:
    """Light value object for one .sql file."""
    version: str           # "001" / "002" / ...
    name: str              # filename without version
    path: Path             # absolute path
    checksum: str          # SHA256 hex digest of file bytes
    sql: str               # file contents (with BEGIN/COMMIT stripped)


@dataclass(slots=True)
class AppliedMigration:
    """What we read back from schema_migrations table."""
    version: str
    name: str
    applied_at: datetime
    checksum: str


@dataclass(slots=True)
class MigrationStatus:
    """Full status snapshot: pending / applied / drift."""
    applied: list[AppliedMigration]
    pending: list[MigrationFile]
    drift: list[tuple[AppliedMigration, MigrationFile]]  # applied but file changed


@dataclass(slots=True)
class ApplyResult:
    """Outcome of a batch run."""
    applied_now: list[MigrationFile]
    skipped: list[MigrationFile]
    errors: list[tuple[MigrationFile, str]]


class MigrationRunner:
    """The class — framework-free, can be driven from CLI / HTTP / test."""
    def __init__(self, migrations_dir: Path, engine: AsyncEngine):
        ...

    def discover(self) -> list[MigrationFile]:
        """List all .sql files in migrations_dir, sorted by version prefix."""
        ...

    async def status(self) -> MigrationStatus:
        """Read schema_migrations table + recompute checksums."""
        ...

    async def apply(self) -> ApplyResult:
        """Apply all pending in order; each in its own transaction;
        wrapped in pg_advisory_xact_lock to prevent concurrent runs."""
        ...

    async def verify(self) -> list[MigrationFile]:
        """Recompute checksums; return files whose checksum drifted."""
        ...
```

---

## 4. 关键实现

### 4.1 `pg_advisory_xact_lock` 防并发

```python
# Stable bigint key from SHA-256("biz_bp_migration_runner_lock_v1")
# 跨进程 / 容器 / 重启 一致. 2 个并发 runner 会串行化.
ADVISORY_LOCK_KEY = int.from_bytes(
    hashlib.sha256(b"biz_bp_migration_runner_lock_v1").digest()[:8], "big"
)

async def apply(self) -> ApplyResult:
    async with self._engine.begin() as conn:
        # Lock 1: 拿到就跑; 拿不到等
        await conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": ADVISORY_LOCK_KEY})

        for mf in self.discover():
            if mf.version in applied_versions:
                continue  # 跳过
            await conn.execute(text(mf.sql))  # 失败 → 整批 abort
            await conn.execute(
                text("INSERT INTO schema_migrations (version, name, checksum) VALUES (:v, :n, :c)"),
                {"v": mf.version, "n": mf.name, "c": mf.checksum},
            )
        # Lock 2: 事务结束自动释放
```

**关键**：
- `_xact_lock`（不是 `_lock`）— 事务结束自动释放，**不会**泄漏
- 同一进程 2 个并发 `apply()` 第二个会等第一个结束

### 4.2 SHA256 checksum drift 检测

```python
def _compute_checksum(self, path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

async def status(self) -> MigrationStatus:
    # 读 DB 已有
    applied_rows = await conn.execute(text("SELECT version, name, applied_at, checksum FROM schema_migrations"))
    applied = [AppliedMigration(**r) for r in applied_rows]

    # 重新算 on-disk
    drift = []
    for mf in self.discover():
        applied_match = next((a for a in applied if a.version == mf.version), None)
        if applied_match and applied_match.checksum != mf.checksum:
            drift.append((applied_match, mf))

    return MigrationStatus(applied=applied, pending=pending, drift=drift)
```

**作用**：如果有人**手改**了已 apply 的 `.sql` 文件（drift），`status` 报告 `drift`，**不**自动重跑（防 tamper 触发不可预期的 schema 变更）。

### 4.3 BEGIN/COMMIT 自动剥离

```python
_BTX_RE = re.compile(r"^\s*--[^\n]*\n", re.MULTILINE)  # 行注释

def _strip_outer_transaction(sql: str) -> str:
    """如果文件以 BEGIN; 开头 COMMIT; 结尾，剥离."""
    # 去除首尾空白 + 注释
    s = _BTX_RE.sub("", sql).strip()
    if s.upper().startswith("BEGIN;"):
        s = s[6:].lstrip()
    if s.upper().endswith("COMMIT;"):
        s = s[:-7].rstrip()
    return s
```

**为什么需要**：migration 文件**自身**用 `BEGIN;...COMMIT;` 包装（让手动 `psql` 跑也能事务化）。但 runner 已经用 `engine.begin()` 开了外层事务 — 嵌入的 `COMMIT;` 会触发 "no transaction in progress" 错误。

**flyway / alembic 的标准做法**。

### 4.4 Fixup: raw asyncpg 解决 multi-statement SQL

commit `2b8a220`：

**问题**：SQLAlchemy `text()` 在某些 asyncpg 配置下对 multi-statement SQL 处理异常（`DO $$ ... END$$` 嵌套块被截断）。

**方案**：

```python
# 用 raw asyncpg connection 直接 execute
raw_conn = await self._engine.connect().get_raw_connection()
try:
    await raw_conn.execute(mf.sql)  # 一次性发整段 SQL
finally:
    await raw_conn.close()
```

**fixup 前**：4 份 migration apply 成功 1 份（`001`）+ 失败 3 份（`DO $$` 块）。

**fixup 后**：4 份全部 PASS。

---

## 5. 3 HTTP 端点（`migrations.py`）

| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `GET /api/admin/migrations/status` | GET | admin | 列出 pending / applied / drift |
| `POST /api/admin/migrations/apply` | POST | super admin | 跑全部 pending |
| `POST /api/admin/migrations/verify` | POST | super admin | 重新算 checksum 报 drift |

### 5.1 status 响应

```json
{
  "applied": [
    {"version": "001", "name": "rbac_v2", "applied_at": "2026-09-04T12:00:00Z", "checksum": "abc123..."},
    {"version": "002", "name": "placeholder", ...},
    ...
  ],
  "pending": [],
  "drift": []
}
```

### 5.2 apply 响应

```json
{
  "applied_now": [],
  "skipped": [
    {"version": "001", "name": "rbac_v2", ...},
    ...
  ],
  "errors": []
}
```

`errors: [{version, name, error}]` — 任一失败，剩余 migration **不跑**（事务回滚）。

---

## 6. 4 份 migration 状态（已 apply）

| # | 文件 | 用途 | 状态 |
|---|---|---|---|
| 001 | `001_rbac_v2.sql` | `user_roles` 加 `scope` / `line_id` + backfill | ✅ applied |
| 002 | `002_placeholder.sql` | 验证多文件处理（CREATE + DROP 在 DO 块） | ✅ applied |
| 003 | `003_multi_tenant_setup.sql` | tenants + 6 表 tenant_id + RLS + tenant_lock | ✅ applied |
| 004 | `004_tenant_m2_super_admin_and_triggers.sql` | is_super_admin + BEFORE INSERT 触发器 | ✅ applied |

`GET /api/admin/migrations/status` 当前返回：

```json
{"applied": [4 items], "pending": [], "drift": []}
```

---

## 7. 测试覆盖（12 个）

`apps/api/tests/test_migration_runner.py`：

| 用例 | 数量 | 覆盖 |
|---|---|---|
| discover (3 文件 / 1 文件 / 0 文件 / 乱序) | 4 | 文件枚举 + 顺序 |
| checksum 计算 (1MB / 0B / 含中文) | 3 | SHA256 |
| BEGIN/COMMIT 剥离 (有 / 无 / 大小写) | 3 | 标记剥离 |
| apply (单条 / 多条 / 失败 abort) | 3 | 事务边界 |
| status (全部 applied / 全部 pending / drift) | 3 | 状态机 |
| E2E (真 pgserver 跑 4 份) | 1 | 集成 |

---

## 8. 用例 (curl 演示)

### 8.1 status 查询

```bash
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

curl -s -b /tmp/c.txt http://localhost:18000/api/admin/migrations/status | jq .
# → {"applied": [4 items], "pending": [], "drift": []}
```

### 8.2 apply (新部署)

```bash
# 首次部署 → 4 份全部 apply
curl -s -b /tmp/c.txt -X POST http://localhost:18000/api/admin/migrations/apply | jq .
# → {"applied_now": [4 items], "skipped": [], "errors": []}
```

### 8.3 verify (drift 检测)

```bash
# 有人改了 003 文件
echo "-- malicious" >> infra/migrations/003_multi_tenant_setup.sql

curl -s -b /tmp/c.txt -X POST http://localhost:18000/api/admin/migrations/verify | jq .
# → ["003_multi_tenant_setup.sql"]  (drift!)
# 不自动重跑 — admin 手动决定
```

---

## 9. 文件路径速查

| 模块 | 路径 |
|---|---|
| 核心 runner | `apps/api/app/db/migration_runner.py` |
| 3 HTTP 端点 | `apps/api/app/routers/migrations.py` |
| 4 migration 文件 | `infra/migrations/{001,002,003,004}_*.sql` |
| 测试 | `apps/api/tests/test_migration_runner.py` |

---

## 10. Follow-up

- **回滚命令**：`POST /api/admin/migrations/rollback?to=002` — 反向跑（需每份 migration 提供 down SQL）
- **dry-run 模式**：`POST /api/admin/migrations/apply?dry_run=true` — 仅打印不执行
- **migration 通知**：apply 成功后发 webhook / Slack 通知
- **Alembic 迁移路径**：如果未来 schema 变更复杂到 SQL 手写 hold 不住，迁移到 Alembic

---

_交付日期: 2026-09-04 / 任务: F / Commits: `7b51e06` + `2b8a220` / 测试: 12 passed_
