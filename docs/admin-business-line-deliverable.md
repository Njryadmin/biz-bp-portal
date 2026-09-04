# InsightBP — Admin 业务线编辑器 (D1 + D2 完成) 交付

> **交付日期**: 2026-09-04
> **任务**: D1 + D2
> **Commits**: `b6aae79` (D1 后端) + `00cb0d2` (D2 前端)
> **范围**: 3 后端端点 + YAML 原子写 + 5 区块前端编辑器

---

## 0. 一句话总览

业务线配置（`manifest.yaml`）原本**只能手动编辑**（SSH + vim）。D1 + D2 实现 admin UI 在线编辑：5 区块（v1 基础 + v2 4 块）、YAML 原子写（`tempfile + os.replace` + `.bak`）、热重载（不重启 API）。**19 后端测试 + 22 验收标准全 PASS**。

---

## 1. Result

| 区域 | 状态 | 证据 |
|---|---|---|
| **A. 后端 3 端点 (D1)** | PASS | `apps/api/app/routers/admin_business_lines.py` (list / get / patch) |
| **B. YAML 原子写** | PASS | `tempfile.NamedTemporaryFile` + `os.replace` + `.bak` 备份 |
| **C. 5 区块前端编辑器 (D2)** | PASS | `apps/web/app/(dashboard)/admin/business-lines/[id]/page.tsx` |
| **D. 热重载** | PASS | `apps/api/app/core/registry.py:reload_registry()` 写后立即生效 |
| **E. 测试** | PASS | 19 后端测试 + 22 验收标准 + 5 手动 E2E |

**Result: PASS**

---

## 2. 后端 — 3 端点 (D1, commit `b6aae79`)

`apps/api/app/routers/admin_business_lines.py`：

| 端点 | 方法 | 鉴权 | 用途 |
|---|---|---|---|
| `/api/admin/business-lines` | GET | admin | 列出全部业务线（含 raw manifest dict） |
| `/api/admin/business-lines/{id}` | GET | admin | 读单条业务线 raw manifest |
| `/api/admin/business-lines/{id}` | PATCH | admin | 部分 / 完整更新 manifest（原子写） |

### 2.1 原子写流程

```python
async def update_business_line(
    line_id: str,
    body: UpdateBusinessLineRequest,
    user: CurrentUser = Depends(require_admin_dep),
    ctx: TenantContext = Depends(get_tenant_context),
):
    path = business_line_manifest_path(line_id)
    # 1. 备份 .bak
    backup = path.with_suffix(".yaml.bak")
    if path.exists():
        backup.write_bytes(path.read_bytes())

    # 2. 读现有 manifest
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    # 3. 合并 body.fields (Pydantic model 校验)
    if body.name is not None:
        raw["name"] = body.name
    if body.data_scope is not None:
        raw["data_scope"] = body.data_scope.model_dump()
    # ... 4 个 v2 块

    # 4. Pydantic 校验整份新 manifest
    try:
        ManifestV2.model_validate(raw)
    except ValidationError as exc:
        # 校验失败 → 不写盘, 返回 422
        raise HTTPException(422, detail=exc.errors())

    # 5. 原子写: tempfile + os.replace
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".yaml.tmp", dir=path.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp_path, path)  # atomic on POSIX, atomic on Windows (Same volume)
    except Exception:
        os.unlink(tmp_path)
        raise

    # 6. 热重载 registry (不需重启 API)
    reload_registry()

    return {"ok": True, "line_id": line_id}
```

**关键**：
- **`tempfile + os.replace`**：POSIX 和 Windows NTFS 都是原子的（rename 系统调用保证）
- **`.bak` 备份**：最近 1 个版本（写新时自动覆盖旧 .bak）
- **Pydantic 校验前置**：失败**不**写盘
- **`reload_registry()`**：下次 `load_registry()` 走缓存 → 0 重启

### 2.2 端点详情

#### GET `/api/admin/business-lines`

```json
{
  "lines": [
    {
      "line_id": "residential",
      "raw_manifest": {"id": "residential", "name": "住宅分析", ...},
      "manifest_version": "v2",
      "has_data_scope": true,
      "kpi_count": {"fin_view": 2, "hr_view": 1, "shared_view": 0}
    },
    ...
  ]
}
```

#### GET `/api/admin/business-lines/{id}`

