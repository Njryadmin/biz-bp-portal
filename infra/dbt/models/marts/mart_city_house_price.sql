-- infra/dbt/models/marts/mart_city_house_price.sql
-- City-level rollup of 70-city house-price index. One row per
-- (city, period). Used by the market-pulse charts.

{{ config(materialized='table') }}

with ranked as (

    select
        city,
        period_date,
        new_home_index_yoy,
        new_home_index_mom,
        second_hand_index_yoy,
        second_hand_index_mom,
        fetched_at,
        row_number() over (
            partition by city, period_date
            order by fetched_at desc nulls last, uploaded_at desc
        ) as rn
    from {{ ref('stg_nbs_house_price') }}
    where city is not null
      and period_date is not null

)

select
    city,
    period_date,
    avg(new_home_index_yoy)            as new_home_index_yoy,
    avg(new_home_index_mom)            as new_home_index_mom,
    avg(second_hand_index_yoy)         as second_hand_index_yoy,
    avg(second_hand_index_mom)         as second_hand_index_mom,
    max(fetched_at)                    as last_fetched_at
from ranked
where rn = 1
group by city, period_date
order by period_date desc, city
