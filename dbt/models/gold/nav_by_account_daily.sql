{{
    config(
        materialized = 'table',
        alias        = 'nav_by_account_daily',
        tags         = ['gold', 'sizing_workload']
    )
}}

-- Gold: account-level NAV rollup by as_of_date. Aggregates 2M silver rows
-- into ~300K (10K accounts x ~62 business days). This is a pure group-by
-- with a few window functions — a good test for Oracle parallel query.

with enriched as (
    select * from {{ ref('positions_enriched') }}
),

agg as (
    select
        {{ parallel_hint(4) }}
        as_of_date,
        account_id,
        account_name,
        account_type,
        account_base_ccy,
        portfolio_manager,
        aum_tier,

        count(*)                                        as position_count,
        count(distinct security_id)                     as distinct_securities,
        count(distinct asset_class)                     as distinct_asset_classes,

        sum(market_value_base)                          as nav_base,
        sum(cost_basis_base)                            as cost_basis_base,
        sum(unrealized_gl_base)                         as unrealized_gl_base,
        sum(accrued_income)                             as accrued_income_total,

        sum(case when is_cash   = 1 then market_value_base else 0 end) as cash_base,
        sum(case when is_failed = 1 then 1 else 0 end)                  as failed_trade_count,
        sum(case when matures_within_90d = 1 then market_value_base
                 else 0 end)                                            as maturing_90d_base,

        -- Concentration: largest single position as % of NAV
        max(market_value_base)                          as top_position_base
    from enriched
    group by
        as_of_date, account_id, account_name, account_type,
        account_base_ccy, portfolio_manager, aum_tier
),

with_derived as (
    select
        a.*,
        case when nav_base > 0
             then round(top_position_base / nav_base * 100, 2)
             else null
        end                                             as top_position_pct,
        case when nav_base > 0
             then round(cash_base / nav_base * 100, 2)
             else null
        end                                             as cash_pct_nav,
        -- Day-over-day NAV change per account
        lag(nav_base) over (
            partition by account_id
            order by     as_of_date
        )                                               as prior_day_nav_base
    from agg a
)

select
    as_of_date,
    account_id,
    account_name,
    account_type,
    account_base_ccy,
    portfolio_manager,
    aum_tier,
    position_count,
    distinct_securities,
    distinct_asset_classes,
    nav_base,
    cost_basis_base,
    unrealized_gl_base,
    accrued_income_total,
    cash_base,
    cash_pct_nav,
    failed_trade_count,
    maturing_90d_base,
    top_position_base,
    top_position_pct,
    prior_day_nav_base,
    case when prior_day_nav_base is not null and prior_day_nav_base > 0
         then round((nav_base - prior_day_nav_base) / prior_day_nav_base * 100, 4)
         else null
    end                                                 as daily_return_pct,
    sysdate                                             as dbt_built_at
from with_derived
