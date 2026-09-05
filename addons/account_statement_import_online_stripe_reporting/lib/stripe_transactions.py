"""Stripe Balance Transactions → statement lines.

Live payloads put the customer on the expanded ``source`` (charge):
``billing_details.name``, ``customer.email`` / ``customer.description``,
or Wix metadata ``Customer Email``. Fees are a separate Stripe line.
Payouts have no counterparty.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlparse

_logger = logging.getLogger(__name__)

STRIPE_API_BASE = "https://api.stripe.com"
WEBHOOK_PATH_PREFIX = "/stripe/webhook"
WEBHOOK_LOOKBACK_DAYS = 3
PAGE_LIMIT = 100
ZERO_DECIMAL = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "XAF",
        "XOF",
        "XPF",
    }
)
PAYOUT_TYPES = frozenset({"payout", "payout_failure", "payout_cancel"})
REFUND_TYPES = frozenset({"refund", "payment_refund"})
FEE_TYPES = frozenset({"stripe_fee", "application_fee"})
CHARGE_TYPES = frozenset({"charge", "payment"})


class StripeConfigError(ValueError):
    """API key or webhook secret is missing."""


class StripeHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: str, url: str = ""):
        self.status_code = status_code
        self.body = body
        self.url = url
        suffix = f" [{url}]" if url else ""
        super().__init__(f"Stripe API error {status_code}: {body}{suffix}")


def new_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def public_https_base(base_url: str | None) -> str:
    raw = (base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.hostname or ""
    if not host:
        return ""
    port = parsed.port
    if port and port not in (80, 443, 8069, 8071, 8072):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    return f"https://{netloc}"


def webhook_url(base_url: str, token: str | None) -> str:
    if not token:
        return ""
    base = public_https_base(base_url) or str(base_url or "").rstrip("/")
    if base.startswith("http://"):
        base = "https://" + base[len("http://") :]
    return f"{base.rstrip('/')}{WEBHOOK_PATH_PREFIX}/{token}"


def verify_webhook_signature(
    secret: str,
    raw_body: bytes,
    header_value: str | None,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> bool:
    """Stripe-Signature: t=timestamp,v1=hex hmac of ``timestamp.payload``."""
    if not secret or not header_value or not raw_body:
        return False
    items = {}
    for part in header_value.split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        items.setdefault(key.strip(), []).append(value.strip())
    timestamp = (items.get("t") or [""])[0]
    signatures = items.get("v1") or []
    if not timestamp or not signatures:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current = now if now is not None else int(datetime.now(timezone.utc).timestamp())
    if tolerance_seconds and abs(current - ts) > tolerance_seconds:
        return False
    signed = f"{timestamp}.".encode() + raw_body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def _naive_dt(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    return None


def _from_unix(value: int | float | str | None) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)


def minor_to_major(amount: int | str | float, currency: str) -> float:
    raw = float(amount)
    decimals = 0 if (currency or "").upper() in ZERO_DECIMAL else 2
    return raw / (10**decimals)


def transaction_id(transaction: dict[str, Any]) -> str:
    return str(transaction.get("id") or "")


def transaction_unique_id(tx_id: str) -> str:
    return f"st:txn:{tx_id}"


def fee_unique_id(tx_id: str) -> str:
    return f"st:txn:{tx_id}:fee"


def source_object(transaction: dict[str, Any]) -> dict[str, Any]:
    source = transaction.get("source")
    return source if isinstance(source, dict) else {}


def customer_object(source: dict[str, Any]) -> dict[str, Any]:
    customer = source.get("customer")
    return customer if isinstance(customer, dict) else {}


def metadata(source: dict[str, Any]) -> dict[str, Any]:
    meta = source.get("metadata")
    return meta if isinstance(meta, dict) else {}


def format_partner_name(transaction: dict[str, Any]) -> str | None:
    if (transaction.get("type") or "") in PAYOUT_TYPES:
        return None
    source = source_object(transaction)
    billing = source.get("billing_details") or {}
    customer = customer_object(source)
    for candidate in (
        billing.get("name"),
        customer.get("name"),
        customer.get("description"),
        source.get("name"),
    ):
        text = (candidate or "").strip()
        if text:
            return text
    return partner_email(transaction)


def partner_email(transaction: dict[str, Any]) -> str | None:
    source = source_object(transaction)
    billing = source.get("billing_details") or {}
    customer = customer_object(source)
    meta = metadata(source)
    for candidate in (
        billing.get("email"),
        customer.get("email"),
        source.get("receipt_email"),
        meta.get("Customer Email"),
        meta.get("email"),
    ):
        text = (candidate or "").strip()
        if text:
            return text
    return None


def payment_method_label(source: dict[str, Any]) -> str | None:
    details = source.get("payment_method_details") or {}
    kind = (details.get("type") or "").strip()
    if not kind:
        return None
    return kind.replace("_", " ").title()


def detail_label(transaction: dict[str, Any]) -> str:
    tx_type = transaction.get("type") or ""
    source = source_object(transaction)
    description = (transaction.get("description") or source.get("description") or "").strip()
    if tx_type in PAYOUT_TYPES:
        payout_id = source.get("id") or ""
        return f"Payout — {payout_id}" if payout_id else (description or "Payout")
    if tx_type in REFUND_TYPES:
        if description.upper().startswith("REFUND FOR CHARGE") and "(" in description:
            inner = description[description.find("(") + 1 : description.rfind(")")]
            return f"Refund — {inner}" if inner else "Refund"
        return f"Refund — {description}" if description else "Refund"
    method = payment_method_label(source)
    if description and method and method.casefold() not in description.casefold():
        return description
    return description or method or tx_type.replace("_", " ").title() or "Stripe"


def status_label(transaction: dict[str, Any]) -> str:
    status = (transaction.get("status") or "").lower()
    if status == "available":
        return "paid"
    return status or "unknown"


def _payment_ref(status: str, partner: str | None, detail: str) -> str:
    if partner and detail and partner not in detail:
        return f"[{status}] {partner} — {detail}"
    if partner:
        return f"[{status}] {partner}"
    return f"[{status}] {detail}"


def transaction_in_window(
    transaction: dict[str, Any],
    date_since: datetime | date,
    date_until: datetime | date,
) -> bool:
    when = _from_unix(transaction.get("created"))
    since = _naive_dt(date_since)
    until = _naive_dt(date_until)
    if when is None or since is None or until is None:
        return True
    return since <= when < until


def statement_line_from_transaction(
    transaction: dict[str, Any],
) -> dict[str, Any]:
    tx_id = transaction_id(transaction)
    if not tx_id:
        raise ValueError("Stripe balance transaction is missing id")
    when = _from_unix(transaction.get("created"))
    if when is None:
        raise ValueError(f"Stripe transaction {tx_id} has no created timestamp")
    currency = (transaction.get("currency") or "").upper()
    amount = minor_to_major(transaction.get("amount") or 0, currency)
    partner = format_partner_name(transaction)
    email = partner_email(transaction)
    tx_type = transaction.get("type") or ""
    detail = detail_label(transaction)
    status = status_label(transaction)
    source = source_object(transaction)
    narration = " ".join(
        part
        for part in (
            f"stripe={tx_id}",
            f"type={tx_type}" if tx_type else None,
            f"status={transaction.get('status')}" if transaction.get("status") else None,
            f"email={email}" if email else None,
            f"source={source.get('id')}" if source.get("id") else None,
            transaction.get("description"),
        )
        if part
    )
    line = {
        "date": when,
        "payment_ref": _payment_ref(status, partner, detail),
        "ref": tx_id,
        "unique_import_id": transaction_unique_id(tx_id),
        "amount": amount,
        "partner_name": partner or False,
        "account_number": email or False,
        "narration": narration or False,
        "transaction_type": tx_type or status,
    }
    if currency:
        line["currency_code"] = currency
    if email:
        line["partner_email"] = email
    return line


def fee_line_from_transaction(transaction: dict[str, Any]) -> dict[str, Any] | None:
    if (transaction.get("type") or "") in FEE_TYPES:
        return None
    currency = (transaction.get("currency") or "").upper()
    fee = minor_to_major(transaction.get("fee") or 0, currency)
    if not fee:
        return None
    tx_id = transaction_id(transaction)
    if not tx_id:
        return None
    main = statement_line_from_transaction(transaction)
    return {
        "date": main["date"],
        "payment_ref": f"[fee] Stripe — {tx_id}",
        "ref": tx_id,
        "unique_import_id": fee_unique_id(tx_id),
        "amount": -abs(fee),
        "partner_name": "Stripe",
        "account_number": False,
        "narration": f"fee for {main['payment_ref']}",
        "transaction_type": "fee",
        **({"currency_code": currency} if currency else {}),
    }


def statement_lines_from_transaction(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    lines = [statement_line_from_transaction(transaction)]
    fee = fee_line_from_transaction(transaction)
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
        tx_currency = (transaction.get("currency") or "").upper()
        if currency and tx_currency and tx_currency != currency.upper():
            continue
        wanted.extend(statement_lines_from_transaction(transaction))
    wanted.sort(key=lambda line: (line["date"], line["unique_import_id"]))
    return wanted


def _error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return error.get("message") or error.get("code") or str(error)
        if payload.get("message"):
            return str(payload["message"])
        return str(payload)
    return str(payload)


class StripeClient:
    """Balance Transactions client.

    ``http_request(method, url, headers, data=None) -> (status_code, json_or_text)``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        api_base: str | None = None,
        http_request: Callable[..., tuple[int, Any]] | None = None,
        page_limit: int = PAGE_LIMIT,
    ):
        if not api_key:
            raise StripeConfigError("Stripe API key is required")
        self.api_key = api_key
        self.api_base = (api_base or STRIPE_API_BASE).rstrip("/")
        self.page_limit = page_limit
        self._http_request = http_request

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if self._http_request is None:
            raise StripeConfigError("No HTTP backend configured")
        url = f"{self.api_base}{path}"
        if params:
            filtered = {
                key: value
                for key, value in params.items()
                if value not in (None, "")
            }
            if filtered:
                url = f"{url}?{urlencode(filtered, doseq=True)}"
        status, payload = self._http_request(method, url, self._headers(), None)
        if status >= 400:
            raise StripeHTTPError(status, _error_message(payload), url=url)
        return payload

    def list_transactions(
        self,
        date_since: datetime | date,
        date_until: datetime | date | None = None,
        *,
        currency: str | None = None,
        expand: bool = True,
    ) -> list[dict[str, Any]]:
        since = _naive_dt(date_since)
        until = _naive_dt(date_until) if date_until is not None else None
        if since is None:
            raise StripeConfigError("Stripe pull needs a start date")
        if until is not None and since >= until:
            return []
        collected: list[dict[str, Any]] = []
        starting_after = None
        while True:
            params: dict[str, Any] = {
                "limit": self.page_limit,
                "created[gte]": int(since.replace(tzinfo=timezone.utc).timestamp()),
            }
            if until is not None:
                params["created[lt]"] = int(
                    until.replace(tzinfo=timezone.utc).timestamp()
                )
            if expand:
                params["expand[]"] = ["data.source", "data.source.customer"]
            if starting_after:
                params["starting_after"] = starting_after
            payload = self._request("GET", "/v1/balance_transactions", params)
            rows = list((payload or {}).get("data") or [])
            collected.extend(rows)
            if not (payload or {}).get("has_more") or not rows:
                break
            starting_after = rows[-1].get("id")
            if not starting_after:
                break
        if currency:
            wanted = currency.lower()
            collected = [
                row
                for row in collected
                if (row.get("currency") or "").lower() == wanted
            ]
        return collected

    def current_wallet(self, currency: str | None) -> float:
        """Live Stripe balance for one currency (available + pending).

        ``/v1/balance`` is only “now”. Historical extras subtract later nets.
        """
        payload = self._request("GET", "/v1/balance")
        wanted = (currency or "").lower()
        total = 0.0
        for bucket in ("available", "pending"):
            for row in payload.get(bucket) or []:
                code = (row.get("currency") or "").lower()
                if wanted and code != wanted:
                    continue
                total += minor_to_major(row.get("amount") or 0, code or "EUR")
        return total

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
        extras = {}
        try:
            extras = historical_wallet_extras(
                self.current_wallet(currency),
                transactions,
                self.list_transactions(
                    date_until, None, currency=currency, expand=False
                ),
                currency,
            )
        except StripeHTTPError as error:
            _logger.info("Stripe historical balance skipped: %s", error)
        return lines, extras


def transaction_net(transaction: dict[str, Any], currency: str | None = None) -> float:
    code = (currency or transaction.get("currency") or "EUR").upper()
    return minor_to_major(transaction.get("net") or 0, code)


def historical_wallet_extras(
    current_wallet: float,
    window_transactions: list[dict[str, Any]],
    later_transactions: list[dict[str, Any]],
    currency: str | None,
) -> dict[str, Any]:
    """Wallet at the window end — same extras keys as PayPal.

    PayPal puts ``available_balance`` on each transaction. Stripe does not.
    GoCardless is clearing, not a wallet, so it sends no extras.

    Reconstruct: wallet_then = wallet_now − sum(net of later BTs).
    """
    later_net = sum(transaction_net(tx, currency) for tx in later_transactions)
    window_net = sum(transaction_net(tx, currency) for tx in window_transactions)
    end = float(current_wallet) - later_net
    return {
        "balance_start": end - window_net,
        "balance_end_real": end,
    }
