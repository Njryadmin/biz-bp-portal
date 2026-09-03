-- business_lines/valuation/dbt/models/staging/stg_valuation_reports.sql
-- 估价报告主数据 (staging). 从 raw_valuation.valuation_reports 拉取,做轻度清洗 + 重命名.

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_valuation', 'valuation_reports') }}
),

renamed as (
    select
        report_id,
        client_name,
        purpose,
        city,
        property_type,
        subject_area_sqm,
        valuation_amount_wan,
        fee_yuan,
        issue_date,
        due_date,
        actual_delivery_date,
        collection_date,
        appraiser,
        appraiser_level,
        revaluation_amount_wan,
        on_time,
        revision_count,
        client_score,
        is_repeat_client,
        -- 派生指标
        case
            when valuation_amount_wan > 0
                then round(
                    abs(coalesce(revaluation_amount_wan, valuation_amount_wan) - valuation_amount_wan)
                    / valuation_amount_wan, 4)
            else 0
        end                                                           as valuation_bias_rate,
        case
            when actual_delivery_date is not null and due_date is not null then
                case when actual_delivery_date <= due_date then 1 else 0 end
            else 0
        end                                                           as on_time_delivery,
        case
            when actual_delivery_date is not null and due_date is not null then
                greatest(0, date_diff('day', due_date, actual_delivery_date))
            else 0
        end                                                           as late_days,
        case
            when collection_date is not null and issue_date is not null then
                date_diff('day', issue_date, collection_date)
            else 0
        end                                                           as collection_days,
        case when revision_count > 0 then 1 else 0 end                as is_revised,
        fee_yuan / 10000.0                                            as fee_wan
    from source
)

select * from renamed
