-- business_lines/advisory/dbt/models/marts/mart_project_kpis.sql
-- 项目 KPI 汇总 (mart). 顾问部的核心指标表.

{{ config(materialized='table') }}

with projects as (
    select * from {{ ref('stg_advisory_projects') }}
),

by_service as (
    select
        service_type,
        count(*)                                            as project_count,
        sum(contract_amount_wan)                            as contract_amount_wan,
        avg(contract_amount_wan)                            as avg_contract_wan,
        avg(case when renewed then 1.0 else 0.0 end)        as renewal_rate,
        avg(case when is_adopted then 1.0 else 0.0 end)     as project_success_rate,
        avg(expected_duration_days)                         as avg_project_duration,
        avg(nps)                                            as avg_nps,
        avg(on_time_delivery)                               as avg_on_time_delivery
    from projects
    group by service_type
),

overall as (
    select
        'overall'                                           as service_type,
        count(*)                                            as project_count,
        sum(contract_amount_wan)                            as contract_amount_wan,
        avg(contract_amount_wan)                            as avg_contract_wan,
        avg(case when renewed then 1.0 else 0.0 end)        as renewal_rate,
        avg(case when is_adopted then 1.0 else 0.0 end)     as project_success_rate,
        avg(expected_duration_days)                         as avg_project_duration,
        avg(nps)                                            as avg_nps,
        avg(on_time_delivery)                               as avg_on_time_delivery
    from projects
),

by_consultant as (
    select
        lead_consultant,
        count(*)                                            as project_count,
        sum(contract_amount_wan)                            as total_contract_wan,
        round(sum(contract_amount_wan) / sum(team_size), 1) as per_consultant_output_wan,
        avg(nps)                                            as avg_nps,
        avg(case when is_adopted then 1.0 else 0.0 end)     as adopted_rate
    from projects
    group by lead_consultant
)

select
    service_type,
    project_count,
    round(contract_amount_wan, 0)                          as contract_amount,
    round(avg_contract_wan, 1)                             as avg_contract,
    round(renewal_rate, 4)                                 as renewal_rate,
    round(project_success_rate, 4)                         as project_success_rate,
    round(avg_project_duration, 1)                         as avg_project_duration,
    round(avg_nps, 1)                                      as client_nps,
    round(avg_on_time_delivery, 4)                         as on_time_delivery_rate,
    null                                                   as lead_consultant,
    null                                                   as per_consultant_output,
    null                                                   as avg_nps_consultant
from by_service
union all
select
    service_type,
    project_count,
    round(contract_amount_wan, 0),
    round(avg_contract_wan, 1),
    round(renewal_rate, 4),
    round(project_success_rate, 4),
    round(avg_project_duration, 1),
    round(avg_nps, 1),
    round(avg_on_time_delivery, 4),
    null, null, null
from overall
union all
select
    'by_consultant'                                        as service_type,
    project_count,
    round(total_contract_wan, 0)                          as contract_amount,
    null                                                   as avg_contract,
    null                                                   as renewal_rate,
    round(adopted_rate, 4)                                 as project_success_rate,
    null                                                   as avg_project_duration,
    round(avg_nps, 1)                                      as client_nps,
    null                                                   as on_time_delivery_rate,
    lead_consultant,
    per_consultant_output_wan                              as per_consultant_output,
    null                                                   as avg_nps_consultant
from by_consultant
