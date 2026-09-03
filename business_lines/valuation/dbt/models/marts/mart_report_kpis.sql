-- business_lines/valuation/dbt/models/marts/mart_report_kpis.sql
-- 报告 KPI 汇总 (mart). 估价部的核心指标表.
-- 应用层 UniversalKpiCard + /reports 接口直接消费本表.

{{ config(materialized='table') }}

with reports as (
    select * from {{ ref('stg_valuation_reports') }}
),

by_purpose as (
    select
        purpose,
        count(*)                                                        as report_count,
        sum(valuation_amount_wan)                                      as valuation_amount_wan,
        sum(fee_yuan)                                                   as total_fee_yuan,
        avg(fee_yuan)                                                   as avg_fee_yuan,
        avg(valuation_bias_rate)                                        as avg_bias_rate,
        avg(collection_days)                                            as avg_collection_days,
        avg(on_time_delivery)                                           as avg_on_time_delivery,
        avg(is_revised)                                                 as revision_rate,
        avg(client_score)                                               as avg_client_score,
        avg(case when is_repeat_client then 1.0 else 0.0 end)           as repeat_client_rate
    from reports
    group by purpose
),

overall as (
    select
        'overall' as purpose,
        count(*)                                                        as report_count,
        sum(valuation_amount_wan)                                      as valuation_amount_wan,
        sum(fee_yuan)                                                   as total_fee_yuan,
        avg(fee_yuan)                                                   as avg_fee_yuan,
        avg(valuation_bias_rate)                                        as avg_bias_rate,
        avg(collection_days)                                            as avg_collection_days,
        avg(on_time_delivery)                                           as avg_on_time_delivery,
        avg(is_revised)                                                 as revision_rate,
        avg(client_score)                                               as avg_client_score,
        avg(case when is_repeat_client then 1.0 else 0.0 end)           as repeat_client_rate
    from reports
),

by_appraiser as (
    select
        appraiser,
        appraiser_level,
        count(*)                                                        as report_count,
        sum(fee_yuan)                                                   as total_fee_yuan,
        round(sum(fee_yuan) / 10000.0, 1)                              as per_capita_output_wan,
        avg(valuation_bias_rate)                                        as avg_bias_rate,
        avg(client_score)                                               as avg_client_score
    from reports
    group by appraiser, appraiser_level
)

select
    purpose,
    report_count,
    round(valuation_amount_wan, 0)                                     as valuation_amount_wan,
    round(total_fee_yuan, 0)                                           as total_fee_yuan,
    round(avg_fee_yuan, 0)                                             as avg_report_size_yuan,
    round(avg_bias_rate, 4)                                            as valuation_bias_rate,
    round(avg_collection_days, 1)                                      as collection_days,
    round(avg_on_time_delivery, 4)                                     as on_time_delivery_rate,
    round(revision_rate, 4)                                            as report_revision_rate,
    round(avg_client_score, 1)                                         as client_satisfaction,
    round(repeat_client_rate, 4)                                       as repeat_client_rate,
    null                                                               as appraiser,
    null                                                               as appraiser_level,
    null                                                               as per_capita_output_wan
from by_purpose
union all
select
    purpose,
    report_count,
    round(valuation_amount_wan, 0),
    round(total_fee_yuan, 0),
    round(avg_fee_yuan, 0),
    round(avg_bias_rate, 4),
    round(avg_collection_days, 1),
    round(avg_on_time_delivery, 4),
    round(revision_rate, 4),
    round(avg_client_score, 1),
    round(repeat_client_rate, 4),
    null, null, null
from overall
union all
select
    'by_appraiser'                                                     as purpose,
    report_count,
    null                                                               as valuation_amount_wan,
    round(total_fee_yuan, 0)                                           as total_fee_yuan,
    null                                                               as avg_report_size_yuan,
    round(avg_bias_rate, 4)                                            as valuation_bias_rate,
    null                                                               as collection_days,
    null                                                               as on_time_delivery_rate,
    null                                                               as report_revision_rate,
    round(avg_client_score, 1)                                         as client_satisfaction,
    null                                                               as repeat_client_rate,
    appraiser,
    appraiser_level,
    per_capita_output_wan                                              as per_capita_output_wan
from by_appraiser
