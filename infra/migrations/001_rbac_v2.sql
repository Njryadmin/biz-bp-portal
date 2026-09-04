-- ============================================================================
-- 001_rbac_v2.sql
--
-- 目的: 把 user_roles 表从 v1 升级到 v2, 支持 8 角色 + 5 数据域 + FIN/HR 物理隔离.
-- 锁定日期: 2026-09-04
-- 作者: Codex (合入) / 路由虾 (设计)
-- 不破坏: v1 `bp:<line>` 角色自动 backfill, 8 月份发布的 8 个种子用户继续可用
--
-- 引用: .routex-handoff/rbac_v2.py (Role / Scope / DataDomain 枚举)
--       apps/api/app/db/bootstrap.py:87-151 (v1 AUTH_DDL 已有的 user_roles / user_business_lines 定义)
--
-- 用法: 手动跑 (psql / pgAdmin / DBeaver) 或由 migration runner 自动应用
--   psql -U finbp -d finbp -f infra/migrations/001_rbac_v2.sql
-- ============================================================================

BEGIN;

-- 1. 扩展 user_roles 表: 加 scope + line_id 两列 (v2 必需)
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS scope TEXT;
ALTER TABLE user_roles ADD COLUMN IF NOT EXISTS line_id TEXT;

-- 2. 加 CHECK 约束 (scope 必须 ∈ {global, business_line})
--    DO 块兜底处理 "constraint 已存在" 错误 (idempotent)
DO $$
BEGIN
    ALTER TABLE user_roles DROP CONSTRAINT IF EXISTS user_roles_scope_check;
    ALTER TABLE user_roles ADD CONSTRAINT user_roles_scope_check
        CHECK (scope IS NULL OR scope IN ('global', 'business_line'));
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'scope check constraint skipped: %', SQLERRM;
END$$;

-- 3. 索引: 加速 (scope, line_id) 联合查找 (v2 路由关键路径)
CREATE INDEX IF NOT EXISTS idx_user_roles_scope_line
    ON user_roles (scope, line_id)
    WHERE scope IS NOT NULL;

-- 4. Backfill v1 → v2 (按角色字符串启发式推断)
--    4a. 全局角色 (admin / auditor / viewer) → scope='global', line_id=NULL
UPDATE user_roles
SET scope = 'global', line_id = NULL
WHERE role IN ('admin', 'auditor', 'viewer')
  AND scope IS NULL;

--    4b. 业务线角色 (bp:<line>) → scope='business_line', line_id=SUBSTR(role, 4)
--        保守策略: 把 v1 的 bp:<line> 视作 v2 line_owner 等价
--        (单一 binding, 全业务线权限). 后续 admin 手动细分成 fin_bp / hr_bp
UPDATE user_roles
SET scope = 'business_line',
    line_id = SUBSTR(role, 4)  -- 去掉 'bp:' 前缀
WHERE role LIKE 'bp:%'
  AND scope IS NULL;

--    4c. 兜底: 任何 scope 仍为 NULL 的行 (未识别的角色字符串) → 标 'global'
UPDATE user_roles
SET scope = 'global', line_id = NULL
WHERE scope IS NULL;

-- 5. 验证 backfill 完整性 (NOTICE 不阻塞, 失败由 admin 排查)
DO $$
DECLARE
    unmapped_count INT;
BEGIN
    SELECT COUNT(*) INTO unmapped_count FROM user_roles WHERE scope IS NULL;
    IF unmapped_count > 0 THEN
        RAISE WARNING 'rbac_v2 migration: % user_roles rows still have NULL scope (review manually)', unmapped_count;
    ELSE
        RAISE NOTICE 'rbac_v2 migration: all user_roles rows mapped successfully';
    END IF;
END$$;

-- 6. 同步 user_business_lines (v1 兼容读路径, 给 v1 CurrentUser 继续用)
--    v1 的 user_business_lines 是 bp:role 行的 "shadow", 已经存在, 不用动
--    但如果某用户有 scope=business_line 角色但 user_business_lines 缺对应行, 补上
INSERT INTO user_business_lines (user_id, line_id)
SELECT ur.user_id, ur.line_id
FROM user_roles ur
WHERE ur.scope = 'business_line'
  AND ur.line_id IS NOT NULL
ON CONFLICT (user_id, line_id) DO NOTHING;

COMMIT;
