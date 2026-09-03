"""
infra/airflow/dags/scrape_weekly.py

Weekly scrape DAG: runs every enabled scraper from the Fin BP Portal
web-scraping framework, persists results to ``raw.uploads`` and writes
the raw JSON snapshot to ``data/landing/scrapers/<source_id>/<date>.json``.

Schedule: ``@weekly`` (Sundays 00:00 UTC).

The DAG is intentionally framework-light: a single PythonOperator that
calls into the FastAPI app's scraper framework. The DAG also runs
``dbt run`` after the scrape, mirroring the pattern from
``ingest_daily.py``.
"""
from __future__ import annotations

import asyncio
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

LANDING_DIR = Path(os.environ.get("SCRAPER_LANDING_DIR", "/data/landing/scrapers"))
DBT_PROJECT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", "/opt/airflow/dbt"))
API_APP_DIR = Path(os.environ.get("BIZ_BP_API_DIR", "/opt/airflow/api"))
PG_HOST = os.environ.get("POSTGRES_HOST", "postgres")
PG_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_USER = os.environ.get("POSTGRES_USER", "finbp")
PG_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "finbp")
PG_DB = os.environ.get("POSTGRES_DB", "finbp")


# ---- DAG-level DDL: drop the old CHECK & add the scraper variant -------

SCHEMA_DDL = [
    "CREATE SCHEMA IF NOT EXISTS raw",
    """
    CREATE TABLE IF NOT EXISTS raw.uploads (
        id          BIGSERIAL PRIMARY KEY,
        upload_id   TEXT NOT NULL UNIQUE,
        filename    TEXT NOT NULL,
        upload_type TEXT NOT NULL
                    CHECK (upload_type IN ('excel', 'csv', 'bank_statement', 'scraper')),
        row_count   INTEGER NOT NULL,
        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        fetched_at  TIMESTAMPTZ,
        source      TEXT,
        payload     JSONB NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_raw_uploads_uploaded_at "
    "ON raw.uploads (uploaded_at DESC)",
    "ALTER TABLE raw.uploads ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMPTZ",
    "ALTER TABLE raw.uploads ADD COLUMN IF NOT EXISTS source TEXT",
    "CREATE INDEX IF NOT EXISTS idx_raw_uploads_source "
    "ON raw.uploads (source) WHERE source IS NOT NULL",
    """
    DO $$
    BEGIN
        ALTER TABLE raw.uploads DROP CONSTRAINT IF EXISTS raw_uploads_upload_type_check;
        ALTER TABLE raw.uploads
            ADD CONSTRAINT raw_uploads_upload_type_check
            CHECK (upload_type IN ('excel', 'csv', 'bank_statement', 'scraper'));
    EXCEPTION WHEN OTHERS THEN NULL;
    END$$;
    """,
]


def _connect():
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB,
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


# ---- Task 1: scrape_all --------------------------------------------------


def _run_one_sync(source_id: str) -> dict:
    """Synchronous wrapper that runs the async framework and inserts into raw.uploads."""
    import psycopg2
    import psycopg2.extras

    sys.path.insert(0, str(API_APP_DIR))

    from app.services.scrapers import get, discover_scrapers
    from app.services.scrapers.persist import persist_scraper_rows

    discover_scrapers()
    s = get(source_id)
    if s is None:
        return {"source_id": source_id, "status": "error", "error": "unknown"}

    # Async run, persisted via the framework.
    result = asyncio.run(s.run())
    raw_rows: list[dict] = []
    try:
        raw = asyncio.run(s.fetch())
    except Exception:
        raw = []
    if not raw:
        try:
            raw = s.fallback()
        except Exception:
            raw = []
    parsed = s.validate(s.parse(raw))
    landing_rows = [s.to_landing_row(r) for r in parsed]

    # Also write a JSON snapshot to data/landing/scrapers/<source_id>/<date>.json
    out_dir = LANDING_DIR / source_id
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snap = {
        "source_id": source_id,
        "fetched_at": result.fetched_at,
        "rows": landing_rows,
        "used_fallback": result.used_fallback,
        "status": result.status,
        "error": result.error,
    }
    snap_path = out_dir / f"{today}.json"
    snap_path.write_text(
        json.dumps(snap, default=str, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Persist to Postgres (synchronous via psycopg2). If the API is on
    # another host this is the same database the API writes to; we
    # can't call into the async stack from the DAG without a running
    # event loop, so we replicate the insert here.
    conn, Json = _connect()
    upload_id = None
    try:
        if landing_rows:
            upload_id = (
                f"sc_{source_id}_"
                f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S_}"
                f"{abs(hash(source_id)) % 10000:04d}"
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO raw.uploads
                        (upload_id, filename, upload_type, row_count,
                         source, fetched_at, payload)
                    VALUES (%s, %s, %s, %s, %s, NOW(), %s)
                    ON CONFLICT (upload_id) DO UPDATE
                        SET row_count = EXCLUDED.row_count,
                            payload   = EXCLUDED.payload,
                            source    = EXCLUDED.source
                    """,
                    (
                        upload_id,
                        f"{source_id}__{today}.json",
                        "scraper",
                        len(landing_rows),
                        source_id,
                        Json(landing_rows),
                    ),
                )
            conn.commit()
    finally:
        conn.close()

    return {
        "source_id": source_id,
        "name": result.name,
        "status": result.status,
        "rows": result.rows,
        "used_fallback": result.used_fallback,
        "upload_id": upload_id,
        "snapshot": str(snap_path),
    }


