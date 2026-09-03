-- business_lines/retail/dbt/models/staging/stg_tenants.sql
-- 租户/品牌主数据 (staging).

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_retail', 'tenants') }}
),

renamed as (
    select
        tenant_id,
        tenant_name,
        brand_name_en,
        category,
        tier                       as brand_tier,
        country_of_origin,
        first_open_year
    from source
)

select * from renamed
