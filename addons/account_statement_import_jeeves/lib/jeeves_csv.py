"""Jeeves Activity / statement CSV parser (Odoo-independent).

Jeeves does not publish a stable bank-feed API. Exports from
Activity & Exports and from credit statements use slightly different
headers; this module normalizes both.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Iterable

PENDING_STATUSES = {"pending", "authorization", "authorised", "authorized"}
CREDIT_HINTS = {"credit", "refund", "payment", "reimbursement", "cashback"}
DEBIT_HINTS = {"debit", "charge", "purchase", "fee", "withdrawal"}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": (
        "transaction id",
        "transaction_id",
        "transactionid",
        "id",
        "reference id",
        "reference_id",
        "jeeves id",
        "txn id",
        "txn_id",
    ),
    "date": (
        "posted date",
        "posted_date",
        "settled date",
        "settlement date",
        "transaction date",
        "date",
        "created date",
        "created_at",
        "completed date",
    ),
    "amount": (
        "amount",
        "billing amount",
        "settled amount",
        "net amount",
        "transaction amount",
        "local amount",
    ),
    "currency": (
        "currency",
        "billing currency",
        "settled currency",
        "transaction currency",
    ),
    "merchant": (
        "merchant",
        "merchant name",
        "description",
        "vendor",
        "counterparty",
        "name",
    ),
    "status": (
        "status",
        "transaction status",
        "state",
        "posted status",
    ),
    "tx_type": (
        "type",
        "transaction type",
        "debit/credit",
        "debit credit",
        "direction",
    ),
    "notes": (
        "memo",
        "notes",
        "note",
        "comment",
        "category",
    ),
    "account": (
        "account",
        "account name",
        "card",
        "card last 4",
        "last 4",
    ),
}

DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
)


class JeevesCSVError(ValueError):
    """CSV is not a usable Jeeves activity/statement export."""


def _norm_header(value: str) -> str:
    return " ".join(value.strip().lower().replace("_", " ").replace("-", " ").split())


def _build_header_map(headers: Iterable[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    normalized = {_norm_header(header): header for header in headers}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[field] = normalized[alias]
                break
    return mapping


def detect_jeeves_csv(raw: bytes | str) -> bool:
    """True when the header looks like a Jeeves activity or statement export."""
    try:
        headers, _rows = _read_table(raw)
    except Exception:  # noqa: BLE001 - detection must never raise
        return False
    mapping = _build_header_map(headers)
    has_amount = "amount" in mapping
    has_date = "date" in mapping
    identity = {"transaction_id", "merchant", "status", "currency"}
    return has_amount and has_date and bool(identity.intersection(mapping))


def _read_table(raw: bytes | str) -> tuple[list[str], list[dict[str, str]]]:
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise JeevesCSVError("CSV has no header row")
    rows = [{key: (value or "").strip() for key, value in row.items() if key} for row in reader]
    return list(reader.fieldnames), rows


def _parse_amount(value: str) -> float:
    cleaned = value.strip()
    if not cleaned:
        raise JeevesCSVError("Empty amount")
    cleaned = cleaned.replace("€", "").replace("$", "").replace("£", "").strip()
    if cleaned.startswith("(") and cleaned.endswith(")"):
        cleaned = f"-{cleaned[1:-1]}"
    if cleaned.count(",") == 1 and cleaned.count(".") == 0:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(",") == 1 and cleaned.count(".") >= 1:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    return float(cleaned.replace(" ", ""))


def _parse_date(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise JeevesCSVError("Empty date")
    candidates = [text]
    if text.endswith("Z") and "T" in text:
        candidates.append(text[:-1])
    if "T" in text:
        candidates.append(text.split("T", 1)[0])
    if " " in text:
        candidates.append(text.split(" ", 1)[0])
    for candidate in candidates:
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except ValueError as error:
        raise JeevesCSVError(f"Unrecognised date: {value}") from error


def _is_pending(status: str) -> bool:
    return status.strip().lower() in PENDING_STATUSES


def _signed_amount(amount: float, tx_type: str, invert_card_charges: bool) -> float:
    """Card activity exports usually list purchases as positive numbers."""
    kind = tx_type.strip().lower()
    if any(hint in kind for hint in CREDIT_HINTS) and not any(
        hint in kind for hint in DEBIT_HINTS
    ):
        return abs(amount)
    if invert_card_charges and amount > 0 and kind in ("", "debit", "charge", "purchase"):
        return -amount
    if invert_card_charges and amount < 0 and any(hint in kind for hint in CREDIT_HINTS):
        return abs(amount)
    return amount


def parse_jeeves_csv(
    raw: bytes | str,
    *,
    invert_card_charges: bool = True,
    skip_pending: bool = True,
) -> list[dict[str, Any]]:
    headers, rows = _read_table(raw)
    mapping = _build_header_map(headers)
    missing = [field for field in ("date", "amount") if field not in mapping]
    if missing:
        raise JeevesCSVError(f"Jeeves CSV is missing columns: {', '.join(missing)}")

    lines: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not any(row.values()):
            continue
        status = row.get(mapping.get("status", ""), "")
        if skip_pending and _is_pending(status):
            continue
        amount_raw = row.get(mapping["amount"], "")
        if not amount_raw:
            continue
        amount = _signed_amount(
            _parse_amount(amount_raw),
            row.get(mapping.get("tx_type", ""), ""),
            invert_card_charges,
        )
        when = _parse_date(row[mapping["date"]])
        tx_id = row.get(mapping.get("transaction_id", ""), "") or f"jeeves-row-{index}"
        merchant = row.get(mapping.get("merchant", ""), "")
        notes = row.get(mapping.get("notes", ""), "")
        currency = row.get(mapping.get("currency", ""), "")
        lines.append(
            {
                "date": when,
                "payment_ref": merchant or notes or tx_id,
                "ref": tx_id,
                "unique_import_id": tx_id,
                "amount": amount,
                "partner_name": merchant or False,
                "narration": notes or False,
                "currency_code": currency or False,
                "transaction_type": row.get(mapping.get("tx_type", ""), "") or False,
                "account_label": row.get(mapping.get("account", ""), "") or False,
            }
        )
    return lines


def statement_from_rows(
    lines: list[dict[str, Any]],
    *,
    name: str = "Jeeves",
) -> tuple[str | bool, str | bool, list[dict[str, Any]]]:
    """Return the OCA ``_parse_file`` triple."""
    if not lines:
        return False, False, []
    currencies = {line.get("currency_code") for line in lines if line.get("currency_code")}
    currency = next(iter(currencies)) if len(currencies) == 1 else False
    dates = [line["date"] for line in lines]
    transactions = []
    for line in lines:
        values = dict(line)
        values.pop("account_label", None)
        values.pop("currency_code", None)
        transactions.append(values)
    statement = {
        "name": f"{name}/{min(dates).date().isoformat()}",
        "date": max(dates).date(),
        "transactions": transactions,
    }
    return currency, False, [statement]
