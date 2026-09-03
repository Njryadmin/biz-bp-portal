-- business_lines/retail-leasing/dbt/models/marts/fct_retail_leasing.sql
-- 零售租赁与市场报告 核心 mart: 商铺级 deal / benchmark / vacancy / commission.
-- 应用层 UniversalKpiCard + /properties + /market-benchmark + /vacancy-alerts 全部消费本表.

{{ config(materialized='table') }}

with properties as (

    select
        property_id,
        property_name,
        city,
        city_tier,
        area_district,
        gla_sqm,
        deal_rent_yuan_per_sqm_per_month,
        benchmark_rent_yuan_per_sqm_per_month,
        vacancy_rate,
        owner,
        tenant,
        owner_vacancy_days,
        quarterly_reports_published,
        brand_entry_rate,
        renewal_rate,
        commission_revenue_wan,
        -- 派生指标
        round(1.0 - vacancy_rate, 4)                                            as occupancy_rate,
        round(
            (deal_rent_yuan_per_sqm_per_month - benchmark_rent_yuan_per_sqm_per_month)
            / nullif(benchmark_rent_yuan_per_sqm_per_month, 0)
            , 4
        )                                                                       as benchmark_gap_pct,
        round(deal_rent_yuan_per_sqm_per_month * gla_sqm, 0)                     as monthly_deal_revenue_yuan
    from {{ ref('stg_retail_leasing_properties') }}

)

select * from properties
