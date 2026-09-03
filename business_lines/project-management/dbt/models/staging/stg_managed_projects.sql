-- business_lines/project-management/dbt/models/staging/stg_managed_projects.sql
-- 代建项目主数据 (staging).

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_pm', 'managed_projects') }}
),

renamed as (
    select
        project_id,
        project_name,
        project_type,
        client_name,
        city,
        contract_value_yi,
        start_date,
        planned_end_date,
        actual_progress_pct,
        planned_progress_pct,
        actual_cost_wan,
        budgeted_cost_wan,
        lead_pm,
        pm_team_size,
        milestones_total,
        milestones_on_time,
        quality_defects,
        safety_incidents,
        client_score,
        renewed,
        -- 派生
        round(actual_progress_pct - planned_progress_pct, 4) as progress_deviation,
        case
            when budgeted_cost_wan > 0
                then round((actual_cost_wan - budgeted_cost_wan) / budgeted_cost_wan, 4)
            else 0
        end                                                       as cost_deviation,
        case
            when milestones_total > 0
                then round(milestones_on_time * 1.0 / milestones_total, 4)
            else 0
        end                                                       as on_time_milestone_rate,
        case
            when milestones_total > 0
                then round(quality_defects * 1.0 / milestones_total, 4)
            else 0
        end                                                       as quality_defect_rate
    from source
)

select * from renamed
