-- business_lines/retail/dbt/models/intermediate/int_property_noi_monthly.sql
-- 物业月度 NOI 推演 (intermediate).
-- 维度: property_id x month. 简化模型:NOI 在年内按月均匀分布.

{{ config(materialized='view') }}

with props as (
    select * from {{ ref('stg_properties') }}
),

unrolled as (
    select
        p.property_id,
        p.property_name,
        p.city,
        p.property_format,
        p.gla_wan_sqm,
        p.noi_wan,
        p.gross_rent_wan,
        p.opex_wan,
        p.vacancy_rate,
        p.collection_rate,
        p.rent_escalation_rate,
        p.foot_traffic_wan_per_month,
        -- 月度切片: NOI / 12
        round(p.noi_wan / 12.0, 2)                       as noi_monthly_wan,
        round(p.gross_rent_wan / 12.0, 2)                as gross_rent_monthly_wan,
        round(p.opex_wan / 12.0, 2)                      as opex_monthly_wan,
        -- 月度坪效
        round(p.noi_wan * 10000.0 / p.gla_wan_sqm / 12.0, 2) as efficiency_yuan_per_sqm_per_month,
        -- 客流坪效
        round(p.foot_traffic_wan_per_month * 10000.0 / (p.gla_wan_sqm * 10000.0) / 30.0, 4) as foot_traffic_per_sqm_per_day
    from props p
)

select * from unrolled
