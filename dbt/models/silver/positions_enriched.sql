{{
    config(
        materialized          = 'incremental',
        incremental_strategy  = 'merge',
        unique_key            = 'position_hash',
        alias                 = 'positions_enriched',
        cluster_by            = ['as_of_date'],
        tags                  = ['silver', 'sizing_workload']
    )
}}

-- Silver: the primary sizing workload.
-- Joins 2M positions x 50K securities x 10K accounts, derives several
-- business metrics (base-currency conversions, asset-class rollups),
-- and MERGEs into the target.
--
-- This is the model that drives most of the CPU and memory figures in the
-- sizing report. Expected Oracle behaviour on a cold run:
--   - Full scan on positions_raw  (external table or heap)
--   - Hash join builds for both securities and accounts (dominant PGA)
--   - Single pass write; redo volume ~= final table size
--
-- Incremental filter: only reprocess rows whose as_of_date >= the max
-- already loaded (classic "late-arriving allowed within 3 days" pattern).

with positions as (
    select * from {{ ref('stg_positions') }}

    {% if is_incremental() %}
      where as_of_date >= (
          select coalesce(max(as_of_date), date '1900-01-01') - 3
          from {{ this }}
      )
    {% endif %}
),

securities as (select * from {{ ref('stg_securities') }}),
accounts   as (select * from {{ ref('stg_accounts')   }})

select
    p.position_hash,
    p.as_of_date,
    p.account_id,
    a.account_name,
    a.account_type,
    a.base_currency        as account_base_ccy,
    a.portfolio_manager,
    a.aum_tier,

    p.security_id,
    s.cusip,
    s.isin,
    s.ticker,
    s.security_name,
    s.asset_class,
    s.sub_asset_class,
    s.currency             as security_ccy,
    s.country              as security_country,

    p.quantity,
    p.unit_cost,
    p.unit_price,
    p.cost_basis,
    p.market_value,
    p.unrealized_gl,
    coalesce(p.accrued_income, 0)                 as accrued_income,

    -- Convert to account base currency
    p.fx_rate,
    round(p.market_value  * p.fx_rate, 2)          as market_value_base,
    round(p.cost_basis    * p.fx_rate, 2)          as cost_basis_base,
    round(p.unrealized_gl * p.fx_rate, 2)          as unrealized_gl_base,

    p.settlement_status,
    p.source_system,

    -- Derived flags used by gold aggregates
    case when s.asset_class = 'CASH' then 1 else 0 end        as is_cash,
    case when p.settlement_status = 'FAILED' then 1 else 0 end as is_failed,
    case when s.maturity_date is not null
              and s.maturity_date < p.as_of_date + 90
         then 1 else 0 end                                     as matures_within_90d,

    sysdate as dbt_updated_at

from positions p
inner join securities s on p.security_id = s.security_id
inner join accounts   a on p.account_id  = a.account_id
