"""
load_to_oracle.py

Bulk-loads the generated sample CSV.gz files into Oracle landing tables.
Uses oracledb executemany() with array binds — the fastest row-oriented
path when you can't use the Oracle Instant Client sqlldr binary.

Typical throughput on the POC slice:
    ~80K rows/sec at batch size 10000
    full 2M-row positions load: ~25s

Creates landing tables if missing. Idempotent: truncates before reload.
"""

from __future__ import annotations

import csv
import gzip
import os
import sys
import time
from pathlib import Path

import oracledb

DATA_DIR = Path(os.environ.get("SAMPLE_DATA_DIR", "/mnt/sample_data"))
BATCH    = 10_000


DDL = {
    "securities": """
        create table securities (
            security_id       varchar2(20)  primary key,
            cusip             varchar2(9),
            isin              varchar2(12),
            ticker            varchar2(10),
            security_name     varchar2(200),
            asset_class       varchar2(20),
            sub_asset_class   varchar2(30),
            currency          varchar2(3),
            country           varchar2(2),
            exchange          varchar2(10),
            issue_date        date,
            maturity_date     date,
            is_active         varchar2(5)
        )
    """,
    "accounts": """
        create table accounts (
            account_id        varchar2(20)  primary key,
            account_name      varchar2(200),
            client_id         varchar2(20),
            account_type      varchar2(20),
            base_currency     varchar2(3),
            domicile_country  varchar2(2),
            inception_date    date,
            portfolio_manager varchar2(20),
            is_discretionary  varchar2(5),
            aum_tier          varchar2(10)
        )
    """,
    "positions_raw": """
        create table positions_raw (
            position_hash      varchar2(60),
            as_of_date         date,
            account_id         varchar2(20),
            security_id        varchar2(20),
            quantity           number(18, 4),
            unit_cost          number(18, 6),
            unit_price         number(18, 6),
            cost_basis         number(20, 2),
            market_value       number(20, 2),
            unrealized_gl      number(20, 2),
            accrued_income     number(20, 2),
            fx_rate            number(10, 6),
            settlement_status  varchar2(10),
            source_system      varchar2(20)
        )
        partition by range (as_of_date)
        interval (numtodsinterval(1, 'DAY')) (
            partition p_init values less than (date '2025-01-01')
        )
    """,
}

INSERT_SQL = {
    "securities": """
        insert into securities (
            security_id, cusip, isin, ticker, security_name, asset_class,
            sub_asset_class, currency, country, exchange, issue_date,
            maturity_date, is_active
        ) values (
            :1, :2, :3, :4, :5, :6, :7, :8, :9, :10,
            to_date(:11, 'YYYY-MM-DD'),
            case when :12 is null then null else to_date(:12, 'YYYY-MM-DD') end,
            :13
        )
    """,
    "accounts": """
        insert into accounts values (
            :1, :2, :3, :4, :5, :6,
            to_date(:7, 'YYYY-MM-DD'),
            :8, :9, :10
        )
    """,
    "positions_raw": """
        insert into positions_raw (
            position_hash, as_of_date, account_id, security_id, quantity,
            unit_cost, unit_price, cost_basis, market_value, unrealized_gl,
            accrued_income, fx_rate, settlement_status, source_system
        ) values (
            :1, to_date(:2, 'YYYY-MM-DD'), :3, :4, :5, :6, :7, :8, :9, :10,
            case when :11 is null or :11 = '' then null else to_number(:11) end,
            :12, :13, :14
        )
    """,
}

