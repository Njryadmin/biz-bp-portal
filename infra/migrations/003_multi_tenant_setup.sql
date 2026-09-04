-- ============================================================================
-- 003_multi_tenant_setup.sql
--
-- 多租户隔离 — InsightBP v2 阶段 P2 多租户起步 (M1)
-- 锁定日期: 2026-09-04
-- 引用: .routex-handoff/INSIGHTBP-DISCUSSION-SUMMARY.md §5.3 (P2 多租户)
--
-- 这一波只做 schema 改造 + RLS 启用 + 默认 tenant backfill.
-- 不做: tenant context middleware / router 改造 / super admin UI (M2/M3).
--
-- 不破坏: v0.1.0 + PR#1 现有功能. 9 个种子用户 (admin + 8 个 bp:<line>)
-- 全部 backfill 到 default tenant. 现有 raw.audit_log / ai_models /
-- raw.uploads 全部 backfill.
--
-- 设计要点:
--   * tenants 表 (顶层), 1 个 default tenant (UUID 全 0) 作 backfill 目标
--   * 6 张业务表加 tenant_id 列 (additive), NOT NULL after backfill
--   * FK 约束 (RESTRICT, 防止误删 tenant)
--   * RLS 启用 + FORCE + tenant_lock policy — 任何 query 必须带
--     ``app.tenant_id`` GUC, 否则返 0 行 (默认锁住).
--   * M2 middleware 通过 ``SET LOCAL app.tenant_id = '<uuid>'`` 解锁.
--   * migration 完全 idempotent: 跑两遍不报错, 跑 N 遍结果相同.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. tenants 表 — 顶层租户
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tenants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         TEXT UNIQUE NOT NULL,           -- 'acme-realty', 'jll', 'cbre', ...
    name         TEXT NOT NULL,                  -- 'Acme Realty'
    plan         TEXT NOT NULL DEFAULT 'standard',  -- 'standard' | 'enterprise' | 'demo'
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata     JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- plan 列枚举约束 (idempotent: 跑两遍 = drop + recreate)
DO $$
BEGIN
    ALTER TABLE tenants DROP CONSTRAINT IF EXISTS tenants_plan_check;
    ALTER TABLE tenants
        ADD CONSTRAINT tenants_plan_check
        CHECK (plan IN ('standard', 'enterprise', 'demo'));
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'tenants_plan_check skipped: %', SQLERRM;
END$$;

-- ---------------------------------------------------------------------------
-- 2. default tenant (backfill 目标, 固定 UUID 全 0)
-- ---------------------------------------------------------------------------

INSERT INTO tenants (id, slug, name, plan, is_active)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'default',
    'Default Tenant (legacy)',
    'enterprise',
    TRUE
) ON CONFLICT (slug) DO NOTHING;

-- ---------------------------------------------------------------------------
-- 3. 加 tenant_id 列 (additive, 不破坏现有数据)
-- ---------------------------------------------------------------------------

ALTER TABLE users               ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE user_roles          ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE user_business_lines ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE raw.audit_log       ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE ai_models           ADD COLUMN IF NOT EXISTS tenant_id UUID;
ALTER TABLE raw.uploads         ADD COLUMN IF NOT EXISTS tenant_id UUID;

-- ---------------------------------------------------------------------------
-- 4. 默认 tenant_id backfill (所有现有行 = default tenant)
-- ---------------------------------------------------------------------------

UPDATE users               SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
UPDATE user_roles          SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
UPDATE user_business_lines SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
UPDATE raw.audit_log       SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
UPDATE ai_models           SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;
UPDATE raw.uploads         SET tenant_id = '00000000-0000-0000-0000-000000000000' WHERE tenant_id IS NULL;

-- ---------------------------------------------------------------------------
-- 5. NOT NULL 约束 (idempotent: 已 NOT NULL 时 no-op)
--    验证 backfill 完整性: 还有 NULL → 抛错, 中断 migration
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    has_nulls BOOLEAN;
    bad_table TEXT;
