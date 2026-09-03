-- business_lines/office-leasing/dbt/models/marts/mart_deal_kpis.sql
-- 成交 KPI 汇总 (mart). 写字楼租赁部的核心指标表.

{{ config(materialized='table') }}

with deals as (
    select * from {{ ref('stg_office_deals') }}
),

by_industry as (
    select
        tenant_industry,
        count(*)                                            as deal_count,
        sum(area_sqm)                                       as deal_area,
        sum(commission_wan)                                 as commission_revenue,
        avg(commission_rate)                                as avg_commission_rate,
        avg(deal_cycle_days)                                as avg_deal_cycle,
        avg(case when is_renewal then 1.0 else 0.0 end)     as renewal_rate,
        avg(case when is_cross_region then 1.0 else 0.0 end) as cross_region_ratio
    from deals
    group by tenant_industry
),

overall as (
    select
        'overall'                                           as tenant_industry,
        count(*)                                            as deal_count,
        sum(area_sqm)                                       as deal_area,
        sum(commission_wan)                                 as commission_revenue,
        avg(commission_rate)                                as avg_commission_rate,
        avg(deal_cycle_days)                                as avg_deal_cycle,
        avg(case when is_renewal then 1.0 else 0.0 end)     as renewal_rate,
        avg(case when is_cross_region then 1.0 else 0.0 end) as cross_region_ratio
    from deals
),

by_broker as (
    select
        broker,
        count(*)                                            as deal_count,
        sum(area_sqm)                                       as total_area,
        sum(commission_wan)                                 as total_commission_wan,
        round(avg(deal_cycle_days), 1)                      as avg_deal_cycle,
        avg(case when is_renewal then 1.0 else 0.0 end)     as renewal_rate
    from deals
    group by broker
)

select
    tenant_industry                                        as dimension,
    'industry'                                             as dim_type,
    deal_count,
    deal_area,
    round(commission_revenue, 1)                           as commission_revenue,
    round(avg_commission_rate, 4)                          as avg_commission_rate,
    round(avg_deal_cycle, 1)                               as avg_deal_cycle,
    round(renewal_rate, 4)                                 as renewal_rate,
    round(cross_region_ratio, 4)                           as cross_region_ratio,
    null                                                   as broker,
    null                                                   as total_commission_wan
from by_industry
union all
select
    tenant_industry,
    'overall',
    deal_count,
    deal_area,
    round(commission_revenue, 1),
    round(avg_commission_rate, 4),
    round(avg_deal_cycle, 1),
    round(renewal_rate, 4),
    round(cross_region_ratio, 4),
    null, null
from overall
union all
select
    'by_broker'                                            as dimension,
    'broker'                                               as dim_type,
    deal_count,
    total_area,
    round(total_commission_wan, 1)                         as commission_revenue,
    null                                                   as avg_commission_rate,
    avg_deal_cycle                                          as avg_deal_cycle,
    renewal_rate                                            as renewal_rate,
    null                                                   as cross_region_ratio,
    broker,
    total_commission_wan
from by_broker
