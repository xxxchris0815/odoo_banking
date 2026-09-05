"""Match Jeeves bill-pay invoices to cash transactions and Odoo bills."""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from typing import Any

from .jeeves_mcp import unwrap_mcp_json_value

LIST_BILLPAY_INVOICES_TOOL = "list_billpay_invoices"
AMOUNT_TOLERANCE = 0.02
DATE_WINDOW_DAYS = 3
MATCHABLE_INVOICE_STATUSES = frozenset(
    {
        "completed",
        "scheduled",
        "initiated",
        "payment-in-progress",
        "ready-to-initiate",
        "in-review",
        "ready-to-schedule",
        "pending",
    }
)
BULK_CSV_HEADERS = (
    "Vendor name",
    "Account number",
    "Vendor currency (mandatory)",
    "Amount (mandatory)",
    "Memo (mandatory)",
    "Invoice ID (optional)",
    "Invoice Date (optional)",
    "Invoice Due Date (optional)",
)


def unwrap_mcp_invoices(payload: Any) -> tuple[list[dict[str, Any]], int]:
    parsed = unwrap_mcp_json_value(payload)
    if isinstance(parsed, list):
        rows = [row for row in parsed if isinstance(row, dict)]
        return rows, len(rows)
    if isinstance(parsed, dict):
        data = parsed.get("data") or parsed.get("invoices")
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
            total = parsed.get("totalCount")
            if total is None:
                total = parsed.get("count")
            return rows, int(total if total is not None else len(rows))
    return [], 0


def invoice_amount(invoice: dict[str, Any]) -> float:
    if invoice.get("transactionAmount") not in (None, ""):
        return abs(float(invoice["transactionAmount"]))
    total = invoice.get("total")
    if isinstance(total, dict) and total.get("amount") not in (None, ""):
        return abs(float(total["amount"]))
    if invoice.get("baseCurrencyAmount") not in (None, ""):
        return abs(float(invoice["baseCurrencyAmount"]))
    return 0.0


def invoice_currency(invoice: dict[str, Any]) -> str:
    total = invoice.get("total") if isinstance(invoice.get("total"), dict) else {}
    return (
        (total.get("currencyAlphaCode") if isinstance(total, dict) else None)
        or invoice.get("currencyAlphaCode")
        or ""
    ).strip().upper()


def invoice_when(invoice: dict[str, Any]) -> datetime | None:
    for key in ("scheduledDate", "createdAt", "updatedAt", "dueDate"):
        raw = invoice.get(key)
        if not raw:
            continue
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed
    return None


def invoice_vendor_id(invoice: dict[str, Any]) -> str:
    vendor = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    return str(vendor.get("id") or invoice.get("vendorId") or "").strip()


def invoice_vendor_name(invoice: dict[str, Any]) -> str:
    vendor = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    return str(
        vendor.get("vendorName")
        or vendor.get("companyName")
        or " ".join(
            part
            for part in (vendor.get("firstName"), vendor.get("lastName"))
            if part
        )
        or ""
    ).strip()


def invoice_vendor_email(invoice: dict[str, Any]) -> str:
    vendor = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    return str(vendor.get("emailAddress") or "").strip()


def invoice_number(invoice: dict[str, Any]) -> str:
    return str(invoice.get("invoiceNumber") or invoice.get("referenceNumber") or "").strip()


def _amounts_match(left: float, right: float) -> bool:
    return abs(abs(left) - abs(right)) <= AMOUNT_TOLERANCE


