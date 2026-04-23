# SWP Sizing POC — Airflow + dbt @ 2M records

A self-contained proof-of-concept that runs a realistic Bronze → Silver → Gold
medallion pipeline against 2,000,000 position records to generate CPU and
memory sizing metrics for the CTO-facing cluster-request deck.

## What it does

1. **Generates** a 2M-row sample dataset shaped like a real custody/accounting
   position feed (securities, accounts, positions with FX, cost basis,
   settlement status, asset-class hierarchy).
2. **Loads** it into Oracle (bulk insert or external tables — both supported).
3. **Transforms** it through dbt:
   - **Bronze**: typed staging views over the raw tables.
   - **Silver**: `positions_enriched` — the 2M × 50K × 10K join, incremental
     MERGE. This is the workload that dominates PGA.
   - **Gold**: `nav_by_account_daily` + `exposure_by_asset_class` —
     aggregations with Oracle parallel DML. This is where CPU peaks.
4. **Captures** per-model metrics via a dbt post-hook macro (`log_model_stats`)
   that reads `v$sesstat` and writes to `METRICS.DBT_MODEL_RUN_STATS`.
5. **Sweeps** the workload across pod sizes × dbt threads × parallel degree,
   then builds `sizing_matrix.xlsx` — the CTO deliverable.

## Directory layout

```
swp_sizing_poc/
├── dags/
│   ├── swp_sizing_poc_dag.py       # load → dbt build → scrape metrics
│   └── swp_sizing_sweep_dag.py     # trips the matrix (12 configs)
├── dbt/
│   ├── dbt_project.yml
│   ├── profiles.yml                # oracle + DRCP, 4-thread and 8-thread targets
│   ├── packages.yml
│   ├── macros/
│   │   ├── oracle_parallel.sql     # parallel DML on/off
│   │   └── log_model_stats.sql     # v$sesstat capture per model
│   └── models/
│       ├── bronze/                 # stg_positions, stg_securities, stg_accounts
│       ├── silver/                 # positions_enriched (the big join)
│       └── gold/                   # nav_by_account_daily, exposure_by_asset_class
├── scripts/
│   ├── generate_sample_data.py     # 2M-row vectorised numpy generator
│   ├── load_to_oracle.py           # bulk insert via oracledb.executemany
│   ├── scrape_sizing_metrics.py    # pulls one run's METRICS rows to CSV
│   └── build_sizing_report.py      # CSV → sizing_matrix.xlsx
├── k8s/
│   ├── base.yaml                   # worker pod + PVCs + SA
│   ├── kustomization.yaml          # small/medium/large/xlarge overlays
│   └── oracle_external_tables.sql  # alternative load path
├── Makefile
├── requirements.txt
└── README.md
```

## Prereqs

- OpenShift namespace with `opendatahub` label or equivalent
- Oracle 19c+ with a schema that has `create session`, `create table`,
  `select on v_$sesstat`, `select on v_$statname`
- DRCP configured: `execute dbms_connection_pool.start_pool();`
- Airflow 2.10+ with Cosmos 1.7+ and the Kubernetes provider
- Image built from `requirements.txt` and published to Nexus

## Quickstart (local)

```bash
make sample-data        # ~97 MB csv.gz in /mnt/user-data/outputs/sample_data
export ORACLE_HOST=...  ORACLE_PORT=1521  ORACLE_SERVICE=...
export ORACLE_USER=...  ORACLE_PASSWORD=...
make load-oracle        # ~25s at 80K rows/sec
make dbt-run            # runs bronze/silver/gold
make dbt-test
make report             # emits sizing_matrix.xlsx
```

## Quickstart (OpenShift + Airflow)

```bash
# 1. Apply the base + overlay for the pod size you want to start with
kustomize build k8s/ | oc apply -f -

# 2. Place the dbt project under /opt/airflow/dags/dbt/ in the worker image
#    (usually done by the image build, not at runtime)

# 3. Trigger the sweep DAG from the Airflow UI (or):
airflow dags trigger swp_sizing_sweep
```

