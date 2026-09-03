-- business_lines/residential/dbt/models/staging/stg_residential_payments.sql
-- Staging model: residential payment (回款) events landing from raw.
-- Source: {{ var('residential_source') }}.raw_payments

{{ config(materialized='view') }}

with source as (

    select
        payment_id,
        project_id,
        contract_id,
        payment_date,
        amount_cny,
        payment_type,         -- down_payment / mortgage / final / retention
        channel,              -- bank / developer / third_party
        commission_cny,
        channel_fee_cny
    from {{ var('residential_source', 'raw_residential') }}.payments

),

renamed as (

    select
        payment_id,
        project_id,
        contract_id,
        cast(payment_date as date)            as payment_date,
        cast(amount_cny as numeric(20, 2))     as amount_cny,
        lower(trim(payment_type))              as payment_type,
        lower(trim(channel))                   as channel,
        cast(commission_cny as numeric(20, 2)) as commission_cny,
        cast(channel_fee_cny as numeric(20, 2)) as channel_fee_cny,
        year(payment_date)                     as payment_year,
        month(payment_date)                    as payment_month,
        date_trunc('week', payment_date)::date as payment_week
    from source

)

select * from renamed