def _norm_name(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def _as_naive(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return datetime.combine(value, datetime.min.time())


def match_transaction_to_invoice(
    transaction: dict[str, Any],
    invoices: list[dict[str, Any]],
    *,
    line: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pick the unique bill-pay invoice for a cash debit (or a CSV line)."""
    amount = 0.0
    if transaction.get("totalBaseCurrencyAmount") is not None:
        amount = abs(float(transaction.get("totalBaseCurrencyAmount") or 0))
    elif line and line.get("amount") is not None:
        amount = abs(float(line.get("amount") or 0))
    if amount <= 0:
        return None
    direction = (transaction.get("transactionType") or "").strip().lower()
    if line and line.get("amount") is not None and float(line["amount"]) > 0:
        return None
    if direction == "credit":
        return None
    dest = transaction.get("destination") if isinstance(transaction.get("destination"), dict) else {}
    name = _norm_name(
        (line or {}).get("partner_name")
        or dest.get("name")
        or transaction.get("payee")
    )
    vendor_id = str((line or {}).get("jeeves_vendor_id") or "").strip()
    when = None
    if line and isinstance(line.get("date"), (datetime, date)):
        when = _as_naive(line.get("date"))
    else:
        for key in ("transactionPostedDate", "transactionDate", "createdAt"):
            raw = transaction.get(key)
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            when = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
            break
    currency = (
        (line or {}).get("currency_code")
        or dest.get("currencyAlphaCode")
        or ""
    )
    currency = str(currency or "").strip().upper()

    candidates: list[dict[str, Any]] = []
    for invoice in invoices:
        status = (invoice.get("status") or "").strip().lower()
        if status and status not in MATCHABLE_INVOICE_STATUSES:
            continue
        if not _amounts_match(amount, invoice_amount(invoice)):
            continue
        inv_ccy = invoice_currency(invoice)
        if currency and inv_ccy and currency != inv_ccy:
            continue
        candidates.append(invoice)
    if not candidates:
        return None

    def _score(invoice: dict[str, Any]) -> tuple[int, int]:
        score = 0
        inv_vendor = invoice_vendor_id(invoice)
        if vendor_id and inv_vendor and vendor_id == inv_vendor:
            score += 8
        if name and _norm_name(invoice_vendor_name(invoice)) == name:
            score += 4
        inv_when = invoice_when(invoice)
        days = 99
        if when and inv_when:
            days = abs((when.date() - inv_when.date()).days)
            if days <= DATE_WINDOW_DAYS:
                score += 2
        return score, days

    ranked = sorted(candidates, key=_score, reverse=True)
    best = ranked[0]
    best_score, _days = _score(best)
    if best_score < 4:
        if len(candidates) == 1 and _score(best)[1] <= DATE_WINDOW_DAYS:
            return best
        return None
    ties = [row for row in ranked if _score(row) == _score(best)]
    if len(ties) > 1:
        return None
    return best


def enrich_statement_line_with_invoice(
    line: dict[str, Any],
    transaction: dict[str, Any] | None,
    invoices: list[dict[str, Any]],
) -> dict[str, Any]:
    if line.get("invoice_number") and line.get("jeeves_invoice_id"):
        return line
    invoice = match_transaction_to_invoice(transaction or {}, invoices, line=line)
    if not invoice:
        return line
    number = invoice_number(invoice)
    jeeves_id = str(invoice.get("invoiceId") or "").strip()
    if number:
        line["invoice_number"] = number
        partner = line.get("partner_name") or invoice_vendor_name(invoice)
        if partner:
            line["payment_ref"] = f"{partner} — {number}"
        elif line.get("payment_ref"):
            line["payment_ref"] = f"{line['payment_ref']} — {number}"
        else:
            line["payment_ref"] = number
    if jeeves_id:
        line["jeeves_invoice_id"] = jeeves_id
    ref = str(invoice.get("paymentReferenceNumber") or "").strip()
    if ref:
        line["jeeves_payment_reference"] = ref
    status = str(invoice.get("status") or "").strip()
    if status:
        line["jeeves_invoice_status"] = status
    vendor_id = invoice_vendor_id(invoice)
    if vendor_id and not line.get("jeeves_vendor_id"):
        line["jeeves_vendor_id"] = vendor_id
    email = invoice_vendor_email(invoice)
    if email and not line.get("partner_email"):
        line["partner_email"] = email
    name = invoice_vendor_name(invoice)
    if name and not line.get("partner_name"):
        line["partner_name"] = name
    narration = str(line.get("narration") or "")
    extra = " ".join(
        part
        for part in (
            f"invoice={number}" if number else None,
            f"jeeves_invoice={jeeves_id}" if jeeves_id else None,
            f"jpp={ref}" if ref else None,
        )
        if part
    )
    if extra and extra not in narration:
        line["narration"] = f"{narration} {extra}".strip() or False
    return line


def detect_jeeves_bulk_payments_csv(raw: bytes | str) -> bool:
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else (raw or "")
    header = text.splitlines()[0].lower() if text.strip() else ""
    return (
        "vendor name" in header
        and "vendor currency" in header
        and "invoice id" in header
        and "account number" in header
    )


def format_bulk_account_number(value: str | None) -> str:
    cleaned = re.sub(r"\s+", "", (value or "").strip()).lstrip("'")
    if not cleaned:
        return ""
    return f"'{cleaned}"


def format_bulk_date(value: datetime | date | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    return value.strftime("%d/%m/%Y")


def build_bulk_payments_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=list(BULK_CSV_HEADERS),
        extrasaction="ignore",
        lineterminator="\n",
        quoting=csv.QUOTE_ALL,
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                "Vendor name": row.get("vendor_name") or "",
                "Account number": format_bulk_account_number(row.get("account_number")),
                "Vendor currency (mandatory)": (row.get("currency") or "").upper(),
                "Amount (mandatory)": f"{abs(float(row.get('amount') or 0)):.2f}",
                "Memo (mandatory)": row.get("memo") or "Bulk payment",
                "Invoice ID (optional)": row.get("invoice_id") or "",
                "Invoice Date (optional)": row.get("invoice_date") or "",
                "Invoice Due Date (optional)": row.get("invoice_due_date") or "",
            }
        )
    return buffer.getvalue()
