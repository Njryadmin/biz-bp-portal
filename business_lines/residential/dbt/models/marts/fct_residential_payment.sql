-- business_lines/residential/dbt/models/marts/fct_residential_payment.sql
-- Mart: project-month payment, commission, channel-fee.
-- Powers: payment_completion, payment_vs_plan, channel_fee_ratio.

{{ config(materialized='table') }}

with payments as (

    select * from {{ ref('stg_residential_payments') }}

),

project_month as (

    select
        project_id,
        payment_year,
        payment_month,
        sum(amount_cny) / 1e8          as monthly_actual_yi,
        sum(commission_cny) / 1e8      as monthly_commission_yi,
        sum(channel_fee_cny) / 1e8     as monthly_channel_fee_yi
    from payments
    group by 1, 2, 3

),

-- mock monthly plan: 1.05x of the previous month actual (slight over-commit).
-- In production this joins to a plan / budget table.
with_plan as (

    select
        project_id,
        payment_year,
        payment_month,
        monthly_actual_yi,
        monthly_commission_yi,
        monthly_channel_fee_yi,
        monthly_actual_yi * 1.05 as monthly_plan_yi
    from project_month

),

with_cumulative as (

    select
        *,
        sum(monthly_actual_yi) over (partition by project_id order by payment_year, payment_month) as cumulative_actual_yi,
        sum(monthly_plan_yi)   over (partition by project_id order by payment_year, payment_month) as cumulative_plan_yi
    from with_plan

)

select
    project_id,
    payment_year,
    payment_month,
    make_date(payment_year, payment_month, 1)            as month_start_date,
    monthly_plan_yi,
    monthly_actual_yi,
    monthly_commission_yi,
    monthly_channel_fee_yi,
    cumulative_plan_yi,
    cumulative_actual_yi,
    cumulative_actual_yi / nullif(cumulative_plan_yi, 0)     as payment_completion,
    monthly_actual_yi   / nullif(monthly_plan_yi,   0)       as payment_vs_plan,
    monthly_channel_fee_yi
        / nullif(monthly_actual_yi + monthly_commission_yi, 0) as channel_fee_ratio
from with_cumulative
