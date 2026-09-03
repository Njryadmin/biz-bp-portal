# apps/api/app/services/parsers/__init__.py
"""File parsers used by the data-integration upload router and the
Airflow ingest DAG.

Public API (re-exported for convenience):
    parse_excel(source)            -> list[dict]
    parse_csv(source)              -> list[dict]
    parse_bank_statement(source)   -> list[dict]

Each function accepts either a filesystem path (``str`` / ``Path``) or
raw file ``bytes`` and returns a list of plain-Python dicts that can be
JSON-serialized.
"""
from .excel_parser import parse_excel
from .csv_parser import parse_csv
from .bank_statement import parse_bank_statement

__all__ = ["parse_excel", "parse_csv", "parse_bank_statement"]