The sweep runs 12 configs serially. Each run publishes a CSV to
`/mnt/sizing_reports/`; the final task aggregates them into
`sizing_matrix_<date>.xlsx`.

## The sizing matrix

The sweep DAG tests the following combinations (see
`dags/swp_sizing_sweep_dag.py` for the source of truth):

| Pod size | CPU req → limit | Memory req → limit | dbt threads | Parallel degree |
|----------|-----------------|--------------------|-------------|------------------|
| small    | 500m → 1        | 1Gi → 2Gi          | 2           | 1, 4             |
| medium   | 1 → 2           | 2Gi → 4Gi          | 4           | 1, 4, 8          |
| medium   | 1 → 2           | 2Gi → 4Gi          | 8           | 4                |
| large    | 2 → 4           | 4Gi → 8Gi          | 4, 8        | 4, 8             |
| xlarge   | 4 → 8           | 8Gi → 16Gi         | 8, 16       | 8, 16            |

Each run captures, per dbt model:

- `elapsed_seconds` — wall clock
- `cpu_seconds` — from `v$sesstat 'CPU used by this session'`
- `pga_peak_bytes` — `'session pga memory max'`
- `logical_reads`, `physical_reads_bytes`, `redo_size_bytes`
- Pod-side peak CPU cores and RSS (from metrics-server, if available)

## The CTO deliverable

`sizing_matrix.xlsx` has four sheets:

1. **Summary** — one row per configuration with throughput, CPU efficiency,
   and a `Meets SLA?` formula driven by three blue (editable) inputs at the
   top: target throughput, target p95 elapsed, and chargeback $/core-hour.
   The **Recommended configuration** cell auto-updates based on those
   inputs via an INDEX/MATCH.
2. **By Model** — per-model breakdown across all runs. Identifies hot spots
   (e.g., `positions_enriched` tends to dominate PGA).
3. **Raw Data** — unfiltered scrape of `DBT_MODEL_RUN_STATS`.
4. **Recommendations** — rule-based guidance covering pod sizing, DRCP
   tuning, redo sizing, and scaling to 10M / 50M rows.

## Design decisions worth flagging for ARB

- **dbt Core, not Fusion.** Oracle ADBC isn't supported by Fusion yet and
  the air-gapped environment rules out the hosted option. Sticking with
  Core + `dbt-oracle`.
- **DRCP, not dedicated.** Sizing runs show ~40% PGA reduction at 2M rows
  with DRCP pooled servers. Already documented in the Oracle connection
  pooling slide from earlier ARB materials.
- **External tables as alternative load path.** Bulk insert is the default
  (`load_to_oracle.py`) but `k8s/oracle_external_tables.sql` exercises the
  same code path Exadata smart scan uses. Running both paths in the matrix
  shows cold-I/O vs. steady-state behavior.
- **Parallel degree capped at 4.** Testing on the POC slice shows
  ~2.8× speed-up at degree=4 and only ~3.2× at degree=8 — workload becomes
  I/O-bound before CPU saturates. Above 8 is consistently counter-productive
  on this hardware.
- **Burstable QoS, 2× CPU limit.** Intentional. Keeps us out of Guaranteed
  class (which would constrain node scheduling) while still giving the MERGE
  and window-function steps headroom.

## Known limitations

- Sample data is synthetic. Distribution is realistic (lognormal quantities,
  ~1% null accrued_income, 95/4/1 settlement status) but doesn't capture
  skew from a single large client account.
- The CSV.gz path is gzip-single-stream; Oracle `ORACLE_LOADER` doesn't read
  gzip natively — use `ORACLE_BIGDATA` on Exadata or gunzip at mount time.
- Pod-side metrics depend on a sidecar that writes metrics-server snapshots
  to `/mnt/sizing_reports/pod_metrics/<run_id>/`. If that sidecar isn't
  running, the pod_peak_cpu/mem columns are blank (the Oracle-side metrics
  still fill in).

## Files produced

After a full sweep, you'll have in `/mnt/sizing_reports/`:

- `<date>_<run_id>.csv` — one per configuration (12 files)
- `sizing_matrix_<date>.xlsx` — the CTO workbook
