-- business_lines/retail/dbt/models/intermediate/int_lease_status.sql
-- 租约状态推演 (intermediate).
-- 关键派生: 剩余年限是否 < 2 年(进入"临到期")、< 1 年(进入"紧迫续约").

{{ config(materialized='view') }}

with leases as (
    select * from {{ ref('stg_leases') }}
),

flagged as (
    select
        lease_id,
        property_id,
        tenant_id,
        tenant_name,
        category,
        area_sqm,
        term_years,
        years_elapsed,
        years_remaining,
        monthly_rent_yuan_per_sqm,
        annual_base_rent_yuan,
        rent_escalation,
        case
            when years_remaining <= 0 then 'expired'
            when years_remaining <= 1 then 'urgent_renewal'
            when years_remaining <= 2 then 'approaching_renewal'
            else 'active'
        end                                                       as lease_status,
        -- 临期风险打分: 剩余年限越短 + 面积越大,风险越高
        case
            when years_remaining > 0 then
                round(area_sqm / nullif(years_remaining, 0), 2)
            else null
        end                                                       as renewal_risk_score,
        -- 续约预估价 (年化租金按递增率滚动 3 年)
        round(
            annual_base_rent_yuan
            * power(1.0 + rent_escalation, 3),
            0
        )                                                         as renewal_estimate_3y_yuan
    from leases
)

select * from flagged
