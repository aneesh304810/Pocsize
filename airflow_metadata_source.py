"""
Airflow metadata source abstraction.

Lets the SAME AirflowConnector logic read from either:
  - real Postgres  (AIRFLOW_DSN=postgresql://...)   -> production / OpenShift
  - a synthetic JSON file (AIRFLOW_DSN=file:///path/to/airflow_metadata.json)
    -> local laptop, no Postgres needed

Both expose the same .query(sql_key) returning rows in the exact shape the
connector expects, so deployment swaps the DSN and nothing else.

The JSON file is shaped as:
  {"dag": [[dag_id,is_paused,owners],...],
   "dag_tag": [[dag_id,name],...],
   "task_instance": [[dag_id,task_id,state,start_iso,end_iso],...]}
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Any


class PostgresSource:
    """Thin wrapper over a real Airflow Postgres metadata DB (psycopg3)."""
    def __init__(self, dsn: str):
        import psycopg
        self._conn = psycopg.connect(dsn)

    def fetch(self, table: str, limit: int | None = None) -> list[tuple]:
        cur = self._conn.cursor()
        if table == "dag":
            cur.execute("SELECT dag_id, is_paused, owners FROM dag")
        elif table == "dag_tag":
            cur.execute("SELECT dag_id, name FROM dag_tag")
        elif table == "task_instance":
            cur.execute(
                """SELECT dag_id, task_id, state, start_date, end_date
                   FROM task_instance
                   ORDER BY start_date DESC NULLS LAST
                   LIMIT %s""", (limit or 500,))
        rows = cur.fetchall()
        cur.close()
        return rows

    def close(self):
        self._conn.close()


class JsonFileSource:
    """Reads synthetic metadata from a JSON file (local, no Postgres)."""
    def __init__(self, path: str):
        with open(path) as fh:
            self._data = json.load(fh)

    def fetch(self, table: str, limit: int | None = None) -> list[tuple]:
        rows = self._data.get(table, [])
        if table == "task_instance":
            # parse ISO timestamps to datetime so duration math works like PG
            out = []
            for r in rows:
                dag_id, task_id, state, s, e = r
                sdt = datetime.fromisoformat(s) if s else None
                edt = datetime.fromisoformat(e) if e else None
                out.append((dag_id, task_id, state, sdt, edt))
            out.sort(key=lambda x: (x[3] is not None, x[3]), reverse=True)
            return out[: (limit or 500)]
        return [tuple(r) for r in rows]

    def close(self):
        pass


def open_source(dsn: str):
    """Factory: pick the source by DSN scheme."""
    if dsn.startswith(("postgresql://", "postgres://")):
        return PostgresSource(dsn)
    if dsn.startswith("file://"):
        path = dsn[len("file://"):]
        # file:///C:/... -> /C:/... ; strip the leading slash before a Windows
        # drive letter so Windows accepts it (/C:/x -> C:/x)
        if len(path) >= 3 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return JsonFileSource(path)
    # bare path also treated as a file, for convenience
    return JsonFileSource(dsn)
