-- business_lines/advisory/dbt/models/staging/stg_advisory_projects.sql
-- 顾问项目主数据 (staging). 从 raw_advisory.advisory_projects 拉取.

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_advisory', 'advisory_projects') }}
),

renamed as (
    select
        project_id,
        client_name,
        industry,
        service_type,
        city,
        land_area_wan_sqm,
        expected_price_wan,
        contract_amount_wan,
        sign_date,
        delivery_date,
        actual_delivery_date,
        lead_consultant,
        team_size,
        outcome,
        renewed,
        nps,
        case when outcome = '采纳' then 1 else 0 end                  as is_adopted,
        case
            when actual_delivery_date is not null and delivery_date is not null then
                date_diff('day', delivery_date, actual_delivery_date)
            else 0
        end                                                           as late_days,
        case
            when actual_delivery_date is not null and delivery_date is not null
                and actual_delivery_date <= delivery_date then 1
            else 0
        end                                                           as on_time_delivery,
        date_diff('day', sign_date, delivery_date)                    as expected_duration_days
    from source
)

select * from renamed
