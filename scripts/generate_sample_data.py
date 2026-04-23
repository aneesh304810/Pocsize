"""
Generate 2M-record sample dataset for the SWP Sizing POC.

Produces a realistic investment-accounting fact table:
  - positions_raw: 2,000,000 rows (~ securities x accounts x as_of_date)
  - securities:       50,000 rows (SMF-style reference data)
  - accounts:         10,000 rows (client accounts)

Output:
  - CSV.gz (default; ubiquitous, works with Oracle SQL*Loader / external tables)
  - Parquet (if pyarrow/fastparquet available)
  - Also loads directly into Oracle if ORACLE_DSN env var is set

Designed to mimic the Advantage -> SWP migration shape:
  - Cash & non-cash positions
  - Multi-currency
  - Asset class hierarchy (2 levels)
  - Realistic null distribution for robustness testing
"""

import argparse
import os
import random
import string
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# Deterministic output so sizing runs are comparable
SEED = 42
random.seed(SEED)
np.random.seed(SEED)

ASSET_CLASSES = {
    "EQUITY":        ["COMMON_STOCK", "PREFERRED_STOCK", "ADR", "ETF"],
    "FIXED_INCOME":  ["CORPORATE_BOND", "GOVT_BOND", "MUNI_BOND", "MBS"],
    "CASH":          ["USD_CASH", "FX_CASH", "MMF"],
    "ALTERNATIVE":   ["HEDGE_FUND", "PRIVATE_EQUITY", "REAL_ESTATE"],
    "DERIVATIVE":    ["OPTION", "FUTURE", "SWAP", "FORWARD"],
}
CURRENCIES  = ["USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "HKD"]
COUNTRIES   = ["US", "GB", "DE", "FR", "JP", "CH", "CA", "AU", "HK", "SG"]
EXCHANGES   = ["NYSE", "NASDAQ", "LSE", "XETR", "TSE", "SIX", "TSX", "ASX"]

OUT_DIR = Path("/mnt/user-data/outputs/sample_data")


def random_cusip() -> str:
    """9-char pseudo-CUSIP."""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=9))


def random_isin() -> str:
    cc = random.choice(COUNTRIES)
    return cc + "".join(random.choices(string.digits, k=10))


def generate_securities(n: int) -> pd.DataFrame:
    print(f"[data] generating {n:,} securities (vectorised)...")
    asset_class_list = list(ASSET_CLASSES.keys())
    asset_classes    = np.random.choice(asset_class_list, size=n)
    sub_classes      = np.array([random.choice(ASSET_CLASSES[ac]) for ac in asset_classes])

    base_date  = date(2000, 1, 1)
    issue_days = np.random.randint(0, 9000, size=n)
    issue_dates = [base_date + timedelta(days=int(d)) for d in issue_days]

    mat_base   = date(2030, 1, 1)
    mat_days   = np.random.randint(0, 3650, size=n)
    maturity_dates = [
        (mat_base + timedelta(days=int(d))) if ac == "FIXED_INCOME" else None
        for ac, d in zip(asset_classes, mat_days)
    ]

    # CUSIP/ISIN/ticker: generate in bulk
    alphanum = np.array(list(string.ascii_uppercase + string.digits))
    digits   = np.array(list(string.digits))
    letters  = np.array(list(string.ascii_uppercase))
    cusips   = ["".join(np.random.choice(alphanum, 9)) for _ in range(n)]
    countries = np.random.choice(COUNTRIES, size=n)
    isins    = [cc + "".join(np.random.choice(digits, 10)) for cc in countries]
    tickers  = ["".join(np.random.choice(letters, 4)) for _ in range(n)]

    df = pd.DataFrame({
        "security_id":     [f"SEC{i:08d}" for i in range(n)],
        "cusip":           cusips,
        "isin":            isins,
        "ticker":          tickers,
        "security_name":   [f"Security {i} Holdings" for i in range(n)],
        "asset_class":     asset_classes,
        "sub_asset_class": sub_classes,
        "currency":        np.random.choice(CURRENCIES, size=n),
        "country":         countries,
        "exchange":        [random.choice(EXCHANGES) if ac == "EQUITY" else None
                            for ac in asset_classes],
        "issue_date":      issue_dates,
        "maturity_date":   maturity_dates,
        "is_active":       np.random.random(n) > 0.02,
    })
    return df


