-- business_lines/office-leasing/dbt/models/staging/stg_office_deals.sql
-- 写字楼成交主数据 (staging).

{{ config(materialized='view') }}

with source as (
    select * from {{ source('raw_office_leasing', 'office_deals') }}
),

renamed as (
    select
        deal_id,
        building_name,
        building_grade,
        region,
        tenant_industry,
        tenant_name,
        area_sqm,
        monthly_rent_yuan_per_sqm,
        lease_term_years,
        sign_date,
        commission_rate,
        deal_cycle_days,
        broker,
        is_renewal,
        is_cross_region,
        client_hq,
        -- 派生
        area_sqm * monthly_rent_yuan_per_sqm                              as monthly_rent_total_yuan,
        area_sqm * monthly_rent_yuan_per_sqm * 12                        as annual_rent_yuan,
        area_sqm * monthly_rent_yuan_per_sqm * 12 * lease_term_years
            * commission_rate                                            as commission_yuan,
        round(
            area_sqm * monthly_rent_yuan_per_sqm * 12 * lease_term_years
            * commission_rate / 10000.0, 1
        )                                                               as commission_wan
    from source
)

select * from renamed