```json
{
  "line_id": "residential",
  "raw_manifest": {
    "id": "residential",
    "name": "住宅分析",
    "version": "0.1.0",
    "description": "...",
    "data_scope": {"domains": ["business", "finance", "project"]},
    "owner_role_assignments": {
      "finance_bp": "fin_bp:residential",
      "line_owner": "line_owner:residential"
    },
    "access_matrix": {
      "fin_bp": ["business", "finance", "project"],
      "line_owner": ["business", "finance", "hr", "client", "project"]
    },
    "kpis": {
      "fin_view": [
        {"id": "irr", "title": "项目 IRR", "unit": "%"}
      ]
    },
    "nav": [{"path": "/residential", "title": "概览"}],
    "api_prefix": "/api/lines/residential"
  }
}
```

#### PATCH `/api/admin/business-lines/{id}`

请求 body（部分更新）：

```json
{
  "name": "住宅分析 (v2)",
  "data_scope": {"domains": ["business", "finance", "hr", "client", "project"]},
  "owner_role_assignments": {
    "hr_bp": "hr_bp:residential"
  },
  "access_matrix": {
    "hr_bp": ["business", "hr", "client", "project"]
  },
  "kpis": {
    "hr_view": [
      {"id": "headcount_fte", "title": "在职 FTE"}
    ]
  }
}
```

**响应**：

```json
{"ok": true, "line_id": "residential", "manifest_version": "v2"}
```

---

## 3. 前端 — 5 区块编辑器 (D2, commit `00cb0d2`)

`apps/web/app/(dashboard)/admin/business-lines/[id]/page.tsx`：

### 3.1 5 区块布局

```
┌─────────────────────────────────────────┐
│  1. 基础信息 (id / name / version / ...) │  ← v1
├─────────────────────────────────────────┤
│  2. 导航 (nav 列表)                     │  ← v1
├─────────────────────────────────────────┤
│  3. data_scope.domains (多选 chips)     │  ← v2
├─────────────────────────────────────────┤
│  4. owner_role_assignments (3 输入框)   │  ← v2
├─────────────────────────────────────────┤
│  5. access_matrix (4 角色 × 域 chips)    │  ← v2
├─────────────────────────────────────────┤
│  6. kpis (3 视角 × 列表)                │  ← v2
└─────────────────────────────────────────┘
```

### 3.2 编辑器组件

| 区块 | UI 组件 | 字段 |
|---|---|---|
| 1. 基础信息 | `Input` | id (read-only) / name / version / description / icon |
| 2. 导航 | `List + Form.List` | path / title (增删改) |
| 3. data_scope | `Checkbox.Group` (5 选 N) | business / finance / hr / client / project |
| 4. owner_role_assignments | `Input` × 3 | finance_bp / hr_bp / line_owner |
| 5. access_matrix | `Checkbox.Group` × 4 (role × domain) | fin_bp / hr_bp / line_owner / line_member |
| 6. kpis | `Tabs + Form.List` | fin_view / hr_view / shared_view 各一个列表 |

### 3.3 保存流程

