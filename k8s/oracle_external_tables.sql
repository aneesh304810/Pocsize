-- =========================================================================
-- oracle_external_tables.sql
-- =========================================================================
-- Defines external tables over the generated CSV.gz sample data.
--
-- Prefer this over bulk INSERT when the POC sizing workload needs to
-- measure cold I/O — it pushes the full scan through Oracle's ORACLE_LOADER
-- driver and exercises the same code path as an Exadata smart scan.
--
-- Prereqs:
--   1. A DIRECTORY object pointing at the NFS mount where the CSVs live:
--        create directory sample_data_dir as '/mnt/sample_data';
--        grant read on directory sample_data_dir to <schema>;
--   2. The CSV files must be gunzipped first (ORACLE_LOADER doesn't read
--      gzipped inputs directly; either gunzip at mount time or pipe through
--      named pipes). Alternative: use ORACLE_BIGDATA driver, which does
--      support gzip — preferred on Exadata.
-- =========================================================================

-- ---------- positions_raw (the 2M-row fact) ------------------------------
create table ext_positions_raw (
    as_of_date         varchar2(10),
    account_id         varchar2(20),
    security_id        varchar2(20),
    quantity           varchar2(20),
    unit_cost          varchar2(20),
    unit_price         varchar2(20),
    cost_basis         varchar2(20),
    market_value       varchar2(20),
    unrealized_gl      varchar2(20),
    accrued_income     varchar2(20),
    fx_rate            varchar2(20),
    settlement_status  varchar2(10),
    source_system      varchar2(20),
    position_hash      varchar2(60)
)
organization external (
    type   oracle_loader
    default directory sample_data_dir
    access parameters (
        records delimited by newline
        skip 1
        fields csv with embedded terminated by ',' optionally enclosed by '"'
        missing field values are null
        reject rows with all null fields
    )
    location ('positions_raw.csv')
)
reject limit unlimited
parallel 4;        -- px servers used for the external scan

-- ---------- securities (50K reference) -----------------------------------
create table ext_securities (
    security_id       varchar2(20),
    cusip             varchar2(9),
    isin              varchar2(12),
    ticker            varchar2(10),
    security_name     varchar2(200),
    asset_class       varchar2(20),
    sub_asset_class   varchar2(30),
    currency          varchar2(3),
    country           varchar2(2),
    exchange          varchar2(10),
    issue_date        varchar2(10),
    maturity_date     varchar2(10),
    is_active         varchar2(5)
)
organization external (
    type   oracle_loader
    default directory sample_data_dir
    access parameters (
        records delimited by newline
        skip 1
        fields csv with embedded terminated by ',' optionally enclosed by '"'
        missing field values are null
    )
    location ('securities.csv')
)
reject limit unlimited;

-- ---------- accounts (10K reference) -------------------------------------
create table ext_accounts (
    account_id        varchar2(20),
    account_name      varchar2(200),
    client_id         varchar2(20),
    account_type      varchar2(20),
    base_currency     varchar2(3),
    domicile_country  varchar2(2),
    inception_date    varchar2(10),
    portfolio_manager varchar2(20),
    is_discretionary  varchar2(5),
    aum_tier          varchar2(10)
)
organization external (
    type   oracle_loader
    default directory sample_data_dir
    access parameters (
        records delimited by newline
        skip 1
        fields csv with embedded terminated by ',' optionally enclosed by '"'
        missing field values are null
    )
    location ('accounts.csv')
)
reject limit unlimited;

-- Heap-loaded copies (so we can compare external vs. heap timings in the
-- sizing matrix). These are populated by load_to_oracle.py in the Airflow
-- `load_sample_data` task.
--
-- Keep both paths available — the external path measures cold I/O without
-- buffer cache benefit; the heap path represents steady-state production.

-- Grant usage so dbt sees them via the `raw` source
-- grant select on ext_positions_raw to dbt_swp;
-- grant select on ext_securities    to dbt_swp;
-- grant select on ext_accounts      to dbt_swp;

-- =========================================================================
-- Metrics schema: DBT_MODEL_RUN_STATS is created by the dbt on-run-start
-- macro. You can pre-create a dedicated metrics schema with:
--
--   create user metrics identified by "<pw>"
--       default tablespace users
--       temporary tablespace temp
--       quota unlimited on users;
--   grant create session, create table to metrics;
-- =========================================================================
