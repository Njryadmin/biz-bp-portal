-- business_lines/industrial/dbt/models/staging/stg_industrial_properties.sql
-- 工业地产主数据 (staging).

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_industrial', 'industrial_properties') }}
),

renamed as (
    select
        property_id,
        property_name,
        property_type,
        city,
        region,
        total_area_sqm,
        leased_area_sqm,
        occupancy_rate,
        avg_rent_yuan_per_sqm_per_month,
        tenant_count,
        cap_rate,
        is_in_logistics_park,
        renewal_rate_12m,
        -- 派生
        case
            when total_area_sqm > 0
                then round(leased_area_sqm / total_area_sqm, 4)
            else 0
        end                                                       as derived_occupancy_rate,
        -- JSON 数组暂保留为 raw,应用层拆
        tenants
    from source
)

select * from renamed
