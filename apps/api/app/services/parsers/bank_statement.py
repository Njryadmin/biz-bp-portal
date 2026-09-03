"""
apps/api/app/services/parsers/bank_statement.py

Regex-based bank-statement parser. Supports at least:

* 中国工商银行 (ICBC) — most common format: ``日期 时间 摘要 金额 余额``
* 招商银行 (CMB)   — tab-separated: ``日期\\t摘要\\t±金额\\t余额``

Public API:
    parse_bank_statement(source) -> list[dict]

Args:
    source: filesystem path (``str`` / ``Path``) or raw file ``bytes``.

Returns:
    list of dicts with keys: ``date``, ``time``, ``description``,
    ``amount`` (float, always >= 0), ``balance`` (float), ``direction``
    (``"in"`` / ``"out"`` / ``"neutral"``), ``bank`` (``"ICBC"`` /
    ``"CMB"`` / ``"UNKNOWN"``).

Bank detection is done by keyword scan in the first 2 KB of the file;
if no keyword matches the parser falls back to ICBC.
"""
from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path
from typing import Any

# ---- Bank detection -------------------------------------------------------

_ICBC_KEYWORDS = ("工商银行", "ICBC")
_CMB_KEYWORDS = ("招商银行", "CMB")


def _detect_bank(text: str) -> str:
    head = text[:2000]
    for kw in _CMB_KEYWORDS:
        if kw in head:
            return "CMB"
    for kw in _ICBC_KEYWORDS:
        if kw in head:
            return "ICBC"
    return "UNKNOWN"


# ---- ICBC line pattern ----------------------------------------------------
# Sample line:  2026-01-05    09:23:15    工资收入       15000.00      18500.50
# The "余额" keyword is optional; balances may appear without it.
_ICBC_LINE_RE = re.compile(
    r"^(?P<date>\d{4}[-/]\d{2}[-/]\d{2})"
    r"(?:[\sT]+(?P<time>\d{2}:\d{2}:\d{2}))?"
    r"\s+(?P<description>.+?)"
    r"\s+(?P<amount>[\d,]+\.\d{2})"
    r"\s+(?:余额\s+)?"
    r"(?P<balance>[\d,]+\.\d{2})\s*$",
    re.MULTILINE,
)


# ---- CMB line pattern -----------------------------------------------------
# Sample line:  2026/01/05\t工资\t+15000.00\t18500.50
_CMB_LINE_RE = re.compile(
    r"^(?P<date>\d{4}/\d{2}/\d{2})\t"
    r"(?P<description>[^\t]+?)\t"
    r"(?P<signed>[+-][\d,]+\.\d{2})\t"
    r"(?P<balance>[\d,]+\.\d{2})\s*$",
    re.MULTILINE,
)


# ---- Direction inference --------------------------------------------------

_INCOME_KW = ("收入", "存入", "转入", "退款", "红包", "赎回", "发工资", "到账")
_EXPENSE_KW = ("支出", "转出", "取现", "消费", "还款", "代扣", "支付", "扣款")


def _infer_direction(description: str, signed_amount: Decimal) -> str:
    if signed_amount < 0:
        return "out"
    if signed_amount > 0:
        # Check keywords to disambiguate 收入 vs 支出 when sign is missing.
        for kw in _EXPENSE_KW:
            if kw in description:
                return "out"
        for kw in _INCOME_KW:
            if kw in description:
                return "in"
        return "in"
    return "neutral"


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", ""))


# ---- Public entry point ---------------------------------------------------


def parse_bank_statement(source: str | bytes | Path) -> list[dict[str, Any]]:
    """Parse a bank-statement text file. See module docstring."""
    if isinstance(source, bytes):
        text = source.decode("utf-8", errors="replace")
    else:
        text = Path(source).read_text(encoding="utf-8", errors="replace")

    bank = _detect_bank(text)
    if bank == "CMB":
        return _parse_cmb(text)
    # Fall back to ICBC for both ICBC and UNKNOWN — it is the most permissive
    # pattern and the caller's job to validate bank detection if needed.
    return _parse_icbc(text)


# ---- ICBC parser ---------------------------------------------------------


def _parse_icbc(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in _ICBC_LINE_RE.finditer(text):
        date_str = m.group("date").replace("/", "-")
        time_str = m.group("time")
        desc = m.group("description").strip()
        amount = _to_decimal(m.group("amount"))
        balance = _to_decimal(m.group("balance"))
        rows.append(
            {
                "date": date_str,
                "time": time_str,
                "description": desc,
                "amount": float(amount),
                "balance": float(balance),
                "direction": _infer_direction(desc, amount),
                "bank": "ICBC",
            }
        )
    return rows


# ---- CMB parser ----------------------------------------------------------


def _parse_cmb(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for m in _CMB_LINE_RE.finditer(text):
        date_str = m.group("date").replace("/", "-")
        desc = m.group("description").strip()
        signed = _to_decimal(m.group("signed"))
        balance = _to_decimal(m.group("balance"))
        amount = abs(signed)
        rows.append(
            {
                "date": date_str,
                "time": None,
                "description": desc,
                "amount": float(amount),
                "balance": float(balance),
                "direction": _infer_direction(desc, signed),
                "bank": "CMB",
            }
        )
    return rows
