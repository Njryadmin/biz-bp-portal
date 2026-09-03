"""
apps/api/app/services/scrapers/scrapers/_html.py

Shared HTML helpers for the bundled scrapers. Kept under ``scrapers/``
(bot ``_html.py``) so it is NOT auto-registered (the discovery code
skips modules prefixed with ``_``).
"""
from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup


def parse_html(html: str) -> BeautifulSoup:
    """Parse a HTML string with ``lxml`` if available, falling back to ``html.parser``.

    ``bs4`` + ``html.parser`` are part of the stdlib ecosystem; ``lxml``
    is faster and installed in this project's environment.
    """
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return BeautifulSoup(html, "html.parser")


def first_text(soup: BeautifulSoup, *selectors: str) -> str:
    """Return the stripped text of the first selector that matches, or ""."""
    for sel in selectors:
        node = soup.select_one(sel)
        if node:
            return node.get_text(strip=True)
    return ""


def table_to_dicts(table_elem: Any) -> list[dict[str, str]]:
    """Convert a ``<table>`` to a list of dicts using the first row as header.

    Empty cells become "". The header row's first cell must contain text;
    tables without headers return an empty list.
    """
    if table_elem is None:
        return []
    rows = table_elem.find_all("tr")
    if not rows:
        return []
    header_cells = rows[0].find_all(["th", "td"])
    if not header_cells:
        return []
    headers = [c.get_text(strip=True) for c in header_cells]
    # If the header row is empty (typical for NBS data tables where the
    # header is on a previous paragraph), bail and let the caller pass
    # an explicit header list.
    if not any(headers):
        return []
    out: list[dict[str, str]] = []
    for r in rows[1:]:
        cells = r.find_all(["td", "th"])
        if not cells:
            continue
        # Skip fully-empty rows.
        if all(not c.get_text(strip=True) for c in cells):
            continue
        record: dict[str, str] = {}
        for i, c in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col_{i}"
            record[key] = c.get_text(strip=True)
        out.append(record)
    return out
