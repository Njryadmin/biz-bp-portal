-- business_lines/investment/dbt/models/marts/mart_fund_kpis.sql
-- 基金 KPI 汇总 (mart). 地产投资部的核心指标表.

{{ config(materialized='table') }}

with funds as (
    select * from {{ ref('stg_funds') }}
),

by_strategy as (
    select
        strategy,
        count(*)                                                as fund_count,
        sum(aum_yi)                                             as aum,
        sum(project_count)                                      as portfolio_count,
        sum(exit_count)                                         as exit_count,
        avg(mgmt_fee_rate)                                      as avg_mgmt_fee_rate,
        sum(mgmt_fee_revenue_yi)                                as total_mgmt_fee_revenue_yi,
        avg(weighted_irr)                                       as avg_irr,
        sum(dry_powder_yi)                                      as dry_powder,
        avg(capital_called_rate)                                as avg_capital_called,
        sum(distributed_yi)                                     as realized_return,
        sum(committed_yi - called_yi - distributed_yi)           as unrealized_gain_proxy,
        avg(avg_hold_years)                                     as avg_hold_period
    from funds
    group by strategy
),

overall as (
    select
        'overall'                                               as strategy,
        count(*)                                                as fund_count,
        sum(aum_yi)                                             as aum,
        sum(project_count)                                      as portfolio_count,
        sum(exit_count)                                         as exit_count,
        avg(mgmt_fee_rate)                                      as avg_mgmt_fee_rate,
        sum(mgmt_fee_revenue_yi)                                as total_mgmt_fee_revenue_yi,
        sum(weighted_irr * called_yi) / nullif(sum(called_yi), 0) as avg_irr,
        sum(dry_powder_yi)                                      as dry_powder,
        sum(called_yi) / nullif(sum(committed_yi), 0)           as avg_capital_called,
        sum(distributed_yi)                                     as realized_return,
        sum(committed_yi - called_yi - distributed_yi)           as unrealized_gain_proxy,
        avg(avg_hold_years)                                     as avg_hold_period
    from funds
)

select
    strategy,
    fund_count,
    round(aum, 1)                                              as aum,
    portfolio_count,
    exit_count,
    round(avg_mgmt_fee_rate, 4)                                as mgmt_fee_rate,
    round(total_mgmt_fee_revenue_yi, 3)                        as mgmt_fee_revenue_yi,
    round(avg_irr, 4)                                          as project_irr,
    round(dry_powder, 1)                                       as dry_powder,
    round(avg_capital_called, 4)                               as capital_called,
    round(realized_return, 1)                                  as realized_return,
    round(unrealized_gain_proxy, 1)                            as unrealized_gain,
    round(avg_hold_period, 1)                                  as avg_hold_period
from by_strategy
union all
select
    strategy,
    fund_count,
    round(aum, 1),
    portfolio_count,
    exit_count,
    round(avg_mgmt_fee_rate, 4),
    round(total_mgmt_fee_revenue_yi, 3),
    round(avg_irr, 4),
    round(dry_powder, 1),
    round(avg_capital_called, 4),
    round(realized_return, 1),
    round(unrealized_gain_proxy, 1),
    round(avg_hold_period, 1)
from overall