BEGIN
    FOR bad_table IN
        SELECT t FROM (VALUES
            ('public.users'),
            ('public.user_roles'),
            ('public.user_business_lines'),
            ('raw.audit_log'),
            ('public.ai_models'),
            ('raw.uploads')
        ) AS tbls(t)
    LOOP
        -- ``%s`` (not %I) keeps the dot as a separator; the schema.table
        -- identifier passes through verbatim. Using %I would quote the
        -- whole string as one identifier, breaking the schema.table
        -- reference.
        EXECUTE format('SELECT EXISTS (SELECT 1 FROM %s WHERE tenant_id IS NULL)', bad_table)
            INTO has_nulls;
        IF has_nulls THEN
            RAISE EXCEPTION '003_multi_tenant: backfill incomplete for table % (%)', bad_table, has_nulls;
        END IF;
    END LOOP;
    RAISE NOTICE '003_multi_tenant: backfill verified — 0 NULL tenant_id rows across 6 tables';
END$$;

-- 现在加 NOT NULL. ALTER ... SET NOT NULL 本身是 idempotent (已 NOT NULL 时 no-op)
ALTER TABLE users               ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE user_roles          ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE user_business_lines ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE raw.audit_log       ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE ai_models           ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE raw.uploads         ALTER COLUMN tenant_id SET NOT NULL;

