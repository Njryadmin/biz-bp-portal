-- business_lines/residential/dbt/models/marts/fct_residential_redlines.sql
-- Mart: project-level three red lines (三道红线) snapshot.
-- Powers: asset_liability_ratio, net_debt_ratio, cash_to_short_debt.
-- Thresholds (regulator):
--   asset_liability_ratio   <= 70%
--   net_debt_ratio          <= 100%
--   cash_to_short_debt      >= 1.0x

{{ config(materialized='table') }}

with projects as (

    -- mock project balance sheet — in production this is the project_dim
    -- table joined with the corporate finance system.
    select
        project_id,
        project_name,
        city,
        short_term_debt_yi,
        long_term_debt_yi,
        cash_yi,
        total_assets_yi,
        total_liabilities_yi,
        shareholders_equity_yi
    from (
        values
            ('PRJ-001', '上海·绿城黄浦江', '上海',   8.2,  32.5, 11.8, 105.0, 68.0, 37.0),
            ('PRJ-002', '北京·万科海淀',   '北京',   6.5,  28.0,  9.2,  92.0, 56.0, 36.0),
            ('PRJ-003', '深圳·华润前海',   '深圳',  12.0,  45.0, 18.5, 145.0, 88.0, 57.0),
            ('PRJ-004', '杭州·龙湖滨江',   '杭州',   9.5,  36.0, 12.5, 110.0, 72.0, 38.0),
            ('PRJ-005', '成都·保利天府',   '成都',   4.2,  18.0,  6.5,  58.0, 32.0, 26.0),
            ('PRJ-006', '广州·中海天河',   '广州',   5.8,  24.0,  7.4,  88.0, 52.0, 36.0),
            ('PRJ-007', '南京·金地江宁',   '南京',   7.5,  22.0,  4.2,  64.0, 42.0, 22.0),
            ('PRJ-008', '苏州·金地工业园', '苏州',   4.8,  12.0,  2.4,  38.0, 25.0, 13.0)
    ) as t(
        project_id, project_name, city,
        short_term_debt_yi, long_term_debt_yi, cash_yi,
        total_assets_yi, total_liabilities_yi, shareholders_equity_yi
    )

)

select
    project_id,
    project_name,
    city,
    short_term_debt_yi,
    long_term_debt_yi,
    cash_yi,
    total_assets_yi,
    total_liabilities_yi,
    shareholders_equity_yi,
    -- 红线一：剔除预收款的资产负债率 (此处用全口径作 mock)
    total_liabilities_yi / nullif(total_assets_yi, 0)              as asset_liability_ratio,
    -- 红线二：净负债率
    (short_term_debt_yi + long_term_debt_yi - cash_yi)
        / nullif(shareholders_equity_yi, 0)                          as net_debt_ratio,
    -- 红线三：现金短债比
    cash_yi / nullif(short_term_debt_yi, 0)                          as cash_to_short_debt,
    0.70 as asset_liability_ratio_threshold,
    1.00 as net_debt_ratio_threshold,
    1.00 as cash_to_short_debt_threshold,
    case
        when total_liabilities_yi / nullif(total_assets_yi, 0) > 0.70 then 'red'
        else 'green'
    end as asset_liability_status,
    case
        when (short_term_debt_yi + long_term_debt_yi - cash_yi)
             / nullif(shareholders_equity_yi, 0) > 1.00 then 'red'
        else 'green'
    end as net_debt_status,
    case
        when cash_yi / nullif(short_term_debt_yi, 0) < 1.00 then 'red'
        else 'green'
    end as cash_to_short_debt_status
from projects
