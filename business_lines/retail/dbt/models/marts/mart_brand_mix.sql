-- business_lines/retail/dbt/models/marts/mart_brand_mix.sql
-- 品牌组合 (mart). 业态级 + 多样性指数.

{{ config(materialized='table') }}

with leases as (
    select * from {{ ref('stg_leases') }}
),

props as (
    select property_id, property_name, total_brands from {{ ref('stg_properties') }}
),

agg as (
    select
        l.property_id,
        l.category,
        count(distinct l.tenant_id)                                      as brand_count,
        sum(l.area_sqm)                                                  as total_area_sqm,
        sum(l.area_sqm * l.monthly_rent_yuan_per_sqm) / nullif(sum(l.area_sqm), 0)
                                                                         as avg_rent_yuan_per_sqm_per_month,
        sum(l.annual_base_rent_yuan)                                     as total_annual_base_rent_yuan
    from leases l
    group by l.property_id, l.category
),

with_share as (
    select
        a.*,
        p.property_name,
        p.total_brands,
        a.total_area_sqm / nullif(sum(a.total_area_sqm) over (partition by a.property_id), 0)
            as area_share
    from agg a
    join props p using (property_id)
),

with_diversity as (
    select
        *,
        -- Shannon entropy, normalized to 0-1 by ln(n_categories).
        -- 多样性 = -sum(p * ln p) / ln(n)
        -1.0 * sum(area_share * ln(nullif(area_share, 0))) over (partition by property_id)
            / nullif(ln(count(*) over (partition by property_id)), 0)
            as brand_diversity_index
    from with_share
)

select
    property_id,
    property_name,
    category,
    brand_count,
    total_area_sqm,
    area_share,
    avg_rent_yuan_per_sqm_per_month,
    total_annual_base_rent_yuan,
    round(brand_diversity_index, 4) as brand_diversity_index,
    total_brands
from with_diversity
