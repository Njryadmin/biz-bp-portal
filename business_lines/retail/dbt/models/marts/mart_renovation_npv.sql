-- business_lines/retail/dbt/models/marts/mart_renovation_npv.sql
-- 调改 NPV 参数表 (mart). 维持 vs 调改两档的输入参数.
-- NPV 折现/IRR 计算在应用层 (api/router.py) 用 Python 完成,因为 SQL 表达 IRR
-- 既笨拙又不通用. 本表只负责稳定地输出:
--   - 两档方案的 capex
--   - 第一年 NOI
--   - 后续 NOI 增长率
--   - 终值资本化率
--   - 持有期
--   - 折现率
-- 应用层 (router) join 本表 + stg_properties 即可在 Python 端做折现.

{{ config(materialized='table') }}

with props as (
    select * from {{ ref('stg_properties') }}
),

params as (
    select
        10                  as horizon_years,
        0.08                as discount_rate,
        0.055               as terminal_cap_rate,
        0.12                as renovate_uplift_year1,
        0.015               as renovate_extra_escalation,
        600.0               as renovate_capex_per_sqm_yuan
),

props_with_params as (
    select
        p.property_id,
        p.property_name,
        p.noi_wan,
        p.rent_escalation_rate,
        p.gla_wan_sqm,
        pa.horizon_years,
        pa.discount_rate,
        pa.terminal_cap_rate,
        pa.renovate_uplift_year1,
        pa.renovate_extra_escalation,
        pa.renovate_capex_per_sqm_yuan,
        -- 调改资本支出 (万元)
        round(p.gla_wan_sqm * pa.renovate_capex_per_sqm_yuan / 10000.0, 0)
            as renovate_capex_wan,
        -- 调改后递增率 = 基础 + 额外
        p.rent_escalation_rate + pa.renovate_extra_escalation
            as renovate_escalation
    from props p
    cross join params pa
)

select
    property_id,
    property_name,
    horizon_years,
    discount_rate,
    terminal_cap_rate,
    -- Maintain scenario inputs
    0                                                           as maintain_capex_wan,
    noi_wan                                                     as maintain_year1_noi_wan,
    rent_escalation_rate                                        as maintain_noi_growth,
    -- Renovate scenario inputs
    renovate_capex_wan                                          as renovate_capex_wan,
    round(noi_wan * (1.0 + renovate_uplift_year1), 0)          as renovate_year1_noi_wan,
    renovate_escalation                                         as renovate_noi_growth
from props_with_params
