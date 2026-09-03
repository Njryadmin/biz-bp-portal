# docs/ — 文档索引

> 读者：第一次接触本仓库的工程师 / 维护者 / 交接方。
> 完整导航从这里开始。

---

## 快速开始（先读这 3 个）

| 文件 | 用途 | 何时读 |
|---|---|---|
| [`../README.md`](../README.md) | 5 分钟上手（本地 dev + 业务线一览） | 第一天 |
| [`../MAINTENANCE.md`](../MAINTENANCE.md) | 维护手册（TL;DR + 目录 + 约定 + 陷阱） | 第一周 |
| [`../AGENTS.md`](../AGENTS.md) | AI 代理交接（硬约束 + 入口指针） | 接管仓库的 AI |

---

## 维护手册（新增，2026-09-03）

`docs/maintenance/` — 主题化运维 / 扩展 / 排查手册。

| 文件 | 用途 |
|---|---|
| [`maintenance/operations.md`](maintenance/operations.md) | 日常运维：启动 / 重启 / 日志 / 爬虫 / 密钥轮换 / DB 备份还原 |
| [`maintenance/extending.md`](maintenance/extending.md) | 扩展系统：加业务线 / 加 LLM / 加 BFF / 加告警 / 加 API 端点 |
| [`maintenance/troubleshooting.md`](maintenance/troubleshooting.md) | 故障排查：21 个常见症状的诊断 + 修法 |
| [`maintenance/architecture-decisions.md`](maintenance/architecture-decisions.md) | 设计决策的"为什么"（BFF / RBAC / 加密 / reparse-point 等 16 条） |
| [`maintenance/conventions.md`](maintenance/conventions.md) | 编码规范（Python / TypeScript / YAML / commit / PR） |

---

## 架构与设计

| 文件 | 用途 |
|---|---|
| [`architecture-overview.md`](architecture-overview.md) | 5 张架构图（系统分层 / 插件机制 / 通用引擎 / Copilot 降级链 / 数据流） |
| [`architecture-audit-2026-09-03.md`](architecture-audit-2026-09-03.md) | 架构审计：10/11 PASS + 0 P0/P1 + 通用性验证 |
| [`architecture.md`](architecture.md) | 旧版架构概览（已被 architecture-overview.md 取代，但仍可读） |

---

## 主题交付文档（2026-09-03 集成批次）

按时间倒序，每个对应一次大功能上线。

| 文件 | 主题 |
|---|---|
| [`rbac-2026-09-03-deliverable.md`](rbac-2026-09-03-deliverable.md) | RBAC：4 角色 + JWT + httpOnly cookie + 11 用户 + 15 个 curl 场景 |
| [`ai-models-deliverable.md`](ai-models-deliverable.md) | AI 模型注册表：6 厂商 + Fernet 加密 + 运行时切换 |
| [`admin-users-deliverable.md`](admin-users-deliverable.md) | 用户管理：CRUD + 重置密码 + 角色编辑 |
| [`scrapers-deliverable.md`](scrapers-deliverable.md) | 爬虫框架：3 个真实源（NBS / 链家 / 政策）+ 降级链 |
| [`forecast-alerts-deliverable.md`](forecast-alerts-deliverable.md) | 滚动预测 + 告警中心 |
| [`sensitivity-deliverable.md`](sensitivity-deliverable.md) | 敏感性 Lab（双因子 + 龙卷风 + 情景） |
| [`llm-integration-deliverable.md`](llm-integration-deliverable.md) | LLM 集成：DeepSeek / Ollama / Mock + Fallback |
| [`copilot-deliverable.md`](copilot-deliverable.md) | AI Copilot：14 mock intent + 真实 LLM |
| [`business-lines-addon-deliverable.md`](business-lines-addon-deliverable.md) | 业务线插件机制 |
| [`cockpit-deliverable.md`](cockpit-deliverable.md) | 总览驾驶舱 + 通用 UI 组件 |
| [`p2-fixes-2026-09-03-deliverable.md`](p2-fixes-2026-09-03-deliverable.md) | P2 修复批次 |
| [`fixes-2026-09-03-deliverable.md`](fixes-2026-09-03-deliverable.md) | P0/P1 修复批次（包含 commit hash 与验证） |
| [`deliverable-t0.md`](deliverable-t0.md) | T0 初始交付（最早期版本） |

---

## 扩展指南

| 文件 | 用途 |
|---|---|
| [`plugin-howto.md`](plugin-howto.md) | 5 步新增业务线（旧版，含完整 YAML 字段文档） |
| [`../business_lines/_template/README.md`](../business_lines/_template/README.md) | 模板目录自带 5 步指南（新版） |
| [`../infra/README.md`](../infra/README.md) | 生产部署基础设施（docker compose / Airflow / DBT） |
| [`../apps/web/README.md`](../apps/web/README.md) | Next.js 前端开发 |
| [`../packages/types/README.md`](../packages/types/README.md) | 共享 TypeScript 类型 |

---

## 变更日志

| 文件 | 用途 |
|---|---|
| [`changelog.md`](changelog.md) | 按日期排序的变更日志（中文） |
| [`../DEPLOY.md`](../DEPLOY.md) | 部署指南（中文） |
| [`e2e-verification.md`](e2e-verification.md) | 端到端测试结果 |

---

## 专题：修复记录

每个 `fixes-*.md` 包含 commit hash + 验证步骤。
修复与对应 `AGENTS.md §9 哪里开始看` 的入口交叉引用。

---

## 文档维护规则

新加 `docs/*.md` 时：

1. **先**更新本 `docs/README.md` 索引（在对应分类下加一行）
2. **再**更新 `../MAINTENANCE.md §12 文档地图`（如果是顶级入口文档）
3. **最后** commit

文档语言：**中文**（已 2026-09-03 翻译）。
代码注释语言：中文（标识符英文）。