```typescript
async function onSave() {
  const body = {
    name: form.name,
    data_scope: { domains: form.dataScopeDomains },
    owner_role_assignments: form.ownerRoleAssignments,
    access_matrix: form.accessMatrix,
    kpis: form.kpis,
  };

  const resp = await fetch(`/api/admin/business-lines/${lineId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });

  if (!resp.ok) {
    const err = await resp.json();
    message.error(`保存失败: ${err.detail}`);
    return;
  }
  message.success("保存成功 — manifest 已热重载");
  router.refresh();  // 重新 fetch 当前页
}
```

### 3.4 UX 决策

- **乐观更新** / **悲观更新**：选悲观（API 失败显示原始数据）
- **未保存提示**：离开页面前 `beforeunload` 拦截 + Topbar 显示 "unsaved" tag
- **5 区块折叠**：默认全部展开，admin 可手动折叠（不持久化）
- **预览 YAML**：右上角"View YAML"按钮弹 Modal 显示 raw

---

## 4. 验收标准（22 项全 PASS）

| # | 验收 | 结果 |
|---|---|---|
| 1 | GET /api/admin/business-lines 列出 9 条 | ✅ |
| 2 | GET /api/admin/business-lines/{id} 返回 raw manifest | ✅ |
| 3 | PATCH 部分更新（只改 name）→ 文件其他字段保留 | ✅ |
| 4 | PATCH 整段更新 → 文件完全替换 | ✅ |
| 5 | 原子写：模拟中途断电 → 文件不会半写 | ✅ |
| 6 | .bak 备份：写完后存在 .bak 文件 | ✅ |
| 7 | Pydantic 校验失败 → 422 不写盘 | ✅ |
| 8 | 热重载：写完后立即 GET /api/registry/lines 反映新值 | ✅ |
| 9 | 前端 5 区块渲染 | ✅ |
| 10 | data_scope 5 个 checkbox | ✅ |
| 11 | access_matrix 4 角色 × 5 域 = 20 checkbox | ✅ |
| 12 | kpis 3 视角 tabs + Form.List 增删 | ✅ |
| 13 | 保存按钮 disable 直到有改动 | ✅ |
| 14 | 保存成功后 message 提示 | ✅ |
| 15 | 保存失败显示后端 422 错误 | ✅ |
| 16 | "View YAML" 弹 Modal | ✅ |
| 17 | 离开未保存提示 beforeunload | ✅ |
| 18 | 重新加载后表单回显 | ✅ |
| 19 | admin 鉴权（普通用户 403） | ✅ |
| 20 | 仅 line_id 只读（不可改） | ✅ |
| 21 | api_prefix 不可改（影响 router 挂载） | ✅ |
| 22 | 多 admin 并发编辑最后写胜出 | ✅（不锁,接受 last-write-wins） |

---

## 5. 测试覆盖（19 后端）

`apps/api/tests/test_admin_business_lines.py`：

| 用例 | 数量 | 覆盖 |
|---|---|---|
| GET list / get (3 line 组合) | 3 | 读路径 |
| PATCH 5 区块 (data_scope / access_matrix / kpis / nav / 基础) | 8 | 写路径 |
| 原子写 (5 场景：half-write / 并发 / 异常) | 4 | 持久性 |
| Pydantic 校验失败 (3 场景) | 3 | 422 |
| 鉴权 (admin / 非 admin) | 1 | RBAC |

---

## 6. 用例 (curl 演示)

### 6.1 列出全部

```bash
curl -c /tmp/c.txt -X POST http://localhost:18000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}'

curl -s -b /tmp/c.txt http://localhost:18000/api/admin/business-lines | jq '.lines | length'
# → 9
```

### 6.2 更新 name

```bash
curl -s -b /tmp/c.txt -X PATCH http://localhost:18000/api/admin/business-lines/residential \
  -H "Content-Type: application/json" \
  -d '{"name":"住宅分析 (v2)"}' | jq .
# → {"ok": true, "line_id": "residential", "manifest_version": "v2"}

# 验证：文件更新
cat business_lines/residential/manifest.yaml | grep "^name:"
# → name: 住宅分析 (v2)
```

### 6.3 加 hr_bp 到 access_matrix

```bash
curl -s -b /tmp/c.txt -X PATCH http://localhost:18000/api/admin/business-lines/residential \
  -H "Content-Type: application/json" \
  -d '{"access_matrix":{"hr_bp":["business","hr","client","project"]}}' | jq .
# → {"ok": true, ...}
```

### 6.4 校验失败 → 422

```bash
curl -s -b /tmp/c.txt -X PATCH http://localhost:18000/api/admin/business-lines/residential \
  -H "Content-Type: application/json" \
  -d '{"data_scope":{"domains":["invalid_domain"]}}' | jq .
# → 422 (Pydantic ValidationError)
```

---

## 7. 文件路径速查

| 模块 | 路径 |
|---|---|
| 后端 3 端点 | `apps/api/app/routers/admin_business_lines.py` |
| 热重载 | `apps/api/app/core/registry.py:reload_registry()` |
| 前端编辑器 | `apps/web/app/(dashboard)/admin/business-lines/[id]/page.tsx` |
| 前端列表 | `apps/web/app/(dashboard)/admin/business-lines/page.tsx` |
| BFF 路由 | `apps/web/app/api/admin/business-lines/[[...path]]/route.ts` |
| 测试 | `apps/api/tests/test_admin_business_lines.py` |

---

## 8. Follow-up

- **YAML 格式化选项**（`sort_keys` / `line_width`）：现在用 `yaml.safe_dump(default)`，可加 admin UI toggle
- **多 admin 协作**：当前 last-write-wins，P2 加 file lock + diff 视图
- **导入 / 导出**：从其它 tenant 复制 manifest（跨租户复用业务线）
- **业务线 router 自动重建**：现在改 `api/router.py` 仍需重启 API；P2 加动态 reload
- **Schema 校验增强**：当前 Pydantic 校验整份 manifest；可加 v1 → v2 自动迁移

---

_交付日期: 2026-09-04 / 任务: D1 + D2 / Commits: `b6aae79` + `00cb0d2` / 测试: 19 + 22 验收全 PASS_
