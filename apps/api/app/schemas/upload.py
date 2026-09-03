"""
apps/api/app/schemas/upload.py

Pydantic response models for the /api/upload/* endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """Returned by POST /api/upload/{excel,csv} on success."""

    upload_id: str
    filename: str
    upload_type: str
    row_count: int
    status: str = "ok"
    uploaded_at: Optional[datetime] = None


class UploadHistoryItem(BaseModel):
    """One row in GET /api/upload/history."""

    upload_id: str
    filename: str
    upload_type: str
    row_count: int
    uploaded_at: datetime
    byte_size: Optional[int] = None