-- ---------------------------------------------------------------------------
-- 6. 索引 (RLS policy 性能关键)
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_users_tenant               ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_tenant          ON user_roles (tenant_id);
CREATE INDEX IF NOT EXISTS idx_user_business_lines_tenant ON user_business_lines (tenant_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_tenant           ON raw.audit_log (tenant_id);
CREATE INDEX IF NOT EXISTS idx_ai_models_tenant           ON ai_models (tenant_id);
CREATE INDEX IF NOT EXISTS idx_uploads_tenant             ON raw.uploads (tenant_id);

-- ---------------------------------------------------------------------------
-- 7. FK 约束 (idempotent: DO 块捕获 duplicate_object)
--    ON DELETE RESTRICT: 防误删 tenant (必须先迁走所有行才能删 tenant)
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    ALTER TABLE users
        ADD CONSTRAINT users_tenant_id_fkey
        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    ALTER TABLE user_roles
        ADD CONSTRAINT user_roles_tenant_id_fkey
        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    ALTER TABLE user_business_lines
        ADD CONSTRAINT user_business_lines_tenant_id_fkey
        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    ALTER TABLE raw.audit_log
        ADD CONSTRAINT audit_log_tenant_id_fkey
        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    ALTER TABLE ai_models
        ADD CONSTRAINT ai_models_tenant_id_fkey
        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    ALTER TABLE raw.uploads
        ADD CONSTRAINT uploads_tenant_id_fkey
        FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

-- ---------------------------------------------------------------------------
-- 8. RLS 启用 + FORCE
--    ENABLE: 表 RLS 生效
--    FORCE: table owner 也受 RLS 限制 (防特权用户绕过)
-- ---------------------------------------------------------------------------

ALTER TABLE users               ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_roles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_business_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.audit_log       ENABLE ROW LEVEL SECURITY;
ALTER TABLE ai_models           ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw.uploads         ENABLE ROW LEVEL SECURITY;

ALTER TABLE users               FORCE ROW LEVEL SECURITY;
ALTER TABLE user_roles          FORCE ROW LEVEL SECURITY;
ALTER TABLE user_business_lines FORCE ROW LEVEL SECURITY;
ALTER TABLE raw.audit_log       FORCE ROW LEVEL SECURITY;
ALTER TABLE ai_models           FORCE ROW LEVEL SECURITY;
ALTER TABLE raw.uploads         FORCE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 9. RLS 策略: tenant_lock
--    任何 query 必须满足 ``tenant_id = current_setting('app.tenant_id')::uuid``.
--    未设 GUC 时 current_setting(...) 返空串 '' → ::uuid 抛错 → 返 0 行.
--    M2 middleware 用 ``SET LOCAL app.tenant_id = '<uuid>'`` 解锁.
--    M2+ 还会加 ``OR current_setting('app.bypass_rls', true) = 'on'`` 让
--    super admin 绕过 (本波不加, 留 M2 范围).
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    CREATE POLICY tenant_lock ON users
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    CREATE POLICY tenant_lock ON user_roles
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    CREATE POLICY tenant_lock ON user_business_lines
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    CREATE POLICY tenant_lock ON raw.audit_log
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    CREATE POLICY tenant_lock ON ai_models
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

DO $$
BEGIN
    CREATE POLICY tenant_lock ON raw.uploads
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
EXCEPTION WHEN duplicate_object THEN
    NULL;
END$$;

-- ---------------------------------------------------------------------------
-- 10. 验证 (run 一次 SELECT 计数, 跟原数据行数对比, 确认 backfill 完整)
--     NOTICE 不阻塞, 但失败 RAISE EXCEPTION 会让 migration 整体回滚.
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    total_users          INT;
    backfilled_users     INT;
    total_roles          INT;
    backfilled_roles     INT;
    total_audit          INT;
    backfilled_audit     INT;
    total_models         INT;
    backfilled_models    INT;
    total_uploads        INT;
    backfilled_uploads   INT;
    total_lines          INT;
    backfilled_lines     INT;
BEGIN
    SELECT COUNT(*) INTO total_users        FROM users;
    SELECT COUNT(*) INTO backfilled_users   FROM users               WHERE tenant_id = '00000000-0000-0000-0000-000000000000';

    SELECT COUNT(*) INTO total_roles        FROM user_roles;
    SELECT COUNT(*) INTO backfilled_roles   FROM user_roles          WHERE tenant_id = '00000000-0000-0000-0000-000000000000';

    SELECT COUNT(*) INTO total_lines        FROM user_business_lines;
    SELECT COUNT(*) INTO backfilled_lines   FROM user_business_lines WHERE tenant_id = '00000000-0000-0000-0000-000000000000';

    SELECT COUNT(*) INTO total_audit        FROM raw.audit_log;
    SELECT COUNT(*) INTO backfilled_audit   FROM raw.audit_log       WHERE tenant_id = '00000000-0000-0000-0000-000000000000';

    SELECT COUNT(*) INTO total_models       FROM ai_models;
    SELECT COUNT(*) INTO backfilled_models  FROM ai_models           WHERE tenant_id = '00000000-0000-0000-0000-000000000000';

    SELECT COUNT(*) INTO total_uploads      FROM raw.uploads;
    SELECT COUNT(*) INTO backfilled_uploads FROM raw.uploads         WHERE tenant_id = '00000000-0000-0000-0000-000000000000';

    -- 任何一张表 backfill 不完整 = migration 失败
    IF total_users != backfilled_users THEN
        RAISE EXCEPTION 'backfill incomplete: %/% users have default tenant', backfilled_users, total_users;
    END IF;
    IF total_roles != backfilled_roles THEN
        RAISE EXCEPTION 'backfill incomplete: %/% user_roles have default tenant', backfilled_roles, total_roles;
    END IF;
    IF total_lines != backfilled_lines THEN
        RAISE EXCEPTION 'backfill incomplete: %/% user_business_lines have default tenant', backfilled_lines, total_lines;
    END IF;
    IF total_audit != backfilled_audit THEN
        RAISE EXCEPTION 'backfill incomplete: %/% audit_log have default tenant', backfilled_audit, total_audit;
    END IF;
    IF total_models != backfilled_models THEN
        RAISE EXCEPTION 'backfill incomplete: %/% ai_models have default tenant', backfilled_models, total_models;
    END IF;
    IF total_uploads != backfilled_uploads THEN
        RAISE EXCEPTION 'backfill incomplete: %/% uploads have default tenant', backfilled_uploads, total_uploads;
    END IF;

    RAISE NOTICE '003_multi_tenant: ALL CLEAR — users=%, roles=%, lines=%, audit=%, models=%, uploads=% (all backfilled to default tenant)',
        backfilled_users, backfilled_roles, backfilled_lines, backfilled_audit, backfilled_models, backfilled_uploads;
END$$;

COMMIT;