def scrape_all(**_context) -> dict:
    """Discover every scraper, run them, and persist."""
    _ensure_schema()

    sys.path.insert(0, str(API_APP_DIR))
    from app.services.scrapers import discover_scrapers
    from app.services.scrapers.registry import get_all

    discover_scrapers()
    scrapers = get_all()
    if not scrapers:
        print("[scrape_weekly] no scrapers registered; nothing to do")
        return {"sources": 0, "rows": 0}

    total_rows = 0
    summary: list[dict] = []
    for s in scrapers:
        if not getattr(s, "enabled", True):
            print(f"[scrape_weekly] skip disabled: {s.source_id}")
            continue
        print(f"[scrape_weekly] running: {s.source_id}")
        try:
            r = _run_one_sync(s.source_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[scrape_weekly] {s.source_id} failed: {exc}")
            r = {"source_id": s.source_id, "status": "error", "error": str(exc)}
        print(f"[scrape_weekly] {s.source_id} -> {r}")
        summary.append(r)
        total_rows += int(r.get("rows") or 0)
    return {"sources": len(summary), "rows": total_rows, "summary": summary}


# ---- Task 2: dbt run -----------------------------------------------------


def run_dbt(**_context) -> int:
    if not DBT_PROJECT_DIR.exists():
        raise RuntimeError(f"dbt project dir not found: {DBT_PROJECT_DIR}")
    if shutil.which("dbt") is None:
        raise RuntimeError("dbt executable not found on PATH")
    cmd = [
        "dbt", "run",
        "--project-dir", str(DBT_PROJECT_DIR),
        "--profiles-dir", str(DBT_PROJECT_DIR),
    ]
    print(f"[scrape_weekly] running: {' '.join(cmd)}")
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"[scrape_weekly] dbt run failed:\n{result.stderr}", file=sys.stderr)
        raise RuntimeError(f"dbt run failed with exit code {result.returncode}")
    return result.returncode


# ---- DAG definition -----------------------------------------------------

default_args = {
    "owner": "fin-bp",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": 10,  # minutes
}


with DAG(
    dag_id="finbp_scrape_weekly",
    description="Weekly scrape: all enabled web scrapers + dbt run",
    default_args=default_args,
    start_date=datetime(2026, 1, 1),
    schedule_interval="@weekly",
    catchup=False,
    tags=["finbp", "scrape", "dbt"],
) as dag:
    t_scrape = PythonOperator(
        task_id="scrape_all",
        python_callable=scrape_all,
    )
    t_dbt = PythonOperator(
        task_id="run_dbt",
        python_callable=run_dbt,
    )
    t_scrape >> t_dbt
