-- business_lines/residential/dbt/models/staging/stg_residential_contracts.sql
-- Staging model: residential sales contracts (签约) landing from raw.
-- Source: {{ var('residential_source') }}.raw_contracts  (overridable via DBT var)
-- In MVP we use a small in-model seed; in production this is replaced by
--   select * from {{ source('residential', 'contracts') }}.

{{ config(materialized='view') }}

with source as (

    select
        contract_id,
        project_id,
        project_name,
        city,
        contract_date,
        area_sqm,
        unit_price_per_sqm,
        total_amount_cny,
        payment_method,
        customer_type
    from {{ var('residential_source', 'raw_residential') }}.contracts

),

renamed as (

    select
        contract_id,
        project_id,
        project_name,
        city,
        cast(contract_date as date)             as contract_date,
        cast(area_sqm as numeric(18, 2))        as area_sqm,
        cast(unit_price_per_sqm as numeric(18, 2)) as unit_price_per_sqm,
        cast(total_amount_cny as numeric(20, 2))   as total_amount_cny,
        lower(trim(payment_method))             as payment_method,
        lower(trim(customer_type))              as customer_type,
        year(contract_date)                     as contract_year,
        month(contract_date)                    as contract_month
    from source

)

select * from renamed
