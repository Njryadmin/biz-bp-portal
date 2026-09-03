-- business_lines/residential/dbt/models/intermediate/int_residential_payment_weekly.sql
-- Intermediate: aggregate payment events to project-week grain.
-- Adds cumulative & running metrics consumed by marts.

{{ config(materialized='view') }}

with payments as (

    select * from {{ ref('stg_residential_payments') }}

),

weekly as (

    select
        project_id,
        payment_year,
        payment_week,
        min(payment_date)                            as week_start_date,
        count(distinct payment_id)                   as payment_events,
        sum(amount_cny)                              as weekly_amount_cny,
        sum(commission_cny)                          as weekly_commission_cny,
        sum(channel_fee_cny)                         as weekly_channel_fee_cny
    from payments
    group by 1, 2, 3

),

with_running as (

    select
        *,
        sum(weekly_amount_cny) over (
            partition by project_id
            order by payment_year, payment_week
            rows between unbounded preceding and current row
        ) as cumulative_amount_cny
    from weekly

)

select * from with_running
