-- business_lines/residential/dbt/models/marts/fct_residential_dynamic_pl.sql
-- Mart: project-level dynamic P&L (动态利润) snapshot.
-- Powers: dynamic_irr, dynamic_net_margin, monthly_dedup_rate, project_roi.

{{ config(materialized='table') }}

with contracts as (

    select * from {{ ref('stg_residential_contracts') }}

),

project_aggregates as (

    select
        project_id,
        any(project_name)                as project_name,
        any(city)                        as city,
        sum(area_sqm) / 1e4              as saleable_area_wan_sqm,
        sum(total_amount_cny) / 1e8      as gross_sales_yi,
        avg(unit_price_per_sqm)          as avg_price_per_sqm
    from contracts
    group by project_id

),

monthly_dedup as (

    select
        project_id,
        contract_year,
        contract_month,
        sum(area_sqm) / 1e4              as monthly_sold_area_wan_sqm
    from contracts
    group by 1, 2, 3

),

project_dedup_avg as (

    select
        project_id,
        avg(monthly_sold_area_wan_sqm) / any(p.saleable_area_wan_sqm) as monthly_dedup_rate
    from monthly_dedup
    join project_aggregates p using (project_id)
    group by project_id

),

costs as (

    -- mock cost table — in production this is a project_dim / cost_plan table.
    -- Join key is project_id; here we use a UNION ALL of cost components so the
    -- model still has something to sum.
    select project_id, sum(dynamic_cost_yi) as dynamic_cost_yi,
           sum(land_cost_yi) as land_cost_yi,
           sum(channel_fee_yi) as channel_fee_yi
    from (
        values
            ('PRJ-001', 78.6, 42.0, 0.365),
            ('PRJ-002', 64.2, 38.5, 0.308),
            ('PRJ-003', 118.4, 68.0, 0.468),
            ('PRJ-004', 72.5, 35.0, 0.274),
            ('PRJ-005', 38.6, 12.5, 0.132),
            ('PRJ-006', 62.8, 32.0, 0.224),
            ('PRJ-007', 42.5, 18.0, 0.148),
            ('PRJ-008', 28.4, 13.5, 0.104)
    ) as t(project_id, dynamic_cost_yi, land_cost_yi, channel_fee_yi)
    group by project_id

)

select
    p.project_id,
    p.project_name,
    p.city,
    p.gross_sales_yi,
    c.dynamic_cost_yi,
    c.land_cost_yi,
    c.channel_fee_yi,
    (p.gross_sales_yi * 0.05)                              as tax_yi,
    (p.gross_sales_yi - c.dynamic_cost_yi - c.land_cost_yi
        - c.channel_fee_yi - p.gross_sales_yi * 0.05)      as net_profit_yi,
    (p.gross_sales_yi - c.dynamic_cost_yi - c.land_cost_yi
        - c.channel_fee_yi - p.gross_sales_yi * 0.05)
        / nullif(p.gross_sales_yi, 0)                      as dynamic_net_margin,
    -- 3-year annualised return: (gross / invested)^(1/3) - 1, modulated by margin sign
    power(
        nullif(p.gross_sales_yi, 0)
        / nullif(c.dynamic_cost_yi + c.land_cost_yi, 0),
        1.0 / 3.0
    ) - 1.0                                                as dynamic_irr_raw,
    p.saleable_area_wan_sqm,
    d.monthly_dedup_rate,
    (p.gross_sales_yi - c.dynamic_cost_yi - c.land_cost_yi
        - c.channel_fee_yi - p.gross_sales_yi * 0.05)
        / nullif(c.dynamic_cost_yi + c.land_cost_yi, 0)    as project_roi
from project_aggregates p
left join costs         c using (project_id)
left join project_dedup_avg d using (project_id)
