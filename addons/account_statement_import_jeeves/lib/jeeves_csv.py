"""Jeeves Activity / statement CSV parser (Odoo-independent).

Jeeves does not publish a stable bank-feed API. Live *Activity and Exports*
files use ``Unique ID``, ``Posted At UTC``, ``Credit or Debit``, ``Payee``.
Older activity and credit-statement exports use shorter headers. This
module normalizes both.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any, Iterable

PENDING_STATUSES = {
    "pending",
    "authorization",
    "authorised",
    "authorized",
    "failed",
    "cancelled",
    "canceled",
    "declined",
}
CREDIT_HINTS = {"credit", "refund", "reimbursement", "cashback", "deposit"}
DEBIT_HINTS = {
    "debit",
    "charge",
    "purchase",
    "fee",
    "withdrawal",
    "withdraw",
    "payment",
}

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "transaction_id": (
        "unique id",
        "transaction id",
        "transaction_id",
        "transactionid",
        "reference id",
        "reference_id",
        "jeeves id",
        "txn id",
        "txn_id",
        "id",
    ),
    "date": (
        "posted at utc",
        "posted at",
        "posted date",
        "posted_date",
        "settled date",
        "settlement date",
        "transaction date",
        "created at utc",
        "created at",
        "created_at",
        "created date",
        "completed date",
        "date",
    ),
    "amount": (
        "amount (origin currency)",
        "amount origin currency",
        "billing amount",
        "settled amount",
        "net amount",
        "transaction amount",
        "local amount",
        "amount",
    ),
    "currency": (
        "currency",
        "billing currency",
        "settled currency",
        "transaction currency",
        "origin currency",
    ),
    "merchant": (
        "payee",
        "merchant",
        "merchant name",
        "vendor",
        "counterparty",
        "name",
    ),
    "status": (
        "transaction status",
        "status",
        "state",
        "posted status",
    ),
    "direction": (
        "credit or debit",
        "debit or credit",
        "debit/credit",
        "debit credit",
    ),
    "tx_type": (
        "transaction type",
        "type",
        "direction",
    ),
    "sub_type": ("sub transaction type",),
    "notes": (
        "memo",
        "notes",
        "note",
        "comment",
    ),
    "payment_description": ("payment description",),
    "invoice_number": ("invoice number",),
    "invoice_id": ("invoice id",),
    "vendor_email": ("vendor email",),
    "vendor_id": ("vendor id",),
    "category": ("category",),
    "account": (
        "source account",
        "account name",
        "account",
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

_LINE_EXTRA_KEYS = (
    "account_label",
    "currency_code",
    "partner_email",
    "jeeves_vendor_id",
    "invoice_number",
    "jeeves_invoice_id",
    "jeeves_payment_reference",
    "jeeves_invoice_status",
)


class JeevesCSVError(ValueError):
    """CSV is not a usable Jeeves activity/statement export."""


def _norm_header(value: str) -> str:
    text = value.strip().lower().replace("_", " ").replace("-", " ")
    text = text.replace("(", " ").replace(")", " ")
    return " ".join(text.split())


def _find_column(normalized: dict[str, str], aliases: tuple[str, ...]) -> str | None:
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def _build_header_map(headers: Iterable[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    normalized = {_norm_header(header): header for header in headers if header}
    for field, aliases in COLUMN_ALIASES.items():
        found = _find_column(normalized, aliases)
        if found:
            mapping[field] = found
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
    identity = {
        "transaction_id",
        "merchant",
        "status",
        "currency",
        "direction",
        "vendor_id",
    }
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


def _cell(row: dict[str, str], mapping: dict[str, str], field: str) -> str:
    header = mapping.get(field)
    if not header:
        return ""
    return (row.get(header) or "").strip()


def _signed_amount(
    amount: float,
    direction: str,
    tx_type: str,
    invert_card_charges: bool,
) -> float:
    """Live exports are unsigned; ``Credit or Debit`` is the sign."""
    kind_direction = direction.strip().lower()
    if kind_direction == "debit":
        return -abs(amount)
    if kind_direction == "credit":
        return abs(amount)
    kind = tx_type.strip().lower()
    if any(hint in kind for hint in CREDIT_HINTS) and not any(
        hint in kind for hint in DEBIT_HINTS
    ):
        return abs(amount)
    if invert_card_charges and amount > 0 and kind in (
        "",
        "debit",
        "charge",
        "purchase",
        "withdraw",
        "withdrawal",
        "payment",
    ):
        return -amount
    if invert_card_charges and amount < 0 and any(hint in kind for hint in CREDIT_HINTS):
        return abs(amount)
    return amount


def _payment_ref(partner: str, detail: str, tx_id: str) -> str:
    if partner and detail and partner.casefold() != detail.casefold():
        return f"{partner} — {detail}"
    return partner or detail or tx_id


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
        status = _cell(row, mapping, "status")
        if skip_pending and _is_pending(status):
            continue
        amount_raw = _cell(row, mapping, "amount")
        if not amount_raw:
            continue
        direction = _cell(row, mapping, "direction")
        tx_type = _cell(row, mapping, "tx_type")
        amount = _signed_amount(
            _parse_amount(amount_raw),
            direction,
            tx_type,
            invert_card_charges,
        )
        when = _parse_date(_cell(row, mapping, "date"))
        tx_id = _cell(row, mapping, "transaction_id") or f"jeeves-row-{index}"
        partner = _cell(row, mapping, "merchant")
        invoice_number = _cell(row, mapping, "invoice_number")
        invoice_id = _cell(row, mapping, "invoice_id")
        memo = _cell(row, mapping, "notes")
        payment_description = _cell(row, mapping, "payment_description")
        category = _cell(row, mapping, "category")
        sub_type = _cell(row, mapping, "sub_type")
        vendor_email = _cell(row, mapping, "vendor_email")
        vendor_id = _cell(row, mapping, "vendor_id")
        detail = (
            invoice_number
            or memo
            or payment_description
            or category
            or sub_type
            or tx_type
        )
        narration = " ".join(
            part
            for part in (
                f"jeeves={tx_id}",
                f"type={tx_type}" if tx_type else None,
                f"status={status}" if status else None,
                f"vendor={vendor_id}" if vendor_id else None,
                f"email={vendor_email}" if vendor_email else None,
                f"invoice={invoice_number}" if invoice_number else None,
                f"memo={memo}" if memo else None,
                payment_description or None,
            )
            if part
        )
        line = {
            "date": when,
            "payment_ref": _payment_ref(partner, detail, tx_id),
            "ref": tx_id,
            "unique_import_id": tx_id,
            "amount": amount,
            "partner_name": partner or False,
            "narration": narration or False,
            "currency_code": _cell(row, mapping, "currency") or False,
            "transaction_type": tx_type or direction or False,
            "account_label": _cell(row, mapping, "account") or False,
        }
        if vendor_email:
            line["partner_email"] = vendor_email
        if vendor_id:
            line["jeeves_vendor_id"] = vendor_id
        if invoice_number:
            line["invoice_number"] = invoice_number
        if invoice_id:
            line["jeeves_invoice_id"] = invoice_id
        lines.append(line)
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
        for key in _LINE_EXTRA_KEYS:
            values.pop(key, None)
        transactions.append(values)
    statement = {
        "name": f"{name}/{min(dates).date().isoformat()}",
        "date": max(dates).date(),
        "transactions": transactions,
    }
    return currency, False, [statement]
