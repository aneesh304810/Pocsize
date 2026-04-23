{{
  config(
    materialized   = 'view',
    alias          = 'stg_positions'
  )
}}

-- Bronze / staging: lightweight pass-through view over the raw external table.
-- Purpose: give downstream layers a stable contract (typed columns, naming
-- convention) without materialising. Cost: ~0 (view), but every downstream
-- query scans the external table, so we measure full table scan cost here.

with src as (
    select * from {{ source('raw', 'positions_raw') }}
    {% if var('smoke_test', false) %}
    where rownum <= {{ var('smoke_test_limit') }}
    {% endif %}
)

select
    position_hash                                             as position_hash,
    cast(as_of_date      as date)                             as as_of_date,
    cast(account_id      as varchar2(20))                     as account_id,
    cast(security_id     as varchar2(20))                     as security_id,
    cast(quantity        as number(18, 4))                    as quantity,
    cast(unit_cost       as number(18, 6))                    as unit_cost,
    cast(unit_price      as number(18, 6))                    as unit_price,
    cast(cost_basis      as number(20, 2))                    as cost_basis,
    cast(market_value    as number(20, 2))                    as market_value,
    cast(unrealized_gl   as number(20, 2))                    as unrealized_gl,
    cast(accrued_income  as number(20, 2))                    as accrued_income,
    cast(fx_rate         as number(10, 6))                    as fx_rate,
    cast(settlement_status as varchar2(10))                   as settlement_status,
    cast(source_system   as varchar2(20))                     as source_system,
    sysdate                                                   as dbt_loaded_at
from src
