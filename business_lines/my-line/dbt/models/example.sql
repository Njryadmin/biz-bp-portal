-- business_lines/_template/dbt/models/example.sql
-- Template DBT model. Copy to business_lines/<line_id>/dbt/models/<name>.sql

{{ config(materialized='table') }}

-- Replace with real source tables for this business line
with source as (

    select 1 as id, cast('2024-01-01' as date) as dt, 100.0 as value
    union all
    select 2, cast('2024-01-02' as date), 200.0
    union all
    select 3, cast('2024-01-03' as date), 150.0

)

select
    id,
    dt,
    value
from source
