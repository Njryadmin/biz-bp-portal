-- infra/dbt/models/staging/stg_policies.sql
-- Staging model for the policy crawler. Each row is one real-estate
-- policy event.
--
-- Schema: {policy_id, title, publish_date, city, level, content, source_url}

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
    where u.source = 'policy_crawler'
      and u.payload is not null

)

select
    upload_id,
    filename,
    uploaded_at,
    fetched_at,
    row_index,
    coalesce(nullif(payload_row ->> 'policy_id', ''), upload_id || '-' || row_index::text) as policy_id,
    nullif(payload_row ->> 'title', '')                                                    as title,
    case
        when payload_row ->> 'publish_date' ~ '^\d{4}-\d{2}-\d{2}$'
            then (payload_row ->> 'publish_date')::date
        else null
    end                                                                                    as publish_date,
    coalesce(nullif(payload_row ->> 'city', ''), '全国')                                   as city,
    coalesce(nullif(payload_row ->> 'level', ''), '国家')                                  as level,
    nullif(payload_row ->> 'content', '')                                                  as content,
    nullif(payload_row ->> 'source_url', '')                                               as source_url,
    coalesce((payload_row ->> 'is_fallback')::boolean, false)                              as is_fallback,
    payload_row
from src
