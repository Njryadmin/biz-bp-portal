-- infra/dbt/models/staging/stg_nbs_house_price.sql
-- Staging model: flattens raw.uploads rows where source='nbs_house_price'.
-- Schema: {city, period, new_home_index_yoy, new_home_index_mom,
--          second_hand_index_yoy, second_hand_index_mom, ...}

{{ config(materialized='view') }}

with src as (

    select
        upload_id,
        filename,
        uploaded_at,
        fetched_at,
        ord     as row_index,
        row     as payload_row
    from raw.uploads u
    cross join lateral jsonb_array_elements(u.payload) with ordinality as p(row, ord)
    where u.source = 'nbs_house_price'
      and u.payload is not null

)

select
    upload_id,
    filename,
    uploaded_at,
    fetched_at,
    row_index,
    coalesce(nullif(payload_row ->> 'city', ''), 'unknown')                                  as city,
    nullif(payload_row ->> 'period', '')                                                     as period_raw,
    case
        when payload_row ->> 'period' ~ '^\d{4}-\d{2}$'
            then (payload_row ->> 'period') || '-01'
        else null
    end                                                                                      as period_date,
    (payload_row ->> 'new_home_index_yoy')::numeric                                          as new_home_index_yoy,
    (payload_row ->> 'new_home_index_mom')::numeric                                          as new_home_index_mom,
    (payload_row ->> 'second_hand_index_yoy')::numeric                                       as second_hand_index_yoy,
    (payload_row ->> 'second_hand_index_mom')::numeric                                       as second_hand_index_mom,
    coalesce((payload_row ->> 'is_fallback')::boolean, false)                                as is_fallback,
    payload_row
from src
