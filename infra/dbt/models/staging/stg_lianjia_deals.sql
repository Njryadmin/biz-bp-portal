-- infra/dbt/models/staging/stg_lianjia_deals.sql
-- Staging model for Lianjia public deal data.
-- Schema: {city, district, period, avg_price, deals_count, ...}

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
    where u.source = 'lianjia_deals'
      and u.payload is not null

)

select
    upload_id,
    filename,
    uploaded_at,
    fetched_at,
    row_index,
    coalesce(nullif(payload_row ->> 'city', ''), 'unknown')                as city,
    coalesce(nullif(payload_row ->> 'district', ''), 'unknown')            as district,
    nullif(payload_row ->> 'period', '')                                   as period_raw,
    case
        when payload_row ->> 'period' ~ '^\d{4}-\d{2}$'
            then (payload_row ->> 'period') || '-01'
        else null
    end                                                                    as period_date,
    (payload_row ->> 'avg_price')::numeric                                 as avg_price,
    (payload_row ->> 'deals_count')::int                                   as deals_count,
    coalesce((payload_row ->> 'is_fallback')::boolean, false)              as is_fallback,
    payload_row
from src
