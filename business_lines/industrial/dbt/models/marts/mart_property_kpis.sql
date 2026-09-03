-- business_lines/industrial/dbt/models/marts/mart_property_kpis.sql
-- 物业 KPI 汇总 (mart). 工业地产部的核心指标表.

{{ config(materialized='table') }}

with props as (
    select * from {{ ref('stg_industrial_properties') }}
),

by_type as (
    select
        property_type,
        count(*)                                                as property_count,
        sum(total_area_sqm)                                     as total_area,
        sum(leased_area_sqm)                                     as leased_area,
        avg(occupancy_rate)                                      as avg_occupancy,
        avg(avg_rent_yuan_per_sqm_per_month)                     as avg_rent,
        avg(cap_rate)                                            as avg_cap_rate,
        avg(case when is_in_logistics_park then 1.0 else 0.0 end) as logistics_park_coverage,
        avg(renewal_rate_12m)                                    as avg_renewal_rate,
        sum(tenant_count)                                        as total_tenant_count
    from props
    group by property_type
),

overall as (
    select
        'overall'                                               as property_type,
        count(*)                                                as property_count,
        sum(total_area_sqm)                                     as total_area,
        sum(leased_area_sqm)                                     as leased_area,
        avg(occupancy_rate)                                      as avg_occupancy,
        avg(avg_rent_yuan_per_sqm_per_month)                     as avg_rent,
        avg(cap_rate)                                            as avg_cap_rate,
        avg(case when is_in_logistics_park then 1.0 else 0.0 end) as logistics_park_coverage,
        avg(renewal_rate_12m)                                    as avg_renewal_rate,
        sum(tenant_count)                                        as total_tenant_count
    from props
)

select
    property_type                                            as dimension,
    'type'                                                   as dim_type,
    property_count,
    round(total_area, 0)                                     as total_area,
    round(leased_area, 0)                                    as leased_area,
    round(avg_occupancy, 4)                                  as occupancy_rate,
    round(avg_rent, 2)                                       as avg_rent,
    round(avg_cap_rate, 4)                                   as cap_rate,
    round(logistics_park_coverage, 4)                        as logistics_park_coverage,
    round(avg_renewal_rate, 4)                               as lease_renewal_rate,
    total_tenant_count                                       as warehouse_count_proxy
from by_type
union all
select
    property_type,
    'overall',
    property_count,
    round(total_area, 0),
    round(leased_area, 0),
    round(avg_occupancy, 4),
    round(avg_rent, 2),
    round(avg_cap_rate, 4),
    round(logistics_park_coverage, 4),
    round(avg_renewal_rate, 4),
    total_tenant_count
from overall
