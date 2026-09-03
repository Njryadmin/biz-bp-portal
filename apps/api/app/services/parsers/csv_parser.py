"""
apps/api/app/services/parsers/csv_parser.py

pandas-based CSV parser. Returns a list of plain dicts.

Public API:
    parse_csv(source, **pandas_kwargs) -> list[dict]

Args:
    source: filesystem path (``str`` / ``Path``) or raw file ``bytes``.
    **pandas_kwargs: forwarded to ``pandas.read_csv``.

Behaviour:
    * UTF-8 BOM (\\xef\\xbb\\xbf) is stripped automatically when reading bytes.
    * Empty values are converted to ``None`` (not ``NaN``) so the result is
      strictly JSON-compatible.
    * Column names are stripped of leading/trailing whitespace.
"""
from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import pandas as pd


def _read_text(source: str | bytes | Path) -> str:
    """Decode bytes (handling UTF-8 BOM) or read a text file."""
    if isinstance(source, bytes):
        if source.startswith(b"\xef\xbb\xbf"):
            return source.decode("utf-8-sig")
        return source.decode("utf-8", errors="replace")
    p = Path(source)
    raw = p.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("utf-8", errors="replace")


def parse_csv(
    source: str | bytes | Path,
    **pandas_kwargs: Any,
) -> list[dict[str, Any]]:
    """Parse a CSV file into a list of dicts.

    See module docstring for details.
    """
    text = _read_text(source)
    # Sensible defaults; caller can override via **pandas_kwargs.
    kwargs: dict[str, Any] = {
        "keep_default_na": False,  # treat empty cells as "" not NaN
        "na_values": ["", "NA", "N/A", "null", "NULL", "None"],
    }
    kwargs.update(pandas_kwargs)
    df = pd.read_csv(StringIO(text), **kwargs)

    # Strip whitespace from column names.
    df.columns = [str(c).strip() for c in df.columns]

    # Replace any remaining pd.NA / NaN with None for clean JSON output.
    df = df.astype(object).where(pd.notnull(df), None)

    return df.to_dict(orient="records")
