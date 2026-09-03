-- business_lines/retail/dbt/models/staging/stg_leases.sql
-- 租约明细 (staging). 标准化字段 + 计算剩余年限.

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_retail', 'leases') }}
),

renamed as (
    select
        lease_id,
        property_id,
        tenant_id,
        tenant_name,
        category,
        area_sqm,
        term_years,
        start_year,
        -- 当前年份硬编码为 2025,生产环境会用 current_date 或 dbt var 注入
        2025 - start_year                       as years_elapsed,
        term_years - (2025 - start_year)        as years_remaining,
        monthly_rent_yuan_per_sqm,
        annual_escalation                       as rent_escalation,
        -- 月租金总额
        area_sqm * monthly_rent_yuan_per_sqm    as monthly_rent_total_yuan,
        -- 年化基础租金 (元)
        area_sqm * monthly_rent_yuan_per_sqm * 12 as annual_base_rent_yuan
    from source
)

select * from renamed
