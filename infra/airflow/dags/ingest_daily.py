"""
infra/airflow/dags/ingest_daily.py

Daily ingestion DAG for the Fin BP Portal data-integration layer.

Schedule: ``@daily`` (00:00 UTC, Airflow default).

Tasks (in order):

1. ``ingest_csv_landing``  — read every ``/data/landing/*.csv`` file and
                             upsert it into ``raw.uploads`` (one row per
                             file, payload is a JSON array of dicts).
2. ``run_dbt``             — run ``dbt run`` against the project in
                             ``/opt/airflow/dbt``. Fails the DAG if dbt
                             exits non-zero.

The DAG is intentionally framework-light: a pure-Python ``PythonOperator``
for ingestion, and a ``BashOperator`` for dbt. No external scheduler
dependencies beyond Airflow itself.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator


# ---- Config --------------------------------------------------------------

LANDING_DIR = Path(os.environ.get("LANDING_DIR", "/data/landing"))
DBT_PROJECT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/dbt"))
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_USER = os.environ.get("POSTGRES_USER", "finbp")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "finbp")
PG_DB = os.environ.get("POSTGRES_DB", "finbp")


# ---- DDL bootstrap -------------------------------------------------------

SCHEMA_DDL = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    """
    CREATE TABLE IF NOT EXISTS raw.uploads (
        id          BIGSERIAL PRIMARY KEY,
        upload_id   TEXT NOT NULL UNIQUE,
        filename    TEXT NOT NULL,
        upload_type TEXT NOT NULL
                    CHECK (upload_type IN ('excel', 'csv', 'bank_statement')),
        row_count   INTEGER NOT NULL,
        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        payload     JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_uploads_uploaded_at "
    "ON raw.uploads (uploaded_at DESC)",
]


# ---- DB helper -----------------------------------------------------------

def _connect():
    """Open a psycopg2 connection. Imported lazily so the DAG file can be
    parsed even when the postgres extra isn't installed locally."""
    try:
        import psycopg2  # type: ignore
        import psycopg2.extras  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "psycopg2 is required to run the ingest_daily DAG; "
            "install it into the Airflow image."
        ) from exc

    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        dbname=PG_DB,
    )
    conn.autocommit = False
    return conn, psycopg2.extras.Json


def _ensure_schema() -> None:
    conn, _ = _connect()
    try:
        with conn.cursor() as cur:
            for stmt in SCHEMA_DDL:
                cur.execute(stmt)
        conn.commit()
    finally:
        conn.close()


def _read_csv(path: Path) -> list[dict]:
    """Read a CSV file with the stdlib csv module (no pandas dependency)."""
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [dict(row) for row in reader]


def _insert_upload(conn, Json, filename: str, upload_type: str, rows: list[dict]) -> str:
    upload_id = (
        f"up_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_"
        f"{Path(filename).stem}_{abs(hash(filename)) % 10000:04d}"
    )
    payload = Json(rows)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.uploads
                (upload_id, filename, upload_type, row_count, payload)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (upload_id) DO UPDATE
                SET row_count = EXCLUDED.row_count,
                    payload   = EXCLUDED.payload
            """,
            (upload_id, filename, upload_type, len(rows), payload),
        )
    return upload_id


# ---- Task: ingest_csv_landing -------------------------------------------


def ingest_csv_landing(**_context) -> dict:
    """Walk /data/landing, ingest every *.csv, return a small summary dict."""
    _ensure_schema()

    if not LANDING_DIR.exists():
        print(f"[ingest_daily] landing dir does not exist: {LANDING_DIR}")
        return {"files_seen": 0, "rows_ingested": 0}

    csv_files = sorted(LANDING_DIR.glob("*.csv"))
    if not csv_files:
        print(f"[ingest_daily] no .csv files found in {LANDING_DIR}")
        return {"files_seen": 0, "rows_ingested": 0}

    conn, Json = _connect()
    total_rows = 0
    try:
        for path in csv_files:
            try:
                rows = _read_csv(path)
            except Exception as exc:
                print(f"[ingest_daily] failed to read {path.name}: {exc}")
                continue
            upload_id = _insert_upload(conn, Json, path.name, "csv", rows)
            print(
                f"[ingest_daily] ingested {path.name}: "
                f"{len(rows)} rows → upload_id={upload_id}"
            )
            total_rows += len(rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return {"files_seen": len(csv_files), "rows_ingested": total_rows}


# ---- Task: run_dbt -------------------------------------------------------


def run_dbt(**_context) -> int:
    """Invoke ``dbt run`` against /opt/airflow/dbt.

    Returns the dbt process exit code. Raises ``RuntimeError`` if dbt
    is not installed or the project dir is missing.
    """
    if not DBT_PROJECT_DIR.exists():
        raise RuntimeError(f"dbt project dir not found: {DBT_PROJECT_DIR}")
    if shutil.which("dbt") is None:
        raise RuntimeError(
            "dbt executable not found on PATH; "
            "install dbt-postgres into the Airflow image."
        )

    cmd = [
        "dbt", "run",
        "--project-dir", str(DBT_PROJECT_DIR),
        "--profiles-dir", str(DBT_PROJECT_DIR),
    ]
    print(f"[ingest_daily] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"[ingest_daily] dbt run failed:\n{result.stderr}", file=sys.stderr)
        raise RuntimeError(f"dbt run failed with exit code {result.returncode}")
    return result.returncode


# ---- DAG definition ------------------------------------------------------

default_args = {
    "owner": "fin-bp",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": 5,  # minutes
}


with DAG(
    dag_id="finbp_ingest_daily",
    description="Daily CSV landing-zone ingest + dbt run",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule_interval="@daily",
    catchup=False,
    tags=["finbp", "ingest", "dbt"],
) as dag:
    t_ingest = PythonOperator(
        task_id="ingest_csv_landing",
        python_callable=ingest_csv_landing,
    )
    t_dbt = PythonOperator(
        task_id="run_dbt",
        python_callable=run_dbt,
    )

    t_ingest >> t_dbt
