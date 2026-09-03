-- infra/dbt/models/staging/stg_csv_uploads.sql
-- Flattens raw.uploads.payload (jsonb) into typed columns.
-- Used by the API + dbt for incremental processing of uploaded files.

{{ config(materialized='view') }}

with src as (

    select
        upload_id,
        filename,
        upload_type,
        uploaded_at,
        ord        as row_index,
        row        as payload_row
    from raw.uploads u
    cross join lateral jsonb_array_elements(u.payload) with ordinality as p(row, ord)
    where u.upload_type in ('excel', 'csv')
      and u.payload is not null

)

select
    upload_id,
    filename,
    upload_type,
    uploaded_at,
    row_index,
    nullif(payload_row ->> 'project_id',     '')                 as project_id,
    nullif(payload_row ->> 'date',           '')::date          as business_date,
    nullif(payload_row ->> 'sales_amount',   '')::numeric       as sales_amount,
    nullif(payload_row ->> 'payment_amount', '')::numeric       as payment_amount,
    nullif(payload_row ->> 'commission',     '')::numeric       as commission,
    nullif(payload_row ->> 'channel_fee',    '')::numeric       as channel_fee,
    payload_row
from src
