-- infra/dbt/models/marts/mart_market_pulse.sql
-- Cross-source market pulse: combines the 70-city price index, Lianjia
-- deal volumes, and policy events into a single time-ordered fact table
-- the front-end can render in a unified timeline.
--
-- Columns:
--   event_date      date the event applies to (period or publish date)
--   source          nbs_house_price | lianjia_deals | policy_crawler
--   city            city name (or "全国" for national policies)
--   indicator       short metric name
--   value           numeric value (or NULL for free-text events)
--   text            free-text payload (policy title / content, etc.)
--   level           for policy events: "国家" | "省" | "市"; else NULL

{{ config(materialized='table') }}

with price as (

    select
        period_date                          as event_date,
        'nbs_house_price'                    as source,
        city,
        case
            when new_home_index_yoy is not null then 'new_home_yoy'
            when new_home_index_mom is not null then 'new_home_mom'
            when second_hand_index_yoy is not null then 'second_hand_yoy'
            when second_hand_index_mom is not null then 'second_hand_mom'
        end                                  as indicator,
        coalesce(new_home_index_yoy, new_home_index_mom,
                 second_hand_index_yoy, second_hand_index_mom) as value,
        null::text                           as text,
        null::text                           as level
    from {{ ref('mart_city_house_price') }}
    where period_date is not null

),

deals as (

    select
        period_date                          as event_date,
        'lianjia_deals'                      as source,
        city,
        'deals_count'                        as indicator,
        deals_count::numeric                 as value,
        city || ' ' || district || ' 月度成交'  as text,
        null::text                           as level
    from {{ ref('stg_lianjia_deals') }}
    where period_date is not null
      and deals_count is not null

),

deals_price as (

    select
        period_date                          as event_date,
        'lianjia_deals'                      as source,
        city,
        'avg_price'                          as indicator,
        avg_price::numeric                   as value,
        city || ' ' || district || ' 成交均价'  as text,
        null::text                           as level
    from {{ ref('stg_lianjia_deals') }}
    where period_date is not null
      and avg_price is not null

),

policies as (

    select
        publish_date                         as event_date,
        'policy_crawler'                     as source,
        city,
        'policy_event'                       as indicator,
        null::numeric                        as value,
        title                                as text,
        level
    from {{ ref('stg_policies') }}
    where publish_date is not null

)

select * from price
union all
select * from deals
union all
select * from deals_price
union all
select * from policies
