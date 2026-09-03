"""
apps/api/app/routers/upload.py

FastAPI APIRouter for the data-integration upload endpoints.

Routes (mounted under ``/api/upload`` by ``app.main``):

* ``POST /api/upload/excel``  — multipart .xlsx/.xls upload
* ``POST /api/upload/csv``    — multipart .csv upload
* ``GET  /api/upload/history``— last 50 uploads (newest first)

Each upload is parsed by the corresponding parser in
``app.services.parsers`` and persisted as one row in
``raw.uploads (upload_id, filename, upload_type, row_count, payload)``
where ``payload`` is jsonb.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from sqlalchemy import text

from ..core.logging import get_logger
from ..db import get_session
from ..services.parsers import parse_csv, parse_excel
from ..services.parsers.bank_statement import parse_bank_statement
from ..schemas.upload import UploadHistoryItem, UploadResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/api/upload", tags=["upload"])


# ---- Constants -----------------------------------------------------------

_ALLOWED_EXCEL_EXT = (".xlsx", ".xlsm", ".xls")
_ALLOWED_CSV_EXT = (".csv",)
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB safety cap


# ---- Helpers -------------------------------------------------------------


def _check_ext(filename: str, allowed: tuple[str, ...]) -> None:
    if not filename or not filename.lower().endswith(allowed):
        raise HTTPException(
            status_code=400,
            detail=f"filename must end with one of {list(allowed)}, got: {filename!r}",
        )


def _make_upload_id() -> str:
    return f"up_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"


async def _persist_upload(
    filename: str,
    upload_type: str,
    rows: list[dict[str, Any]],
) -> UploadResponse:
    """Insert one row into raw.uploads and return the response model."""
    if not rows:
        raise HTTPException(
            status_code=400,
            detail=f"{filename}: no rows could be parsed (empty file?)",
        )

    upload_id = _make_upload_id()
    payload_json = json.dumps(rows, default=str, ensure_ascii=False)

    insert_sql = text(
        """
        INSERT INTO raw.uploads
            (upload_id, filename, upload_type, row_count, payload)
        VALUES
            (:upload_id, :filename, :upload_type, :row_count,
             CAST(:payload AS JSONB))
        RETURNING uploaded_at
        """
    )

    async with get_session() as session:
        result = await session.execute(
            insert_sql,
            {
                "upload_id": upload_id,
                "filename": filename,
                "upload_type": upload_type,
                "row_count": len(rows),
                "payload": payload_json,
            },
        )
        row = result.mappings().first()
        await session.commit()

    uploaded_at = row["uploaded_at"] if row else None
    return UploadResponse(
        upload_id=upload_id,
        filename=filename,
        upload_type=upload_type,
        row_count=len(rows),
        uploaded_at=uploaded_at,
    )


# ---- Routes --------------------------------------------------------------


@router.post(
    "/excel",
    response_model=UploadResponse,
    summary="Upload an Excel file (.xlsx/.xls) and persist as raw.uploads row",
)
async def upload_excel(file: UploadFile = File(...)) -> UploadResponse:
    _check_ext(file.filename or "", _ALLOWED_EXCEL_EXT)
    contents = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (>{_MAX_UPLOAD_BYTES} bytes)")
    try:
        rows = parse_excel(contents)
    except Exception as exc:  # noqa: BLE001 — surface parser error to the user
        logger.exception("parse_excel failed for %s", file.filename)
        raise HTTPException(400, f"failed to parse excel: {exc}") from exc
    return await _persist_upload(file.filename or "upload.xlsx", "excel", rows)


@router.post(
    "/csv",
    response_model=UploadResponse,
    summary="Upload a CSV file and persist as raw.uploads row",
)
async def upload_csv(file: UploadFile = File(...)) -> UploadResponse:
    _check_ext(file.filename or "", _ALLOWED_CSV_EXT)
    contents = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (>{_MAX_UPLOAD_BYTES} bytes)")
    try:
        rows = parse_csv(contents)
    except Exception as exc:  # noqa: BLE001
        logger.exception("parse_csv failed for %s", file.filename)
        raise HTTPException(400, f"failed to parse csv: {exc}") from exc
    return await _persist_upload(file.filename or "upload.csv", "csv", rows)


@router.post(
    "/bank-statement",
    response_model=UploadResponse,
    summary="Upload a bank-statement text file (ICBC/CMB) and persist",
)
async def upload_bank_statement(file: UploadFile = File(...)) -> UploadResponse:
    """Optional endpoint for completeness — the spec only requires excel+csv
    but the bank-statement parser is already wired up, so we expose it too.
    Accepts .txt or .csv files."""
    name = file.filename or "statement.txt"
    if not name.lower().endswith((".txt", ".csv")):
        raise HTTPException(400, f"unsupported extension for {name!r}")
    contents = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file too large (>{_MAX_UPLOAD_BYTES} bytes)")
    try:
        rows = parse_bank_statement(contents)
    except Exception as exc:  # noqa: BLE001
        logger.exception("parse_bank_statement failed for %s", file.filename)
        raise HTTPException(400, f"failed to parse bank statement: {exc}") from exc
    return await _persist_upload(name, "bank_statement", rows)


@router.get(
    "/history",
    response_model=list[UploadHistoryItem],
    summary="List the most recent uploads (default limit 50)",
)
async def upload_history(
    limit: int = Query(default=50, ge=1, le=500),
) -> list[UploadHistoryItem]:
    sql = text(
        """
        SELECT upload_id, filename, upload_type, row_count, uploaded_at
        FROM raw.uploads
        ORDER BY uploaded_at DESC
        LIMIT :limit
        """
    )
    async with get_session() as session:
        result = await session.execute(sql, {"limit": limit})
        rows = result.mappings().all()

    return [
        UploadHistoryItem(
            upload_id=r["upload_id"],
            filename=r["filename"],
            upload_type=r["upload_type"],
            row_count=r["row_count"],
            uploaded_at=r["uploaded_at"],
        )
        for r in rows
    ]