# Column order as emitted by generate_sample_data.py, for each table
COLS = {
    "securities": [
        "security_id", "cusip", "isin", "ticker", "security_name",
        "asset_class", "sub_asset_class", "currency", "country",
        "exchange", "issue_date", "maturity_date", "is_active",
    ],
    "accounts": [
        "account_id", "account_name", "client_id", "account_type",
        "base_currency", "domicile_country", "inception_date",
        "portfolio_manager", "is_discretionary", "aum_tier",
    ],
    # positions_raw has the position_hash at the END of the CSV per our generator
    "positions_raw": [
        "as_of_date", "account_id", "security_id", "quantity", "unit_cost",
        "unit_price", "cost_basis", "market_value", "unrealized_gl",
        "accrued_income", "fx_rate", "settlement_status", "source_system",
        "position_hash",
    ],
}
# Bind order for the INSERT (may differ from CSV order; positions_raw reorders)
BIND_ORDER = {
    "securities":    COLS["securities"],
    "accounts":      COLS["accounts"],
    "positions_raw": [
        "position_hash", "as_of_date", "account_id", "security_id",
        "quantity", "unit_cost", "unit_price", "cost_basis",
        "market_value", "unrealized_gl", "accrued_income", "fx_rate",
        "settlement_status", "source_system",
    ],
}


def connect() -> oracledb.Connection:
    dsn = oracledb.makedsn(
        os.environ["ORACLE_HOST"],
        int(os.environ["ORACLE_PORT"]),
        service_name=os.environ["ORACLE_SERVICE"],
    )
    return oracledb.connect(
        user     = os.environ["ORACLE_USER"],
        password = os.environ["ORACLE_PASSWORD"],
        dsn      = dsn,
    )


def ensure_table(conn: oracledb.Connection, table: str) -> None:
    cur = conn.cursor()
    cur.execute("""
        select count(*) from user_tables where table_name = :t
    """, {"t": table.upper()})
    (exists,) = cur.fetchone()
    if not exists:
        print(f"[load] creating {table}")
        cur.execute(DDL[table])
        conn.commit()
    else:
        print(f"[load] truncating {table}")
        cur.execute(f"truncate table {table}")
        conn.commit()
    cur.close()


def stream_rows(csv_gz_path: Path, columns: list[str]):
    """Yield dicts keyed by column name from a gzipped CSV."""
    with gzip.open(csv_gz_path, "rt", newline="") as fh:
        reader = csv.DictReader(fh)
        # Sanity check: CSV columns must match expected
        if reader.fieldnames != columns:
            raise ValueError(
                f"{csv_gz_path.name} columns mismatch.\n"
                f"  expected: {columns}\n"
                f"  got:      {reader.fieldnames}"
            )
        for row in reader:
            yield row


def load_table(conn: oracledb.Connection, table: str) -> int:
    ensure_table(conn, table)
    csv_path = DATA_DIR / f"{table}.csv.gz"
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    print(f"[load] loading {csv_path} into {table}")
    cur = conn.cursor()
    cur.arraysize = BATCH

    insert_sql = INSERT_SQL[table]
    bind_order = BIND_ORDER[table]

    batch: list[tuple] = []
    total = 0
    t0 = time.time()

    for row in stream_rows(csv_path, COLS[table]):
        # coerce empty strings -> None so binds get NULL
        tup = tuple((row[c] if row[c] != "" else None) for c in bind_order)
        batch.append(tup)
        if len(batch) >= BATCH:
            cur.executemany(insert_sql, batch)
            total += len(batch)
            batch.clear()
            if total % (BATCH * 10) == 0:
                rate = total / (time.time() - t0)
                print(f"[load]   {total:>10,} rows   ({rate:,.0f} rows/s)")

    if batch:
        cur.executemany(insert_sql, batch)
        total += len(batch)

    conn.commit()
    cur.close()
    rate = total / (time.time() - t0) if total else 0
    print(f"[load]   done: {total:,} rows in {time.time() - t0:.1f}s ({rate:,.0f} rows/s)")
    return total


def main() -> int:
    if not DATA_DIR.exists():
        print(f"[load] ERROR: sample data dir not found: {DATA_DIR}")
        return 1

    conn = connect()
    try:
        for table in ("securities", "accounts", "positions_raw"):
            load_table(conn, table)
        # Gather stats so Oracle optimiser makes good plans for the silver joins
        cur = conn.cursor()
        for t in ("securities", "accounts", "positions_raw"):
            print(f"[load] gather_table_stats({t})")
            cur.callproc("dbms_stats.gather_table_stats",
                         [os.environ["ORACLE_USER"].upper(), t.upper()])
        cur.close()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
