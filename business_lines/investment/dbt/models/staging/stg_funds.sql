-- business_lines/investment/dbt/models/staging/stg_funds.sql
-- 基金/项目主数据 (staging).

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_investment', 'funds') }}
),

renamed as (
    select
        fund_id,
        fund_name,
        strategy,
        vintage,
        aum_yi,
        committed_yi,
        called_yi,
        distributed_yi,
        nav_yi,
        dry_powder_yi,
        mgmt_fee_rate,
        project_count,
        exit_count,
        weighted_irr,
        avg_hold_years,
        -- 派生
        case
            when called_yi > 0
                then round((nav_yi + distributed_yi) / called_yi, 4)
            else 0
        end                                                       as tvpi,
        case
            when called_yi > 0
                then round(distributed_yi / called_yi, 4)
            else 0
        end                                                       as dpi,
        case
            when committed_yi > 0
                then round(called_yi / committed_yi, 4)
            else 0
        end                                                       as capital_called_rate,
        round(aum_yi * mgmt_fee_rate, 3)                          as mgmt_fee_revenue_yi
    from source
)

select * from renamed
