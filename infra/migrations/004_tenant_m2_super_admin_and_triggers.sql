-- ============================================================================
-- 004_tenant_m2_super_admin_and_triggers.sql
--
-- InsightBP v2 P2 多租户 — M2 升级 (2026-09-04)
--
-- 引用: .routex-handoff/INSIGHTBP-DISCUSSION-SUMMARY.md §5.3 (P2 多租户)
-- 前置: 003_multi_tenant_setup.sql 已 apply (tenants + 6 张业务表 tenant_id NOT NULL + RLS)
--
-- 这一波做 3 件事:
--   1. users 表加 is_super_admin 列 — 标记可跨 tenant 切换 / bypass RLS 的 admin
--   2. BEFORE INSERT 触发器 — 当 INSERT 不带 tenant_id 时, 自动从 GUC ``app.tenant_id``
--      读取填入. 这样 router 用 ``tenant_session()`` 包装的 INSERT 不需要每处都写
--      ``tenant_id = :tid``; GUC 已经在, 触发器自动填.
--   3. 标记 ``admin`` 用户的 is_super_admin = TRUE (M2 范围内唯一 super admin).
--      后续 M3 可加 admin UI 提升 / 降级其它用户.
--
-- 设计要点
-- --------
-- * 触发器函数 set_tenant_from_guc() 是 BEFORE INSERT.  只在 NEW.tenant_id IS NULL
--   时才覆盖, 给 INSERT 显式带 tenant_id 的调用方 (如 M2 测试 helper) 优先权.
-- * GUC 读取用 ``current_setting('app.tenant_id', true)``: 第二个参数 ``true`` 让
--   GUC 不存在时返回 NULL 而不是抛错. 然后用 NULLIF(..., '')::uuid 让空串
--   (M1 默认行为) 也走 NULL → NOT NULL 违反 — 错误信息仍然清晰 ("null value in
--   column tenant_id"), 只是路径从"列默认 NULL"变成"GUC 没设".
-- * 触发器不覆盖 UPDATE. UPDATE 走 RLS policy filter, 不修改列. router 显式
--   UPDATE 某行 (如 is_active / password) 不需要改 tenant_id, RLS 也不允许改.
-- * 完全 idempotent: 跑 N 遍结果相同 (列加 IF NOT EXISTS / 触发器加 OR REPLACE /
--   触发器创建用 DO 块捕 duplicate_object).
--
-- 不破坏
-- --------
-- * v0.1.0 + PR#1 + M1 现有功能 0 改动.
-- * 9 个种子用户 (admin + 8 BP) is_super_admin 默认为 FALSE, 唯一例外是 admin.
--   8 个 BP 仍是普通用户, 走 user.tenant_id 路径, 不被提升为 super admin.
-- * 6 张表的 tenant_id NOT NULL 约束不动.
-- * 触发器只在 INSERT 不带 tenant_id 时介入, 不影响现有的 explicit INSERT.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. users.is_super_admin 列 + 部分索引 (只索引 TRUE 行, 小)
-- ---------------------------------------------------------------------------

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS is_super_admin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_super_admin
    ON users (is_super_admin) WHERE is_super_admin = TRUE;

-- ---------------------------------------------------------------------------
-- 2. 标记 admin 用户为 super admin
--    WHERE EXISTS 包一下: dev DB 里 admin 可能已被 test_admin_v2_roles.py 删掉,
--    这种情况下 UPDATE 影响 0 行而不是抛错.
-- ---------------------------------------------------------------------------

UPDATE users
SET is_super_admin = TRUE
WHERE username = 'admin'
  AND EXISTS (SELECT 1 FROM users WHERE username = 'admin');

-- ---------------------------------------------------------------------------
-- 3. 触发器函数: 从 GUC 读 tenant_id, 写进 NEW.tenant_id
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION set_tenant_from_guc()
RETURNS TRIGGER AS $$
BEGIN
    -- 只在 NEW.tenant_id 未设时介入, 给 INSERT 显式带值的调用方优先权.
    IF NEW.tenant_id IS NULL THEN
        -- 优先级:
        --   1. GUC 设了 → 用 GUC (router 走 tenant_session() 时走的路径)
        --   2. GUC 没设 / 空串 → 回落 DEFAULT_TENANT_ID
        --      (audit middleware 之类不走 tenant_session 的代码会走这条;
        --       NOT NULL 违反会拖垮审计写入, 走 default 至少审计能跑)
        NEW.tenant_id := COALESCE(
            NULLIF(current_setting('app.tenant_id', true), '')::uuid,
            '00000000-0000-0000-0000-000000000000'
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4. 6 张表的 BEFORE INSERT 触发器 (idempotent: DO 块捕 duplicate_object)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_users_set_tenant'
    ) THEN
        CREATE TRIGGER trg_users_set_tenant
            BEFORE INSERT ON users
            FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_user_roles_set_tenant'
    ) THEN
        CREATE TRIGGER trg_user_roles_set_tenant
            BEFORE INSERT ON user_roles
            FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_user_business_lines_set_tenant'
    ) THEN
        CREATE TRIGGER trg_user_business_lines_set_tenant
            BEFORE INSERT ON user_business_lines
            FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_audit_log_set_tenant'
    ) THEN
        CREATE TRIGGER trg_audit_log_set_tenant
            BEFORE INSERT ON raw.audit_log
            FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_ai_models_set_tenant'
    ) THEN
        CREATE TRIGGER trg_ai_models_set_tenant
            BEFORE INSERT ON ai_models
            FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
    END IF;
END$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname = 'trg_uploads_set_tenant'
    ) THEN
        CREATE TRIGGER trg_uploads_set_tenant
            BEFORE INSERT ON raw.uploads
            FOR EACH ROW EXECUTE FUNCTION set_tenant_from_guc();
    END IF;
END$$;

-- ---------------------------------------------------------------------------
-- 5. 验证: 触发器 + 列都到位; admin 被标记 (若存在)
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    n_triggers INT;
    n_admin    INT;
BEGIN
    SELECT COUNT(*) INTO n_triggers
    FROM pg_trigger
    WHERE tgname IN (
        'trg_users_set_tenant',
        'trg_user_roles_set_tenant',
        'trg_user_business_lines_set_tenant',
        'trg_audit_log_set_tenant',
        'trg_ai_models_set_tenant',
        'trg_uploads_set_tenant'
    );
    IF n_triggers <> 6 THEN
        RAISE EXCEPTION '004_tenant_m2: expected 6 tenant triggers, got %', n_triggers;
    END IF;

    SELECT COUNT(*) INTO n_admin
    FROM users WHERE username = 'admin' AND is_super_admin = TRUE;
    IF n_admin > 1 THEN
        RAISE EXCEPTION '004_tenant_m2: more than one super admin (n=%)', n_admin;
    END IF;
    -- n_admin = 0 是合法的 (dev DB admin 被 test 删了, 下次 login 会重新走 bootstrap)

    RAISE NOTICE '004_tenant_m2: ALL CLEAR — triggers=6, super_admins=% (1 expected if admin exists)', n_admin;
END$$;

COMMIT;
