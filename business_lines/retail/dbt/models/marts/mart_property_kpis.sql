-- business_lines/retail/dbt/models/marts/mart_property_kpis.sql
-- 物业 KPI 汇总 (mart). 零售分析的核心指标表.
-- 应用层 UniversalKpiCard + /properties 接口直接消费本表.

{{ config(materialized='table') }}

with props as (
    select * from {{ ref('stg_properties') }}
),

noi_monthly as (
    select * from {{ ref('int_property_noi_monthly') }}
),

lease_status as (
    select * from {{ ref('int_lease_status') }}
),

lease_agg as (
    select
        property_id,
        sum(area_sqm)                                                                  as total_lease_area_sqm,
        sum(annual_base_rent_yuan)                                                     as total_annual_base_rent_yuan,
        sum(case when lease_status = 'urgent_renewal' then area_sqm else 0 end)        as urgent_renewal_area_sqm,
        sum(case when lease_status in ('urgent_renewal','approaching_renewal') then area_sqm else 0 end)
                                                                                       as approaching_renewal_area_sqm,
        count(distinct tenant_id)                                                      as sampled_tenant_count
    from lease_status
    group by property_id
),

final as (
    select
        p.property_id,
        p.property_name,
        p.city,
        p.city_tier,
        p.property_format,
        p.gla_wan_sqm,
        p.noi_wan,
        p.gross_rent_wan,
        p.opex_wan,
        p.vacancy_rate,
        p.collection_rate,
        p.rent_escalation_rate,
        p.foot_traffic_wan_per_month,
        p.total_brands,
        p.wault_years,
        -- 派生 KPI
        round(p.noi_wan * 10000.0 / (p.gla_wan_sqm * 10000.0) / 12.0, 2)
            as efficiency_yuan_per_sqm_per_month,
        round(p.foot_traffic_wan_per_month * 10000.0 / (p.gla_wan_sqm * 10000.0) / 30.0, 4)
            as foot_traffic_per_sqm_per_day,
        -- NOI 率 (Effective gross rent = NOI + OpEx)
        case
            when (p.noi_wan + p.opex_wan) > 0
                then round(p.noi_wan / (p.noi_wan + p.opex_wan), 4)
            else 0
        end                                                                              as noi_margin,
        -- 租约面积覆盖率 (租约总面积 / 总建面)
        round(coalesce(la.total_lease_area_sqm, 0) / nullif(p.gla_wan_sqm * 10000.0, 0), 4)
            as lease_area_coverage,
        -- 临期租约面积占比
        case
            when la.total_lease_area_sqm > 0
                then round(la.approaching_renewal_area_sqm / la.total_lease_area_sqm, 4)
            else 0
        end                                                                              as approaching_renewal_ratio,
        -- 续约压力值 (高=压力)
        la.urgent_renewal_area_sqm                                                       as urgent_renewal_area_sqm
    from props p
    left join noi_monthly n on n.property_id = p.property_id
    left join lease_agg la on la.property_id = p.property_id
)

select * from final
