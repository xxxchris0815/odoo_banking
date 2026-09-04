"""GoCardless Payments (Direct Debit) → clearing-journal lines.

This is not Bank Account Data / Open Banking. It tracks every collection
on a clearing journal, updates the line when the mandate payment fails,
and books payouts out so the journal returns to zero.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode, urljoin

GC_API_BASE = "https://api.gocardless.com"
GC_SANDBOX_API_BASE = "https://api-sandbox.gocardless.com"
GC_API_VERSION = "2015-07-06"
DEFAULT_PAGE_LIMIT = 100
STATUS_LOOKBACK_DAYS = 90

COLLECTED_STATUSES = frozenset({"confirmed", "paid_out"})
PAYOUT_BOOK_STATUSES = frozenset({"pending", "paid"})
REFUND_BOOK_STATUSES = frozenset({"created", "pending", "paid", "refunded"})
ZERO_DECIMAL = frozenset({"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"})


class GoCardlessConfigError(ValueError):
    """Access token or webhook secret is missing."""


class GoCardlessHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"GoCardless API error {status_code}: {body}")


def verify_webhook_signature(secret: str, raw_body: bytes, header_value: str | None) -> bool:
    if not secret or not header_value:
        return False
    digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header_value.strip())


def minor_to_major(amount: int | str | float, currency: str) -> float:
    raw = float(amount)
    decimals = 0 if (currency or "").upper() in ZERO_DECIMAL else 2
    return raw / (10**decimals)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    if len(value) == 10 and value[4] == "-":
        return datetime.strptime(value, "%Y-%m-%d")
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _as_iso(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.replace(microsecond=0).isoformat()
    return datetime.combine(value, datetime.min.time()).isoformat()


def payment_amount(payment: dict[str, Any]) -> float:
    """Economic amount on the clearing journal for the current status."""
    major = abs(minor_to_major(payment.get("amount") or 0, payment.get("currency") or "EUR"))
    if (payment.get("status") or "") in COLLECTED_STATUSES:
        return major
    return 0.0


def payment_unique_id(payment_id: str) -> str:
    return f"gc:pay:{payment_id}"


def payout_unique_id(payout_id: str) -> str:
    return f"gc:payout:{payout_id}"


def payout_fee_unique_id(payout_id: str) -> str:
    return f"gc:payout:{payout_id}:fees"


def refund_unique_id(refund_id: str) -> str:
    return f"gc:refund:{refund_id}"


def _label(*parts: Any) -> str:
    return " ".join(str(part) for part in parts if part)


def statement_line_from_payment(
    payment: dict[str, Any],
    *,
    customer_name: str | None = None,
    event_detail: str | None = None,
) -> dict[str, Any]:
    status = payment.get("status") or "unknown"
    pay_id = payment.get("id") or ""
    if not pay_id:
        raise ValueError("GoCardless payment is missing id")
    when = _parse_datetime(payment.get("charge_date")) or _parse_datetime(
        payment.get("created_at")
    )
    if when is None:
        raise ValueError(f"GoCardless payment {pay_id} has no date")
    reference = payment.get("reference") or payment.get("description") or pay_id
    narration = _label(
        f"status={status}",
        payment.get("description"),
        f"mandate={((payment.get('links') or {}).get('mandate'))}",
        event_detail,
    )
    return {
        "date": when,
        "payment_ref": f"[{status}] {reference}",
        "ref": pay_id,
        "unique_import_id": payment_unique_id(pay_id),
        "amount": payment_amount(payment),
        "partner_name": customer_name or False,
        "narration": narration or False,
        "transaction_type": status,
        "currency_code": payment.get("currency") or False,
    }


def statement_lines_from_payout(payout: dict[str, Any]) -> list[dict[str, Any]]:
    payout_id = payout.get("id") or ""
    if not payout_id:
        raise ValueError("GoCardless payout is missing id")
    status = payout.get("status") or "unknown"
    currency = payout.get("currency") or "EUR"
    when = _parse_datetime(payout.get("arrival_date")) or _parse_datetime(
        payout.get("created_at")
    )
    if when is None:
        raise ValueError(f"GoCardless payout {payout_id} has no date")
    book = status in PAYOUT_BOOK_STATUSES
    amount = abs(minor_to_major(payout.get("amount") or 0, currency))
    lines = [
        {
            "date": when,
            "payment_ref": f"[payout {status}] {payout_id}",
            "ref": payout_id,
            "unique_import_id": payout_unique_id(payout_id),
            "amount": -amount if book else 0.0,
            "partner_name": False,
            "narration": _label(
                f"status={status}",
                f"reference={payout.get('reference')}",
                "clearing to bank",
            ),
            "transaction_type": f"payout_{status}",
            "currency_code": currency,
        }
    ]
    fees = abs(minor_to_major(payout.get("deducted_fees") or 0, currency))
    if fees:
        lines.append(
            {
                "date": when,
                "payment_ref": f"[gocardless fees] {payout_id}",
                "ref": f"{payout_id}-fees",
                "unique_import_id": payout_fee_unique_id(payout_id),
                "amount": -fees if book else 0.0,
                "partner_name": False,
                "narration": "GoCardless deducted fees on payout",
                "transaction_type": "gocardless_fee",
                "currency_code": currency,
            }
        )
    return lines


def statement_line_from_refund(refund: dict[str, Any]) -> dict[str, Any]:
    refund_id = refund.get("id") or ""
    if not refund_id:
        raise ValueError("GoCardless refund is missing id")
    status = refund.get("status") or "unknown"
    currency = refund.get("currency") or "EUR"
    when = _parse_datetime(refund.get("created_at"))
    if when is None:
        raise ValueError(f"GoCardless refund {refund_id} has no date")
    major = abs(minor_to_major(refund.get("amount") or 0, currency))
    amount = -major if status in REFUND_BOOK_STATUSES else 0.0
    return {
        "date": when,
        "payment_ref": f"[refund {status}] {refund.get('reference') or refund_id}",
        "ref": refund_id,
        "unique_import_id": refund_unique_id(refund_id),
        "amount": amount,
        "partner_name": False,
        "narration": refund.get("reason") or False,
        "transaction_type": f"refund_{status}",
        "currency_code": currency,
    }


def clearing_balance(lines: Iterable[dict[str, Any]]) -> float:
    return round(sum(float(line.get("amount") or 0) for line in lines), 2)


class GoCardlessPaymentsClient:
    def __init__(
        self,
        access_token: str,
        *,
        api_base: str | None = None,
        http_get: Callable[[str, dict[str, str]], tuple[int, Any]] | None = None,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        status_lookback_days: int = STATUS_LOOKBACK_DAYS,
    ):
        if not access_token:
            raise GoCardlessConfigError("GoCardless access token is required")
        self.access_token = access_token
        self.api_base = (api_base or GC_API_BASE).rstrip("/") + "/"
        self.page_limit = page_limit
        self.status_lookback_days = status_lookback_days
        self._http_get = http_get
        self._customers: dict[str, dict[str, Any]] = {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "GoCardless-Version": GC_API_VERSION,
            "Accept": "application/json",
        }

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = urljoin(self.api_base, path.lstrip("/"))
        if params:
            filtered = {key: value for key, value in params.items() if value not in (None, "")}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        return url

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._http_get is None:
            raise GoCardlessConfigError("No HTTP backend configured")
        status, payload = self._http_get(self._url(path, params), self._headers())
        if status >= 400:
            body = payload if isinstance(payload, str) else str(payload)
            raise GoCardlessHTTPError(status, body)
        if not isinstance(payload, dict):
            raise GoCardlessHTTPError(status, f"Unexpected payload: {payload!r}")
        return payload

    def _paginate(self, path: str, key: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        after = None
        base_params = dict(params or {})
        base_params.setdefault("limit", self.page_limit)
        while True:
            query = dict(base_params)
            if after:
                query["after"] = after
            payload = self._get(path, query)
            rows = payload.get(key) or []
            collected.extend(rows)
            cursors = (payload.get("meta") or {}).get("cursors") or {}
            after = cursors.get("after")
            if not after or not rows:
                break
        return collected

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        return (self._get(f"payments/{payment_id}") or {}).get("payments") or {}

    def get_payout(self, payout_id: str) -> dict[str, Any]:
        return (self._get(f"payouts/{payout_id}") or {}).get("payouts") or {}

    def get_customer(self, customer_id: str) -> dict[str, Any]:
        if customer_id in self._customers:
            return self._customers[customer_id]
        customer = (self._get(f"customers/{customer_id}") or {}).get("customers") or {}
        self._customers[customer_id] = customer
        return customer

    def _customer_name(self, resource: dict[str, Any]) -> str | None:
        customer_id = ((resource.get("links") or {}).get("customer")) or None
        if not customer_id:
            return None
        customer = self.get_customer(customer_id)
        name = " ".join(
            part
            for part in (customer.get("given_name"), customer.get("family_name"))
            if part
        ).strip()
        return name or customer.get("company_name") or None

    def iter_payments(self, date_since: datetime | date, date_until: datetime | date) -> list[dict[str, Any]]:
        lookback_start = (
            date_since - timedelta(days=self.status_lookback_days)
            if isinstance(date_since, datetime)
            else datetime.combine(date_since, datetime.min.time())
            - timedelta(days=self.status_lookback_days)
        )
        return self._paginate(
            "payments",
            "payments",
            {
                "created_at[gte]": _as_iso(lookback_start),
                "created_at[lte]": _as_iso(date_until),
            },
        )

    def iter_payouts(self, date_since: datetime | date, date_until: datetime | date) -> list[dict[str, Any]]:
        return self._paginate(
            "payouts",
            "payouts",
            {
                "created_at[gte]": _as_iso(date_since),
                "created_at[lte]": _as_iso(date_until),
            },
        )

    def iter_refunds(self, date_since: datetime | date, date_until: datetime | date) -> list[dict[str, Any]]:
        return self._paginate(
            "refunds",
            "refunds",
            {
                "created_at[gte]": _as_iso(date_since),
                "created_at[lte]": _as_iso(date_until),
            },
        )

    def obtain_statement_lines(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        for payment in self.iter_payments(date_since, date_until):
            lines.append(
                statement_line_from_payment(
                    payment, customer_name=self._customer_name(payment)
                )
            )
        for payout in self.iter_payouts(date_since, date_until):
            lines.extend(statement_lines_from_payout(payout))
        for refund in self.iter_refunds(date_since, date_until):
            lines.append(statement_line_from_refund(refund))
        return lines

    def lines_for_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        resource_type = event.get("resource_type")
        links = event.get("links") or {}
        details = event.get("details") or {}
        detail_text = _label(details.get("cause"), details.get("description"), details.get("reason_code"))
        if resource_type == "payments" and links.get("payment"):
            payment = self.get_payment(links["payment"])
            if not payment:
                return []
            return [
                statement_line_from_payment(
                    payment,
                    customer_name=self._customer_name(payment),
                    event_detail=detail_text,
                )
            ]
        if resource_type == "payouts" and links.get("payout"):
            payout = self.get_payout(links["payout"])
            return statement_lines_from_payout(payout) if payout else []
        if resource_type == "refunds" and links.get("refund"):
            refund = (self._get(f"refunds/{links['refund']}") or {}).get("refunds") or {}
            return [statement_line_from_refund(refund)] if refund else []
        return []