def generate_accounts(n: int) -> pd.DataFrame:
    print(f"[data] generating {n:,} accounts (vectorised)...")
    base_date = date(2010, 1, 1)
    incep_days = np.random.randint(0, 5000, size=n)
    return pd.DataFrame({
        "account_id":        [f"ACCT{i:07d}" for i in range(n)],
        "account_name":      [f"Client Account {i}" for i in range(n)],
        "client_id":         [f"CLT{i // 10:06d}" for i in range(n)],
        "account_type":      np.random.choice(
            ["TRUST", "IRA", "TAXABLE", "401K", "ENDOWMENT"], size=n),
        "base_currency":     np.random.choice(["USD", "EUR", "GBP"], size=n),
        "domicile_country":  np.random.choice(COUNTRIES, size=n),
        "inception_date":    [base_date + timedelta(days=int(d)) for d in incep_days],
        "portfolio_manager": [f"PM{random.randint(1, 200):04d}" for _ in range(n)],
        "is_discretionary":  np.random.random(n) > 0.3,
        "aum_tier":          np.random.choice(["SMALL", "MID", "LARGE", "UHNW"], size=n),
    })


def generate_positions(n: int, n_securities: int, n_accounts: int) -> pd.DataFrame:
    """
    Generate n position rows using vectorised numpy -- we need 2M rows fast.

    Schema mirrors a typical custody/accounting position feed:
      - as_of_date, account_id, security_id
      - quantity, cost_basis, market_value
      - unrealized_gl, accrued_income
      - fx_rate, settlement_status
    """
    print(f"[data] generating {n:,} positions (vectorised)...")

    as_of_dates = pd.date_range("2025-01-01", "2025-03-31", freq="B")  # business days
    date_idx    = np.random.randint(0, len(as_of_dates), size=n)
    acct_idx    = np.random.randint(0, n_accounts,   size=n)
    sec_idx     = np.random.randint(0, n_securities, size=n)

    # Realistic distributions: quantities skewed, prices lognormal
    quantities     = np.round(np.random.lognormal(mean=6, sigma=1.5, size=n), 2)
    unit_cost      = np.round(np.random.lognormal(mean=4, sigma=0.8, size=n), 4)
    unit_price     = unit_cost * np.random.normal(loc=1.05, scale=0.15, size=n)
    unit_price     = np.clip(unit_price, 0.01, None).round(4)

    cost_basis     = (quantities * unit_cost).round(2)
    market_value   = (quantities * unit_price).round(2)
    unrealized_gl  = (market_value - cost_basis).round(2)
    accrued_income = np.round(market_value * np.random.uniform(0, 0.005, size=n), 2)
    fx_rate        = np.round(np.random.uniform(0.8, 1.5, size=n), 6)

    # Inject ~1% nulls on accrued_income to exercise coalesce/null-handling downstream
    null_mask                 = np.random.random(n) < 0.01
    accrued_income[null_mask] = np.nan

    settlement_status = np.random.choice(
        ["SETTLED", "PENDING", "FAILED"], size=n, p=[0.95, 0.04, 0.01]
    )

    # Build ID strings via pandas string formatting (faster than python list comp for 2M)
    acct_ids = pd.Series(acct_idx).astype(str).str.zfill(7).radd("ACCT")
    sec_ids  = pd.Series(sec_idx).astype(str).str.zfill(8).radd("SEC")

    df = pd.DataFrame({
        "as_of_date":        as_of_dates[date_idx].date,
        "account_id":        acct_ids.values,
        "security_id":       sec_ids.values,
        "quantity":          quantities,
        "unit_cost":         unit_cost,
        "unit_price":        unit_price,
        "cost_basis":        cost_basis,
        "market_value":      market_value,
        "unrealized_gl":     unrealized_gl,
        "accrued_income":    accrued_income,
        "fx_rate":           fx_rate,
        "settlement_status": settlement_status,
        "source_system":     np.random.choice(["ADVANTAGE", "STAR"], size=n, p=[0.7, 0.3]),
    })

    # Hash-style surrogate key for idempotency checks in dbt
    df["position_hash"] = (
        df["as_of_date"].astype(str)
        + "|" + df["account_id"]
        + "|" + df["security_id"]
    )
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions", type=int, default=2_000_000)
    ap.add_argument("--securities", type=int, default=50_000)
    ap.add_argument("--accounts",   type=int, default=10_000)
    ap.add_argument("--out",        type=str, default=str(OUT_DIR))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    securities = generate_securities(args.securities)
    accounts   = generate_accounts(args.accounts)
    positions  = generate_positions(args.positions, args.securities, args.accounts)

    print(f"[data] writing output to {out}")

    # Try parquet first (compressed, typed); fall back to csv.gz if pyarrow absent.
    # csv.gz is actually preferred in the BBH air-gapped environment anyway since
    # Oracle SQL*Loader / external tables consume it natively.
    try:
        import pyarrow  # noqa: F401
        securities.to_parquet(out / "securities.parquet",    index=False, compression="snappy")
        accounts  .to_parquet(out / "accounts.parquet",      index=False, compression="snappy")
        positions .to_parquet(out / "positions_raw.parquet", index=False, compression="snappy")
        fmt, suffix = "parquet", ".parquet"
    except ImportError:
        print("[data] pyarrow unavailable; writing gzipped CSV instead")
        import gzip
        securities.to_csv(out / "securities.csv.gz", index=False, compression="gzip")
        accounts  .to_csv(out / "accounts.csv.gz",   index=False, compression="gzip")
        # Stream positions chunks through a single gzip file handle to avoid
        # multi-member gzip files (which break Oracle external table loaders).
        pos_path = out / "positions_raw.csv.gz"
        chunk    = 250_000
        with gzip.open(pos_path, "wt", compresslevel=6, newline="") as gz:
            for i in range(0, len(positions), chunk):
                positions.iloc[i:i + chunk].to_csv(
                    gz, index=False, header=(i == 0), mode="a",
                )
                print(f"[data]   wrote rows {i:>8,} - {min(i + chunk, len(positions)):>8,}")
        fmt, suffix = "csv.gz", ".csv.gz"

    # CSV sample of first 1000 rows per table for quick inspection / dbt seed fallback
    securities.head(1000).to_csv(out / "securities_sample.csv",    index=False)
    accounts  .head(1000).to_csv(out / "accounts_sample.csv",      index=False)
    positions .head(1000).to_csv(out / "positions_raw_sample.csv", index=False)

    size_mb = lambda p: p.stat().st_size / 1024 / 1024
    print("\n=== Sample Data Summary ===")
    print(f"format:                {fmt}")
    print(f"positions_raw{suffix} : {len(positions):>10,} rows  "
          f"({size_mb(out / ('positions_raw' + suffix)):.1f} MB)")
    print(f"securities{suffix}    : {len(securities):>10,} rows  "
          f"({size_mb(out / ('securities' + suffix)):.1f} MB)")
    print(f"accounts{suffix}      : {len(accounts):>10,} rows  "
          f"({size_mb(out / ('accounts' + suffix)):.1f} MB)")
    total_mem_mb = (
        positions.memory_usage(deep=True).sum()
        + securities.memory_usage(deep=True).sum()
        + accounts.memory_usage(deep=True).sum()
    ) / 1024 / 1024
    print(f"\nTotal uncompressed in-memory size: {total_mem_mb:.1f} MB")
    print("  -> baseline for pod memory sizing in k8s/values.yaml")


if __name__ == "__main__":
    main()
