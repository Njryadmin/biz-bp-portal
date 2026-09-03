"""
apps/api/app/services/scrapers/persist.py

Persists a scraper's parsed rows into ``raw.uploads`` as a single
JSONB payload.

Schema notes:
    * ``upload_type = 'scraper'`` (we extend the CHECK constraint in
      ``db.bootstrap.ensure_raw_schema`` so this value is allowed).
    * ``filename`` is set to ``<source_id>__<timestamp>.json`` so the
      DBT staging models can group by it.
    * ``payload`` is the JSON array of rows.
    * ``row_count`` is the array length.

The function is sync-from-the-caller's-perspective (await-able) and
never raises — DB failures are logged and ``None`` is returned.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from ...core.logging import get_logger
from ...db import get_session

logger = get_logger(__name__)


async def persist_scraper_rows(
    source_id: str,
    rows: list[dict[str, Any]],
    run_status: str = "ok",
) -> str | None:
    """Insert one row into ``raw.uploads`` for a scraper run.

    Returns the generated ``upload_id`` or None when no rows / on error.

    ``run_status`` is one of ``ok`` / ``degraded`` / ``error`` and is
    stored in the new ``raw.uploads.run_status`` column so the
    dashboard tile can distinguish a clean live run from one that
    had to fall back to mock data.
    """
    if not rows:
        return None
    upload_id = (
        f"sc_{source_id}_"
        f"{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_"
        f"{uuid.uuid4().hex[:8]}"
    )
    filename = f"{source_id}__{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    payload_json = json.dumps(rows, default=str, ensure_ascii=False)
    try:
        async with get_session() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO raw.uploads
                        (upload_id, filename, upload_type, row_count,
                         source, fetched_at, run_status, payload)
                    VALUES
                        (:upload_id, :filename, :upload_type, :row_count,
                         :source, NOW(), :run_status, CAST(:payload AS JSONB))
                    """
                ),
                {
                    "upload_id": upload_id,
                    "filename": filename,
                    "upload_type": "scraper",
                    "row_count": len(rows),
                    "source": source_id,
                    "run_status": run_status,
                    "payload": payload_json,
                },
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist_scraper_rows failed for %s: %s", source_id, exc)
        return None
    return upload_id


async def scraper_history(source_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` scraper runs for a source_id.

    Joins on ``upload_id`` LIKE 'sc_<source_id>_%' since the upload_id
    convention encodes the source.
    """
    sql = text(
        """
        SELECT upload_id, filename, row_count, uploaded_at, run_status
        FROM raw.uploads
        WHERE upload_id LIKE :pattern
        ORDER BY uploaded_at DESC
        LIMIT :limit
        """
    )
    try:
        async with get_session() as session:
            result = await session.execute(
                sql,
                {"pattern": f"sc_{source_id}_%", "limit": limit},
            )
            rows = result.mappings().all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("scraper_history failed for %s: %s", source_id, exc)
        return []
    return [
        {
            "upload_id": r["upload_id"],
            "filename": r["filename"],
            "row_count": r["row_count"],
            "uploaded_at": r["uploaded_at"].isoformat() if r["uploaded_at"] else None,
            "run_status": r.get("run_status"),
        }
        for r in rows
    ]


async def last_scraper_run(source_id: str) -> dict[str, Any] | None:
    """Return the single most recent run for a source_id (or None)."""
    sql = text(
        """
        SELECT upload_id, filename, row_count, uploaded_at, run_status
        FROM raw.uploads
        WHERE upload_id LIKE :pattern
        ORDER BY uploaded_at DESC
        LIMIT 1
        """
    )
    try:
        async with get_session() as session:
            result = await session.execute(
                sql,
                {"pattern": f"sc_{source_id}_%"},
            )
            row = result.mappings().first()
    except Exception as exc:  # noqa: BLE001
        logger.warning("last_scraper_run failed for %s: %s", source_id, exc)
        return None
    if not row:
        return None
    return {
        "upload_id": row["upload_id"],
        "filename": row["filename"],
        "row_count": row["row_count"],
        "uploaded_at": row["uploaded_at"].isoformat() if row["uploaded_at"] else None,
        "run_status": row.get("run_status"),
    }
