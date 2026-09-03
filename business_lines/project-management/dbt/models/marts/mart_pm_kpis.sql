-- business_lines/project-management/dbt/models/marts/mart_pm_kpis.sql
-- 项目 KPI 汇总 (mart). 地产项目管理部的核心指标表.

{{ config(materialized='table') }}

with projects as (
    select * from {{ ref('stg_managed_projects') }}
),

by_type as (
    select
        project_type,
        count(*)                                                as project_count,
        sum(contract_value_yi)                                   as contract_value,
        avg(progress_deviation)                                  as avg_progress_deviation,
        avg(cost_deviation)                                      as avg_cost_deviation,
        avg(on_time_milestone_rate)                              as avg_on_time_milestone_rate,
        avg(quality_defect_rate)                                 as avg_quality_defect_rate,
        sum(safety_incidents)                                    as safety_incidents,
        avg(client_score)                                        as avg_client_score,
        avg(case when renewed then 1.0 else 0.0 end)             as renewal_rate
    from projects
    group by project_type
),

overall as (
    select
        'overall'                                               as project_type,
        count(*)                                                as project_count,
        sum(contract_value_yi)                                   as contract_value,
        avg(progress_deviation)                                  as avg_progress_deviation,
        avg(cost_deviation)                                      as avg_cost_deviation,
        avg(on_time_milestone_rate)                              as avg_on_time_milestone_rate,
        avg(quality_defect_rate)                                 as avg_quality_defect_rate,
        sum(safety_incidents)                                    as safety_incidents,
        avg(client_score)                                        as avg_client_score,
        avg(case when renewed then 1.0 else 0.0 end)             as renewal_rate
    from projects
),

by_pm as (
    select
        lead_pm,
        count(*)                                                as project_count,
        sum(contract_value_yi)                                   as total_contract_yi,
        sum(pm_team_size)                                        as total_team_size,
        round(sum(contract_value_yi) * 10000.0 / sum(pm_team_size) / 12.0, 0)
                                                                 as per_pm_output_wan,
        avg(progress_deviation)                                  as avg_progress_deviation,
        avg(client_score)                                        as avg_client_score
    from projects
    group by lead_pm
)

select
    project_type                                              as dimension,
    'type'                                                    as dim_type,
    project_count,
    round(contract_value, 1)                                  as contract_value,
    round(avg_progress_deviation, 4)                          as progress_deviation,
    round(avg_cost_deviation, 4)                              as cost_deviation,
    round(avg_on_time_milestone_rate, 4)                      as on_time_milestone_rate,
    round(avg_quality_defect_rate, 4)                         as quality_defect_rate,
    safety_incidents,
    round(avg_client_score, 1)                                as client_satisfaction,
    round(renewal_rate, 4)                                    as renewal_rate,
    null                                                      as lead_pm,
    null                                                      as per_pm_output
from by_type
union all
select
    project_type,
    'overall',
    project_count,
    round(contract_value, 1),
    round(avg_progress_deviation, 4),
    round(avg_cost_deviation, 4),
    round(avg_on_time_milestone_rate, 4),
    round(avg_quality_defect_rate, 4),
    safety_incidents,
    round(avg_client_score, 1),
    round(renewal_rate, 4),
    null, null
from overall
union all
select
    'by_pm'                                                   as dimension,
    'pm'                                                      as dim_type,
    project_count,
    round(total_contract_yi, 2)                               as contract_value,
    round(avg_progress_deviation, 4)                          as progress_deviation,
    null                                                      as cost_deviation,
    null                                                      as on_time_milestone_rate,
    null                                                      as quality_defect_rate,
    null                                                      as safety_incidents,
    round(avg_client_score, 1)                                as client_satisfaction,
    null                                                      as renewal_rate,
    lead_pm,
    per_pm_output_wan                                         as per_pm_output
from by_pm
