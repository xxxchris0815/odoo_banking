"""PayPal Transaction Search → statement lines.

Live Reporting payloads put the counterparty in ``payer_name.alternate_full_name``
or ``given_name`` + ``surname``. ``full_name`` is usually absent. Shipping name
is the account holder on outgoing payments and must not become the partner.
"""

from __future__ import annotations

import json
import logging
import secrets
from base64 import b64encode
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable
from urllib.parse import urlencode

_logger = logging.getLogger(__name__)

PAYPAL_API_BASE = "https://api.paypal.com"
PAYPAL_SANDBOX_API_BASE = "https://api.sandbox.paypal.com"
TRANSACTIONS_SCOPE = "https://uri.paypal.com/services/reporting/search/read"
NO_DATA_FOR_DATE_AVAIL_MSG = "Data for the given start date is not available."
MAX_HISTORY = timedelta(days=365 * 3)
CHUNK_DAYS = 31
PAGE_SIZE = 500
WEBHOOK_PATH_PREFIX = "/paypal/webhook"
WEBHOOK_LOOKBACK_DAYS = 3
DEFAULT_WEBHOOK_EVENTS = (
    "PAYMENT.SALE.COMPLETED",
    "PAYMENT.SALE.REFUNDED",
    "PAYMENT.SALE.REVERSED",
    "PAYMENT.CAPTURE.COMPLETED",
    "PAYMENT.CAPTURE.DENIED",
    "PAYMENT.CAPTURE.REFUNDED",
    "PAYMENT.CAPTURE.REVERSED",
    "CUSTOMER.DISPUTE.CREATED",
)

STATUS_LABELS = {
    "S": "paid",
    "P": "pending",
    "D": "denied",
    "V": "reversed",
    "F": "refunded",
}

WITHDRAWAL_EVENTS = frozenset(
    {"T0400", "T0401", "T0402", "T0403", "T1700", "T1701"}
)
FUNDING_EVENTS = frozenset(
    {"T0300", "T0301", "T0302", "T0303", "T0700", "T0701"}
)
REFUND_EVENTS = frozenset(
    {"T1106", "T1107", "T1114", "T1115", "T1201", "T1202"}
)
HOLD_EVENTS = frozenset({"T1500", "T1501", "T1502", "T1503", "T2101", "T2103", "T2105", "T2107"})
HOLD_RELEASE_EVENTS = frozenset({"T1105", "T2102", "T2104", "T2106", "T2108"})

EVENT_LABELS = {
    "T0000": "PayPal payment",
    "T0002": "Subscription",
    "T0003": "Subscription",
    "T0005": "Direct payment",
    "T0006": "Checkout",
    "T0007": "Website payment",
    "T0011": "Mobile payment",
    "T0013": "Donation",
    "T0100": "PayPal fee",
    "T0106": "Chargeback fee",
    "T0107": "Payment fee",
    "T0200": "Currency conversion",
    "T0300": "Account funding",
    "T0400": "Withdrawal",
    "T0403": "Withdrawal",
    "T0700": "Card funding",
    "T1105": "Hold release",
    "T1106": "Refund",
    "T1107": "Refund",
    "T1108": "Fee reversal",
    "T1109": "Fee refund",
    "T1201": "Chargeback",
    "T1501": "Hold",
    "T2107": "Payment hold",
    "T2108": "Payment hold release",
}


class PayPalConfigError(ValueError):
    """Client ID, Secret, or Transaction Search scope is missing."""


class PayPalHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str, url: str = ""):
        self.status_code = status_code
        self.body = body
        self.url = url
        suffix = f" [{url}]" if url else ""
        super().__init__(f"PayPal API error {status_code}: {body}{suffix}")


