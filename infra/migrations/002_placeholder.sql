-- ============================================================================
-- 002_placeholder.sql
--
-- 目的: 验证 migration runner 能按顺序处理多个文件.
--       跑完不留垃圾表 (CREATE + DROP 都在 DO 块内).
-- 锁定日期: 2026-09-04
-- 适用: F 任务测试场景, 不影响生产 schema
-- ============================================================================

DO $$
BEGIN
    CREATE TABLE IF NOT EXISTS _migration_runner_test (
        id SERIAL PRIMARY KEY,
        ran_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    -- 跑完立刻 DROP, 不留垃圾表
    DROP TABLE _migration_runner_test;
END$$;
