"""
swp_sizing_poc_dag.py

Main POC pipeline:
    load sample data -> dbt build (bronze/silver/gold) -> capture metrics

Shape mirrors the production SWP DAGs: Cosmos integration for dbt,
KubernetesPodOperator for isolation, explicit resource requests/limits so
the scheduler can place the pods predictably.

Run this DAG at each (pod size, dbt threads, parallel degree) combination
you want to benchmark. Results land in METRICS.DBT_MODEL_RUN_STATS and are
scraped by the scrape_sizing_metrics task at the end.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

# Cosmos is the canonical SWP pattern — parses dbt manifest, emits one
# Airflow task per model, preserves dependency graph.
from cosmos import DbtTaskGroup, ProjectConfig, ProfileConfig, ExecutionConfig
from cosmos.profiles import OracleManagedIdentityProfileMapping  # Adjust if using different auth

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DBT_PROJECT_PATH = Path("/opt/airflow/dags/dbt/swp_sizing_poc")
DBT_PROFILES_PATH = Path("/opt/airflow/dags/dbt/swp_sizing_poc/profiles.yml")

# Pod size profile is driven by an Airflow Variable so the sizing sweep DAG
# can rotate through small / medium / large without editing the DAG file.
POD_SIZE = Variable.get("swp_poc_pod_size", default_var="medium")

POD_SIZES = {
    "small":  {"cpu_req": "500m",  "cpu_lim": "1",    "mem_req": "1Gi",  "mem_lim": "2Gi"},
    "medium": {"cpu_req": "1",     "cpu_lim": "2",    "mem_req": "2Gi",  "mem_lim": "4Gi"},
    "large":  {"cpu_req": "2",     "cpu_lim": "4",    "mem_req": "4Gi",  "mem_lim": "8Gi"},
    "xlarge": {"cpu_req": "4",     "cpu_lim": "8",    "mem_req": "8Gi",  "mem_lim": "16Gi"},
}
RESOURCES = POD_SIZES[POD_SIZE]

# dbt thread count — varied alongside pod size for the CTO matrix.
DBT_THREADS = int(Variable.get("swp_poc_dbt_threads", default_var="4"))

IMAGE = "nexus.bbhgroup.net/swp-airflow-worker:latest"

default_args = {
    "owner":             "capital-partners-data-eng",
    "depends_on_past":    False,
    "email":             ["capital-partners-data-eng@bbh.com"],
    "email_on_failure":   True,
    "retries":           1,
    "retry_delay":        timedelta(minutes=3),
    "execution_timeout":  timedelta(hours=2),
}

# ---------------------------------------------------------------------------
# Pod resource spec (reused across tasks for consistent sizing measurements)
# ---------------------------------------------------------------------------

def build_resources() -> k8s.V1ResourceRequirements:
    return k8s.V1ResourceRequirements(
        requests={"cpu": RESOURCES["cpu_req"], "memory": RESOURCES["mem_req"]},
        limits  ={"cpu": RESOURCES["cpu_lim"], "memory": RESOURCES["mem_lim"]},
    )

# ---------------------------------------------------------------------------
# DAG
# ---------------------------------------------------------------------------

with DAG(
    dag_id           = "swp_sizing_poc",
    description      = "SWP migration sizing POC — 2M records, Bronze/Silver/Gold",
    default_args     = default_args,
    start_date       = datetime(2026, 4, 1),
    schedule         = None,             # manually triggered
    catchup          = False,
    max_active_runs  = 1,                # single run at a time for clean metrics
    tags             = ["swp", "sizing", "poc", f"pod:{POD_SIZE}"],
    params           = {
        "smoke_test": False,             # if true, dbt uses 200K-row limit
        "parallel_degree": 4,
    },
) as dag:

    # -----------------------------------------------------------------------
    # 1. Load sample data into Oracle (SQL*Loader via a sidecar pod)
    # -----------------------------------------------------------------------
    load_sample_data = KubernetesPodOperator(
        task_id                = "load_sample_data",
        name                   = "swp-load-sample-data",
        namespace              = "opendatahub",
        image                  = IMAGE,
        cmds                   = ["bash", "-lc"],
        arguments              = ["python /opt/airflow/dags/scripts/load_to_oracle.py"],
        env_vars               = {
            "ORACLE_USER":     "{{ conn.oracle_swp.login }}",
            "ORACLE_PASSWORD": "{{ conn.oracle_swp.password }}",
            "ORACLE_HOST":     "{{ conn.oracle_swp.host }}",
            "ORACLE_PORT":     "{{ conn.oracle_swp.port }}",
            "ORACLE_SERVICE":  "{{ conn.oracle_swp.extra_dejson.service_name }}",
            "SAMPLE_DATA_DIR": "/mnt/sample_data",
        },
        container_resources    = build_resources(),
        get_logs               = True,
        is_delete_operator_pod = True,
        in_cluster             = True,
    )

    # -----------------------------------------------------------------------
    # 2. dbt build — bronze / silver / gold — via Cosmos
    # -----------------------------------------------------------------------
    profile_config = ProfileConfig(
        profile_name   = "swp_sizing_poc",
        target_name    = "dev",
        profiles_yml_filepath = DBT_PROFILES_PATH,
    )

    execution_config = ExecutionConfig(
        dbt_executable_path = "/home/airflow/.venv/bin/dbt",
    )

    dbt_build = DbtTaskGroup(
        group_id        = "dbt_build",
        project_config  = ProjectConfig(DBT_PROJECT_PATH),
        profile_config  = profile_config,
        execution_config= execution_config,
        operator_args   = {
            "install_deps":          False,      # deps pre-installed in image
            "vars":                  {"smoke_test": "{{ params.smoke_test }}",
                                      "parallel_degree": "{{ params.parallel_degree }}"},
            "container_resources":   build_resources(),
            "image":                 IMAGE,
            "namespace":             "opendatahub",
            "is_delete_operator_pod":True,
        },
        default_args = {"retries": 1},
    )

    # -----------------------------------------------------------------------
    # 3. Scrape METRICS.DBT_MODEL_RUN_STATS and publish CSV artifact
    # -----------------------------------------------------------------------
    scrape_metrics = KubernetesPodOperator(
        task_id                = "scrape_sizing_metrics",
        name                   = "swp-scrape-metrics",
        namespace              = "opendatahub",
        image                  = IMAGE,
        cmds                   = ["bash", "-lc"],
        arguments              = [
            "python /opt/airflow/dags/scripts/scrape_sizing_metrics.py "
            "--run-id {{ run_id }} "
            f"--pod-size {POD_SIZE} "
            f"--dbt-threads {DBT_THREADS} "
            "--out /mnt/sizing_reports/{{ ds }}_{{ run_id }}.csv"
        ],
        env_vars               = {
            "ORACLE_USER":     "{{ conn.oracle_swp.login }}",
            "ORACLE_PASSWORD": "{{ conn.oracle_swp.password }}",
            "ORACLE_HOST":     "{{ conn.oracle_swp.host }}",
            "ORACLE_PORT":     "{{ conn.oracle_swp.port }}",
            "ORACLE_SERVICE":  "{{ conn.oracle_swp.extra_dejson.service_name }}",
        },
        container_resources    = build_resources(),
        get_logs               = True,
        is_delete_operator_pod = True,
        in_cluster             = True,
    )

    load_sample_data >> dbt_build >> scrape_metrics