def _naive_dt(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return None


def _parse_datetime(value: str | datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def webhook_url(base_url: str, token: str | None) -> str:
    if not token:
        return ""
    return f"{str(base_url or '').rstrip('/')}{WEBHOOK_PATH_PREFIX}/{token}"


def webhook_signature_fields(headers: dict[str, Any] | None) -> dict[str, str]:
    """Read PayPal transmission headers, regardless of case."""
    normalized = {
        str(key).lower(): "" if value is None else str(value)
        for key, value in (headers or {}).items()
    }
    return {
        "auth_algo": normalized.get("paypal-auth-algo", ""),
        "cert_url": normalized.get("paypal-cert-url", ""),
        "transmission_id": normalized.get("paypal-transmission-id", ""),
        "transmission_sig": normalized.get("paypal-transmission-sig", ""),
        "transmission_time": normalized.get("paypal-transmission-time", ""),
    }


def webhook_verified(payload: dict[str, Any] | None) -> bool:
    return (payload or {}).get("verification_status") == "SUCCESS"


def as_rfc3339(value: datetime | date | None) -> str | None:
    """PayPal rejects microseconds; it wants seconds and a Z suffix."""
    dt = _naive_dt(value)
    if dt is None:
        return None
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(value: dict[str, Any] | None) -> Decimal:
    if not value:
        return Decimal("0")
    raw = value.get("value")
    if raw in (None, ""):
        return Decimal("0")
    return Decimal(str(raw))


def _currency(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    code = value.get("currency_code") or value.get("currency")
    return str(code) if code else None


def transaction_info(transaction: dict[str, Any]) -> dict[str, Any]:
    return transaction.get("transaction_info") or {}


def payer_info(transaction: dict[str, Any]) -> dict[str, Any]:
    return transaction.get("payer_info") or {}


def transaction_id(transaction: dict[str, Any]) -> str:
    return str(transaction_info(transaction).get("transaction_id") or "")


def transaction_unique_id(tx_id: str) -> str:
    return f"pp:tx:{tx_id}"


def fee_unique_id(tx_id: str) -> str:
    return f"pp:tx:{tx_id}:fee"


def initiation_date(transaction: dict[str, Any]) -> datetime | None:
    info = transaction_info(transaction)
    return _parse_datetime(info.get("transaction_initiation_date")) or _parse_datetime(
        info.get("transaction_updated_date")
    )


def updated_date(transaction: dict[str, Any]) -> datetime | None:
    info = transaction_info(transaction)
    return _parse_datetime(info.get("transaction_updated_date")) or initiation_date(
        transaction
    )


def line_date(
    transaction: dict[str, Any],
    date_since: datetime | date | None = None,
    date_until: datetime | date | None = None,
) -> datetime:
    started = initiation_date(transaction)
    updated = updated_date(transaction)
    since = _naive_dt(date_since)
    until = _naive_dt(date_until)
    if started and since and until and since <= started < until:
        return started
    if updated and since and until and since <= updated < until:
        return updated
    when = started or updated
    if when is None:
        raise ValueError(
            f"PayPal transaction {transaction_id(transaction) or '?'} has no date"
        )
    return when


def transaction_in_window(
    transaction: dict[str, Any],
    date_since: datetime | date,
    date_until: datetime | date,
) -> bool:
    since = _naive_dt(date_since)
    until = _naive_dt(date_until)
    if since is None or until is None:
        return True
    started = initiation_date(transaction)
    updated = updated_date(transaction)
    return any(
        when is not None and since <= when < until for when in (started, updated)
    )


def format_payer_name(transaction: dict[str, Any]) -> str | None:
    """Live API uses alternate_full_name / given+surname, not full_name."""
    name = payer_info(transaction).get("payer_name") or {}
    full = (name.get("full_name") or "").strip()
    alternate = (name.get("alternate_full_name") or "").strip()
    person = " ".join(
        part for part in (name.get("given_name"), name.get("surname")) if part
    ).strip()
    return full or alternate or person or None


def payer_email(transaction: dict[str, Any]) -> str | None:
    payer = payer_info(transaction)
    name = payer.get("payer_name") or {}
    return (payer.get("email_address") or name.get("email_address") or "").strip() or None


def cart_item_label(transaction: dict[str, Any]) -> str | None:
    items = (transaction.get("cart_info") or {}).get("item_details") or []
    names: list[str] = []
    for item in items:
        label = (item.get("item_name") or item.get("item_description") or "").strip()
        if label and label not in names:
            names.append(label)
    return ", ".join(names) if names else None


def event_label(event_code: str) -> str:
    return EVENT_LABELS.get(event_code) or event_code or "PayPal"


def detail_label(transaction: dict[str, Any]) -> str:
    info = transaction_info(transaction)
    event = info.get("transaction_event_code") or ""
    cart = cart_item_label(transaction)
    subject = (info.get("transaction_subject") or "").strip()
    note = (info.get("transaction_note") or "").strip()
    invoice = (info.get("invoice_id") or "").strip()
    bank_ref = (info.get("bank_reference_id") or "").strip()
    prefix = None
    if event in REFUND_EVENTS:
        prefix = "Refund"
    elif event in WITHDRAWAL_EVENTS:
        prefix = "Withdrawal"
    elif event in FUNDING_EVENTS:
        prefix = "Account funding"
    elif event in HOLD_EVENTS:
        prefix = "Hold"
    elif event in HOLD_RELEASE_EVENTS:
        prefix = "Hold release"
    best = cart or subject or note or invoice or bank_ref
    if prefix and best and best != prefix:
        return f"{prefix} — {best}"
    if prefix:
        return prefix
    if best:
        return best
    return event_label(event)


def status_label(transaction: dict[str, Any]) -> str:
    status = (transaction_info(transaction).get("transaction_status") or "").upper()
    return STATUS_LABELS.get(status, status.lower() or "unknown")


def _payment_ref(status: str, partner: str | None, detail: str) -> str:
    if partner and detail and partner not in detail:
        return f"[{status}] {partner} — {detail}"
    if partner:
        return f"[{status}] {partner}"
    return f"[{status}] {detail}"


def transaction_amount(transaction: dict[str, Any]) -> Decimal:
    return _money(transaction_info(transaction).get("transaction_amount"))


def fee_amount(transaction: dict[str, Any]) -> Decimal:
    return _money(transaction_info(transaction).get("fee_amount"))


def ending_balance(transaction: dict[str, Any]) -> Decimal | None:
    info = transaction_info(transaction)
    raw = info.get("available_balance") or info.get("ending_balance")
    if not raw:
        return None
    return _money(raw)


def statement_line_from_transaction(
    transaction: dict[str, Any],
    *,
    date_since: datetime | date | None = None,
    date_until: datetime | date | None = None,
) -> dict[str, Any]:
    tx_id = transaction_id(transaction)
    if not tx_id:
        raise ValueError("PayPal transaction is missing transaction_id")
    info = transaction_info(transaction)
    event = info.get("transaction_event_code") or ""
    partner = format_payer_name(transaction)
    email = payer_email(transaction)
    detail = detail_label(transaction)
    status = status_label(transaction)
    amount = transaction_amount(transaction)
    currency = _currency(info.get("transaction_amount"))
    narration = " ".join(
        part
        for part in (
            f"paypal={tx_id}",
            f"event={event}" if event else None,
            f"status={info.get('transaction_status')}" if info.get("transaction_status") else None,
            f"email={email}" if email else None,
            f"invoice={info.get('invoice_id')}" if info.get("invoice_id") else None,
            f"bank={info.get('bank_reference_id')}" if info.get("bank_reference_id") else None,
            f"ref={info.get('paypal_reference_id')}" if info.get("paypal_reference_id") else None,
            info.get("transaction_note"),
        )
        if part
    )
    line = {
        "date": line_date(transaction, date_since, date_until),
        "payment_ref": _payment_ref(status, partner, detail),
        "ref": tx_id,
        "unique_import_id": transaction_unique_id(tx_id),
        "amount": float(amount),
        "partner_name": partner or False,
        "account_number": email or False,
        "narration": narration or False,
        "transaction_type": event or status,
    }
    if currency:
        line["currency_code"] = currency
    if email:
        line["partner_email"] = email
    return line


def fee_line_from_transaction(
    transaction: dict[str, Any],
    *,
    date_since: datetime | date | None = None,
    date_until: datetime | date | None = None,
) -> dict[str, Any] | None:
    fee = fee_amount(transaction)
    if fee == 0:
        return None
    tx_id = transaction_id(transaction)
    if not tx_id:
        return None
    main = statement_line_from_transaction(
        transaction, date_since=date_since, date_until=date_until
    )
    return {
        "date": main["date"],
        "payment_ref": f"[fee] PayPal — {tx_id}",
        "ref": tx_id,
        "unique_import_id": fee_unique_id(tx_id),
        "amount": float(fee),
        "partner_name": "PayPal",
        "account_number": False,
        "narration": f"fee for {main['payment_ref']}",
        "transaction_type": "fee",
        **(
            {"currency_code": main["currency_code"]}
            if main.get("currency_code")
            else {}
        ),
    }


def statement_lines_from_transaction(
    transaction: dict[str, Any],
    *,
    date_since: datetime | date | None = None,
    date_until: datetime | date | None = None,
) -> list[dict[str, Any]]:
    lines = [
        statement_line_from_transaction(
            transaction, date_since=date_since, date_until=date_until
        )
    ]
    fee = fee_line_from_transaction(
        transaction, date_since=date_since, date_until=date_until
    )
    if fee:
        lines.append(fee)
    return lines


def statement_lines_from_transactions(
    transactions: list[dict[str, Any]],
    date_since: datetime | date | None = None,
    date_until: datetime | date | None = None,
    *,
    currency: str | None = None,
) -> list[dict[str, Any]]:
    wanted: list[dict[str, Any]] = []
    for transaction in transactions:
        if date_since is not None and date_until is not None:
            if not transaction_in_window(transaction, date_since, date_until):
                continue
        if currency:
            tx_currency = _currency(
                transaction_info(transaction).get("transaction_amount")
            )
            if tx_currency and tx_currency != currency:
                continue
        wanted.extend(
            statement_lines_from_transaction(
                transaction, date_since=date_since, date_until=date_until
            )
        )
    wanted.sort(key=lambda line: (line["date"], line["unique_import_id"]))
    return wanted


def _balance_extras(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    dated = []
    for transaction in transactions:
        when = initiation_date(transaction)
        if when is None:
            continue
        dated.append((when, transaction))
    if not dated:
        return {}
    dated.sort(key=lambda item: item[0])
    first = dated[0][1]
    last = dated[-1][1]
    start = ending_balance(first)
    end = ending_balance(last)
    extras: dict[str, Any] = {}
    if start is not None:
        extras["balance_start"] = float(
            start - transaction_amount(first) - fee_amount(first)
        )
    if end is not None:
        extras["balance_end_real"] = float(end)
    return extras


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        if payload.get("name"):
            return f"{payload['name']}: {payload.get('message') or 'Unknown error'}"
        if payload.get("error"):
            return (
                f"{payload['error']}: "
                f"{payload.get('error_description') or 'Unknown error'}"
            )
        return str(payload)
    return str(payload)


class PayPalClient:
    """Transaction Search client.

    ``http_request`` is injectable:
    ``http_request(method, url, headers, data=None) -> (status_code, json_or_text)``.
    """

    def __init__(
        self,
        client_id: str,
        secret: str,
        *,
        api_base: str | None = None,
        http_request: Callable[..., tuple[int, Any]] | None = None,
        page_size: int = PAGE_SIZE,
    ):
        if not client_id or not secret:
            raise PayPalConfigError("PayPal Client ID and Secret are required")
        self.client_id = client_id
        self.secret = secret
        self.api_base = (api_base or PAYPAL_API_BASE).rstrip("/")
        self.page_size = page_size
        self._http_request = http_request
        self._token: str | None = None

    def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        data: bytes | None = None,
    ) -> Any:
        if self._http_request is None:
            raise PayPalConfigError("No HTTP backend configured")
        status, payload = self._http_request(method, url, headers, data)
        if (
            isinstance(payload, dict)
            and payload.get("name") == "INVALID_REQUEST"
            and payload.get("message") == NO_DATA_FOR_DATE_AVAIL_MSG
        ):
            return {
                "transaction_details": [],
                "page": 1,
                "total_items": 0,
                "total_pages": 0,
            }
        if status >= 400:
            raise PayPalHTTPError(status, _error_message(payload), url=url)
        if isinstance(payload, dict) and (
            payload.get("name") or payload.get("error")
        ):
            raise PayPalHTTPError(status or 400, _error_message(payload), url=url)
        return payload

    def get_token(self) -> str:
        if self._token:
            return self._token
        basic = b64encode(f"{self.client_id}:{self.secret}".encode()).decode("ascii")
        payload = self._request(
            "POST",
            f"{self.api_base}/v1/oauth2/token",
            {
                "Authorization": f"Basic {basic}",
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
        )
        if not isinstance(payload, dict):
            raise PayPalConfigError("Invalid PayPal token response")
        scopes = payload.get("scope") or ""
        if TRANSACTIONS_SCOPE not in scopes:
            raise PayPalConfigError(
                "PayPal app is missing Transaction Search. "
                "Enable it under Apps & Credentials → Features."
            )
        if payload.get("token_type") != "Bearer" or not payload.get("access_token"):
            raise PayPalConfigError("Failed to acquire a PayPal Bearer token")
        self._token = payload["access_token"]
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Accept": "application/json",
        }

    def get_balance(self, currency: str, as_of: datetime | date) -> Decimal:
        stamp = as_rfc3339(as_of)
        url = (
            f"{self.api_base}/v1/reporting/balances?"
            + urlencode({"currency_code": currency, "as_of_time": stamp})
        )
        payload = self._request("GET", url, self._auth_headers())
        balances = (payload or {}).get("balances") or []
        if not balances:
            return Decimal("0")
        available = balances[0].get("available_balance") or {}
        return _money(available)

    def _iter_chunk(
        self,
        date_since: datetime,
        date_until: datetime,
        currency: str | None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            params = {
                "start_date": as_rfc3339(date_since),
                "end_date": as_rfc3339(date_until),
                "fields": "all",
                "balance_affecting_records_only": "Y",
                "page_size": self.page_size,
                "page": page,
            }
            if currency:
                params["transaction_currency"] = currency
            url = f"{self.api_base}/v1/reporting/transactions?{urlencode(params)}"
            payload = self._request("GET", url, self._auth_headers())
            collected.extend(payload.get("transaction_details") or [])
            total_pages = int(payload.get("total_pages") or 1)
            page += 1
        return collected

    def list_transactions(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
        *,
        currency: str | None = None,
    ) -> list[dict[str, Any]]:
        since = _naive_dt(date_since)
        until = _naive_dt(date_until)
        if since is None or until is None:
            raise PayPalConfigError("PayPal pull needs a start and end date")
        if since >= until:
            return []
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if since < now - MAX_HISTORY:
            raise PayPalConfigError(
                "PayPal only returns the last 3 years. "
                "Import older history from a PayPal CSV."
            )
        collected: list[dict[str, Any]] = []
        cursor = since
        step = timedelta(days=CHUNK_DAYS)
        while cursor < until:
            chunk_end = min(cursor + step, until)
            collected.extend(self._iter_chunk(cursor, chunk_end, currency))
            cursor = chunk_end
        by_id: dict[str, dict[str, Any]] = {}
        for transaction in collected:
            tx_id = transaction_id(transaction)
            if tx_id:
                by_id[tx_id] = transaction
        return list(by_id.values())

    def obtain_statement_lines(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
        *,
        currency: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        transactions = self.list_transactions(
            date_since, date_until, currency=currency
        )
        lines = statement_lines_from_transactions(
            transactions, date_since, date_until, currency=currency
        )
        extras = _balance_extras(
            [
                tx
                for tx in transactions
                if transaction_in_window(tx, date_since, date_until)
            ]
        )
        if not extras and currency:
            try:
                balance = float(self.get_balance(currency, date_since))
                extras = {"balance_start": balance, "balance_end_real": balance}
            except PayPalHTTPError as error:
                _logger.info("PayPal balance lookup skipped: %s", error)
        return lines, extras

    def _request_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        headers = self._auth_headers()
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        return self._request(method, f"{self.api_base}{path}", headers, data)

    def verify_webhook(
        self,
        webhook_id: str,
        headers: dict[str, Any],
        event: dict[str, Any],
    ) -> bool:
        if not webhook_id:
            raise PayPalConfigError("PayPal Webhook ID is missing on the provider")
        fields = webhook_signature_fields(headers)
        if not all(fields.values()):
            return False
        payload = self._request_json(
            "POST",
            "/v1/notifications/verify-webhook-signature",
            {
                **fields,
                "webhook_id": webhook_id,
                "webhook_event": event,
            },
        )
        return webhook_verified(payload if isinstance(payload, dict) else {})

    def list_webhooks(self) -> list[dict[str, Any]]:
        payload = self._request_json("GET", "/v1/notifications/webhooks")
        if isinstance(payload, dict):
            return list(payload.get("webhooks") or [])
        return []

    def create_webhook(self, url: str, event_names: tuple[str, ...] | None = None) -> dict[str, Any]:
        payload = self._request_json(
            "POST",
            "/v1/notifications/webhooks",
            {
                "url": url,
                "event_types": [
                    {"name": name} for name in (event_names or DEFAULT_WEBHOOK_EVENTS)
                ],
            },
        )
        if not isinstance(payload, dict) or not payload.get("id"):
            raise PayPalHTTPError(200, "PayPal webhook create returned no id")
        return payload

    def ensure_webhook(self, url: str) -> str:
        """Reuse a webhook already registered for this exact URL."""
        for hook in self.list_webhooks():
            if hook.get("url") == url and hook.get("id"):
                return str(hook["id"])
        return str(self.create_webhook(url)["id"])
