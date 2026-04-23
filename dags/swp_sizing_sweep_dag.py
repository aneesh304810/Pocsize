"""
swp_sizing_sweep_dag.py

Orchestrates the full CTO-facing sizing matrix by triggering the main
swp_sizing_poc DAG once per (pod_size, dbt_threads, parallel_degree)
combination. Total matrix = 4 pod sizes x 3 thread counts x 3 parallel
degrees = 36 runs; trimmed to 12 meaningful combinations below to keep the
POC Exadata slice free overnight.

At the end, a single task aggregates METRICS.DBT_MODEL_RUN_STATS across all
runs in this sweep and produces the sizing_matrix.csv + sizing_report.xlsx
that go into the ARB / CTO deck.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.operators.trigger_dagrun import TriggerDagRunOperator

SWEEP_CONFIGS = [
    # (pod_size, dbt_threads, parallel_degree)   — name suffix
    ("small",  2, 1),   #  1c / 2Gi, serial
    ("small",  2, 4),
    ("medium", 4, 1),   #  2c / 4Gi, serial
    ("medium", 4, 4),   #  <-- candidate recommended config
    ("medium", 4, 8),
    ("medium", 8, 4),   #  contention test: more threads than cores
    ("large",  4, 4),
    ("large",  8, 4),
    ("large",  8, 8),   #  <-- upper bound candidate
    ("xlarge", 8, 8),
    ("xlarge", 16, 8),  #  stress / ceiling
    ("xlarge", 16, 16), #  stress / ceiling
]

default_args = {
    "owner":            "capital-partners-data-eng",
    "retries":          0,
    "execution_timeout": timedelta(hours=6),
}


def set_sweep_vars(pod_size: str, dbt_threads: int, **_):
    """Set Airflow Variables so the child DAG picks up this run's sizing."""
    Variable.set("swp_poc_pod_size",   pod_size)
    Variable.set("swp_poc_dbt_threads", str(dbt_threads))


with DAG(
    dag_id       = "swp_sizing_sweep",
    description  = "Runs the SWP sizing POC across pod sizes / threads / parallelism",
    default_args = default_args,
    start_date   = datetime(2026, 4, 1),
    schedule     = None,
    catchup      = False,
    max_active_runs = 1,
    tags         = ["swp", "sizing", "sweep"],
) as dag:

    previous = None
    for idx, (pod_size, dbt_threads, parallel_degree) in enumerate(SWEEP_CONFIGS):
        suffix = f"{pod_size}_t{dbt_threads}_p{parallel_degree}"

        configure = PythonOperator(
            task_id           = f"configure_{suffix}",
            python_callable   = set_sweep_vars,
            op_kwargs         = {"pod_size": pod_size, "dbt_threads": dbt_threads},
        )

        trigger = TriggerDagRunOperator(
            task_id                = f"run_{suffix}",
            trigger_dag_id         = "swp_sizing_poc",
            conf                   = {
                "smoke_test":      False,
                "parallel_degree": parallel_degree,
                "sweep_suffix":    suffix,
            },
            wait_for_completion    = True,
            reset_dag_run          = True,
            poke_interval          = 30,
            allowed_states         = ["success"],
            failed_states          = ["failed"],
        )

        configure >> trigger
        if previous is not None:
            previous >> configure     # serialize the matrix — one run at a time
        previous = trigger

    # Final step: aggregate across all runs and emit the CTO report.
    aggregate_report = PythonOperator(
        task_id = "aggregate_sizing_matrix",
        python_callable = lambda **ctx: __import__("subprocess").run([
            "python", "/opt/airflow/dags/scripts/build_sizing_report.py",
            "--out", f"/mnt/sizing_reports/sizing_matrix_{ctx['ds']}.xlsx",
        ], check=True),
    )

    previous >> aggregate_report
