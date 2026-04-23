{{
    config(
        materialized = 'table',
        alias        = 'exposure_by_asset_class',
        tags         = ['gold']
    )
}}

-- Gold: firm-level exposure rollup by asset class and date.
-- Smaller result set (~300 rows) but still scans all 2M silver rows,
-- so CPU profile is similar to nav_by_account_daily without the window fn.

select
    {{ parallel_hint(4) }}
    as_of_date,
    asset_class,
    sub_asset_class,

    count(*)                                           as position_count,
    count(distinct account_id)                         as distinct_accounts,
    count(distinct security_id)                        as distinct_securities,

    sum(market_value_base)                             as total_exposure_base,
    sum(cost_basis_base)                               as total_cost_base,
    sum(unrealized_gl_base)                            as total_unrealized_gl_base,
    round(avg(market_value_base), 2)                   as avg_position_base,

    -- Concentration within the asset class
    max(market_value_base)                             as largest_position_base,
    sum(case when is_failed = 1 then 1 else 0 end)     as failed_count,

    sysdate                                            as dbt_built_at

from {{ ref('positions_enriched') }}
group by as_of_date, asset_class, sub_asset_class
