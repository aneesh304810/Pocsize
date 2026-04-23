"""
scrape_sizing_metrics.py

Pulls the METRICS.DBT_MODEL_RUN_STATS rows for a single Airflow run_id,
enriches them with the pod-side metrics pulled from cAdvisor/metrics-server
(CPU seconds and peak working-set bytes per task pod), and emits a flat CSV
for downstream reporting.

The CSV is joined into the matrix report by build_sizing_report.py.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import oracledb


QUERY = """
    select
        run_id,
        run_started_at,
        model_name,
        materialization,
        dbt_threads,
        parallel_degree,
        elapsed_seconds,
        cpu_seconds,
        pga_peak_bytes,
        uga_peak_bytes,
        physical_reads_bytes,
        logical_reads,
        redo_size_bytes,
        rows_processed,
        status
    from metrics.dbt_model_run_stats
    where run_id = :run_id
    order by run_started_at
"""


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


def fetch_pod_metrics_if_available(run_id: str) -> dict:
    """
    Best-effort: read pod CPU/mem peak from metrics-server snapshots written
    to /mnt/sizing_reports/pod_metrics/<run_id>/*.json by a sidecar. Returns
    an empty dict if the directory isn't present (e.g. local-dev runs).
    """
    base = Path(f"/mnt/sizing_reports/pod_metrics/{run_id}")
    if not base.exists():
        return {}
    out = {}
    for f in base.glob("*.json"):
        import json
        with f.open() as fh:
            out[f.stem] = json.load(fh)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id",      required=True)
    ap.add_argument("--pod-size",    required=True)
    ap.add_argument("--dbt-threads", type=int, required=True)
    ap.add_argument("--out",         required=True)
    args = ap.parse_args()

    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(QUERY, {"run_id": args.run_id})
        rows = cur.fetchall()
        cols = [d[0].lower() for d in cur.description]
    finally:
        conn.close()

    pod_metrics = fetch_pod_metrics_if_available(args.run_id)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols + ["pod_size", "pod_peak_cpu_cores",
                                "pod_peak_mem_mib"])
        for row in rows:
            rec = dict(zip(cols, row))
            pod = pod_metrics.get(rec["model_name"], {})
            writer.writerow(list(row) + [
                args.pod_size,
                pod.get("peak_cpu_cores"),
                pod.get("peak_mem_mib"),
            ])

    print(f"[scrape] wrote {len(rows)} rows to {args.out}")
    if not rows:
        print("[scrape] WARNING: no rows matched run_id; check that "
              "log_model_stats macro ran and METRICS schema is populated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
