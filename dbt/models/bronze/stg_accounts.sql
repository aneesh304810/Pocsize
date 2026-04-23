{{ config(materialized = 'view', alias = 'stg_accounts') }}

select
    cast(account_id        as varchar2(20))   as account_id,
    cast(account_name      as varchar2(200))  as account_name,
    cast(client_id         as varchar2(20))   as client_id,
    cast(account_type      as varchar2(20))   as account_type,
    cast(base_currency     as varchar2(3))    as base_currency,
    cast(domicile_country  as varchar2(2))    as domicile_country,
    cast(inception_date    as date)           as inception_date,
    cast(portfolio_manager as varchar2(20))   as portfolio_manager,
    case when upper(is_discretionary) in ('TRUE','1','Y') then 1 else 0 end as is_discretionary,
    cast(aum_tier          as varchar2(10))   as aum_tier
from {{ source('raw', 'accounts') }}
