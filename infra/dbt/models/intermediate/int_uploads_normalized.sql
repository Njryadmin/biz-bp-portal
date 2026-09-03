-- infra/dbt/models/intermediate/int_uploads_normalized.sql
-- Normalize uploaded CSV rows: type-cast, add derived columns, drop junk.

{{ config(materialized='view') }}

with base as (

    select *
    from {{ ref('stg_csv_uploads') }}
    where project_id is not null

),

typed as (

    select
        upload_id,
        filename,
        upload_type,
        uploaded_at,
        project_id,
        business_date,
        coalesce(sales_amount,   0)::numeric as sales_amount,
        coalesce(payment_amount, 0)::numeric as payment_amount,
        coalesce(commission,     0)::numeric as commission,
        coalesce(channel_fee,    0)::numeric as channel_fee
    from base

)

select
    upload_id,
    filename,
    upload_type,
    uploaded_at,
    project_id,
    business_date,
    sales_amount,
    payment_amount,
    commission,
    channel_fee,

    -- Derived KPIs
    sales_amount - commission - channel_fee                     as net_revenue,
    case
        when sales_amount > 0 then commission / sales_amount
        else null
    end                                                          as commission_rate,
    case
        when sales_amount > 0 then channel_fee / sales_amount
        else null
    end                                                          as channel_fee_rate,
    case
        when payment_amount >= sales_amount then 'fully_paid'
        when payment_amount > 0              then 'partially_paid'
        else                                      'unpaid'
    end                                                          as payment_status
from typed
