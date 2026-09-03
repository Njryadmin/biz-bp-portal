-- business_lines/retail/dbt/models/staging/stg_properties.sql
-- 物业主数据 (staging). 从 raw_retail.properties 拉取,做轻度清洗 + 重命名.

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_retail', 'properties') }}
),

renamed as (
    select
        property_id,
        name                  as property_name,
        name_en               as property_name_en,
        city,
        city_tier,
        format                as property_format,
        format_en             as property_format_en,
        gla_wan_sqm           as gla_wan_sqm,
        noi_wan               as noi_wan,
        gross_rent_wan        as gross_rent_wan,
        opex_wan              as opex_wan,
        vacancy_rate          as vacancy_rate,
        collection_rate       as collection_rate,
        rent_escalation_rate  as rent_escalation_rate,
        foot_traffic_wan_per_month as foot_traffic_wan_per_month,
        total_brands          as total_brands,
        weighted_lease_remaining_years as wault_years
    from source
)

select * from renamed
