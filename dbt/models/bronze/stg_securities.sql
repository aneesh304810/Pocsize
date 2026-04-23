{{ config(materialized = 'view', alias = 'stg_securities') }}

select
    cast(security_id       as varchar2(20))   as security_id,
    cast(cusip             as varchar2(9))    as cusip,
    cast(isin              as varchar2(12))   as isin,
    cast(ticker            as varchar2(10))   as ticker,
    cast(security_name     as varchar2(200))  as security_name,
    cast(asset_class       as varchar2(20))   as asset_class,
    cast(sub_asset_class   as varchar2(30))   as sub_asset_class,
    cast(currency          as varchar2(3))    as currency,
    cast(country           as varchar2(2))    as country,
    cast(exchange          as varchar2(10))   as exchange,
    cast(issue_date        as date)           as issue_date,
    cast(maturity_date     as date)           as maturity_date,
    case when upper(is_active) in ('TRUE','1','Y') then 1 else 0 end as is_active
from {{ source('raw', 'securities') }}
