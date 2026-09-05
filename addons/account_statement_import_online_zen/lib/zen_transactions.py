"""ZEN.COM Transfers API helpers (Odoo-independent).

Maps the public Transfers API (accounts + payment history) onto the
line dicts expected by OCA ``online.bank.statement.provider``.
"""

from __future__ import annotations

import os
import secrets
import ssl
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode, urljoin, urlparse

ZEN_DEFAULT_API_BASE = "https://api-services.zen.com"
ZEN_TEST_API_BASE = "https://api-services.zen-test.com"
ACCOUNTS_PATH = "accounts/v1.0"
HISTORY_PATH = "payments/v1.0"
PAYMENT_PATH = "payments/v1.0"
WEBHOOK_PATH_PREFIX = "/zen/webhook"
SETTLED_STATUS = "SETTLED"
DEFAULT_PAGE_LIMIT = 100


def _normalize_api_key(api_key: str) -> str:
    """n8n/ZEN use ``Authorization: Bearer <apiKey>``. Strip a pasted prefix."""
    key = (api_key or "").strip()
    if key.lower().startswith("bearer "):
        return key[7:].strip()
    return key


class ZenConfigError(ValueError):
    """Provider is missing credentials or account identification."""


class ZenHTTPError(RuntimeError):
    """ZEN.COM API returned a non-success status."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"ZEN.COM API error {status_code}: {body}")


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


def normalize_direction(value: str | None) -> str:
    text = (value or "").upper()
    if text in {"IN", "INCOMING"}:
        return "INCOMING"
    if text in {"OUT", "OUTGOING"}:
        return "OUTGOING"
    return text


def payment_unique_id(tx_id: str) -> str:
    return f"zen:pay:{tx_id}"


def fee_unique_id(tx_id: str, index: int = 0) -> str:
    suffix = "" if index == 0 else f":{index}"
    return f"zen:pay:{tx_id}:fee{suffix}"


def normalize_payments(payload: Any) -> list[dict[str, Any]]:
    """Payment details may be one object (docs) or a one-item list (live)."""
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        if payload.get("id") or payload.get("paymentId"):
            return [payload]
        data = payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        if isinstance(data, dict):
            return [data]
    return []


def parse_webhook_events(payload: Any) -> list[dict[str, Any]]:
    """ZEN notification, n8n ``{body, webhookUrl}`` wrapper, or a list."""
    if payload is None:
        return []
    if isinstance(payload, list):
        events: list[dict[str, Any]] = []
        for item in payload:
            events.extend(parse_webhook_events(item))
        return events
    if not isinstance(payload, dict):
        return []
    body = payload.get("body")
    if isinstance(body, dict) and (body.get("paymentId") or body.get("accountId")):
        return parse_webhook_events(body)
    payment_id = payload.get("paymentId") or payload.get("id")
    if not payment_id:
        return []
    status = (payload.get("transactionStatus") or payload.get("status") or "").upper()
    return [
        {
            "payment_id": str(payment_id),
            "account_id": str(payload.get("accountId") or ""),
            "status": status,
            "direction": normalize_direction(payload.get("direction")),
            "external_id": payload.get("externalId"),
        }
    ]


def unwrap_history_page(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Live history is ``[{data, meta}]``; the docs show a bare object."""
    page = payload
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and ("data" in item or "meta" in item):
                page = item
                break
        else:
            rows = [item for item in payload if isinstance(item, dict) and item.get("id")]
            return rows, {}
    if not isinstance(page, dict):
        return [], {}
    rows = page.get("data") or []
    if not isinstance(rows, list):
        rows = []
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else {}
    return rows, meta


@dataclass(frozen=True)
class ZenTLS:
    """Client certificate material for the Transfers API mTLS handshake."""

    client_cert: str
    client_key: str
    ca_cert: str | None = None
    key_password: str | None = None

    def validate(self) -> None:
        cert = (self.client_cert or "").strip()
        key = (self.client_key or "").strip()
        if "BEGIN CERTIFICATE" not in cert:
            raise ZenConfigError(
                "ZEN.COM mTLS client certificate must be PEM "
                "(-----BEGIN CERTIFICATE-----)"
            )
        if "BEGIN" not in key or "PRIVATE KEY" not in key:
            raise ZenConfigError(
                "ZEN.COM mTLS private key must be PEM "
                "(-----BEGIN PRIVATE KEY----- or -----BEGIN RSA PRIVATE KEY-----)"
            )


def _write_pem(content: str) -> str:
    handle, path = tempfile.mkstemp(prefix="zen-mtls-", suffix=".pem")
    try:
        os.write(handle, content.strip().encode())
    finally:
        os.close(handle)
    os.chmod(path, 0o600)
    return path


def build_ssl_context(tls: ZenTLS) -> tuple[ssl.SSLContext, list[str]]:
    """Load client cert/key (and optional CA) into an SSL context."""
    tls.validate()
    temps: list[str] = []
    try:
        cert_path = _write_pem(tls.client_cert)
        key_path = _write_pem(tls.client_key)
        temps.extend([cert_path, key_path])
        context = ssl.create_default_context()
        if tls.ca_cert and tls.ca_cert.strip():
            context.load_verify_locations(cadata=tls.ca_cert.strip())
        context.load_cert_chain(
            certfile=cert_path,
            keyfile=key_path,
            password=tls.key_password or None,
        )
        return context, temps
    except Exception:
        for path in temps:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise


def requests_get_mtls(
    url: str,
    headers: dict[str, str],
    tls: ZenTLS | None = None,
    *,
    timeout: int = 30,
):
    """GET with client certificate. Used by the Odoo provider."""
    import requests
    from requests.adapters import HTTPAdapter

    if tls is None:
        raise ZenConfigError(
            "ZEN.COM Transfers API requires mTLS. "
            "Set the client certificate and private key on the provider."
        )
    context, temps = build_ssl_context(tls)

    class _MTLSAdapter(HTTPAdapter):
        def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
            kwargs["ssl_context"] = context
            return super().init_poolmanager(connections, maxsize, block, **kwargs)

        def proxy_manager_for(self, proxy, **kwargs):
            kwargs["ssl_context"] = context
            return super().proxy_manager_for(proxy, **kwargs)

    session = requests.Session()
    session.mount("https://", _MTLSAdapter())
    try:
        response = session.get(url, headers=headers, timeout=timeout)
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        return response.status_code, payload
    finally:
        session.close()
        for path in temps:
            try:
                os.unlink(path)
            except OSError:
                pass


def _as_date(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


def zen_query_dates(
    date_since: datetime | date, date_until: datetime | date
) -> tuple[str, str]:
    """Inclusive yyyy-MM-dd window for ZEN history.

    OCA ``date_until`` is exclusive (next midnight). ZEN ``*AtTo`` is inclusive
    and a future ``bookedAtTo`` has produced ``INTERNAL_SERVER_ERROR``.
    """
    start = date_since.date() if isinstance(date_since, datetime) else date_since
    if isinstance(date_until, datetime):
        end = date_until.date()
        if date_until.time() == datetime.min.time() and end > start:
            end = end - timedelta(days=1)
    else:
        end = date_until
    today = datetime.now(timezone.utc).date()
    if end > today:
        end = today
    if end < start:
        end = start
    return start.isoformat(), end.isoformat()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _amount_value(transaction: dict[str, Any]) -> float:
    amount = transaction.get("amount") or {}
    raw = amount.get("value", 0)
    return float(raw)


def _signed_amount(transaction: dict[str, Any]) -> float:
    value = abs(_amount_value(transaction))
    direction = normalize_direction(transaction.get("direction"))
    if direction == "OUTGOING":
        return -value
    return value


def _counterparty(transaction: dict[str, Any]) -> dict[str, Any]:
    direction = normalize_direction(transaction.get("direction"))
    if direction == "OUTGOING":
        party = transaction.get("receiver") or {}
    else:
        party = transaction.get("sender") or {}
    return {
        "partner_name": party.get("name") or False,
        "account_number": party.get("accountNumber") or False,
    }


def status_label(transaction: dict[str, Any]) -> str:
    status = (transaction.get("status") or transaction.get("transactionStatus") or "").upper()
    if status == "SETTLED":
        return "paid"
    if status == "IN_PROGRESS":
        return "pending"
    if status == "REJECTED":
        return "rejected"
    return status.lower() or "unknown"


def _payment_ref(status: str, partner: str | None, detail: str) -> str:
    if partner and detail and partner not in detail:
        return f"[{status}] {partner} — {detail}"
    if partner:
        return f"[{status}] {partner}"
    return f"[{status}] {detail}"


def fee_items(transaction: dict[str, Any]) -> list[tuple[str, dict[str, Any], float]]:
    items: list[tuple[str, dict[str, Any], float]] = []
    for key in ("senderFees", "receiverFees"):
        for fee in transaction.get(key) or []:
            if not isinstance(fee, dict):
                continue
            amount = abs(float((fee.get("amount") or {}).get("value") or 0))
            if amount:
                items.append((key, fee, amount))
    return items


def statement_line_from_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    """Convert one ZEN payment-history / payment-details item to a line."""
    booked = _parse_datetime(transaction.get("bookedAt"))
    created = _parse_datetime(transaction.get("createdAt"))
    when = booked or created
    if when is None:
        raise ValueError(f"ZEN transaction {transaction.get('id')} has no date")

    title = (transaction.get("title") or "").strip()
    tx_type = transaction.get("transactionType") or "PAYMENT"
    channel = (transaction.get("channel") or "").strip()
    counterparty = _counterparty(transaction)
    partner = counterparty["partner_name"] or None
    detail = title or channel or tx_type
    unique_id = str(transaction.get("id") or transaction.get("paymentId") or "")
    if not unique_id:
        raise ValueError("ZEN transaction is missing id")

    narration = " ".join(
        part
        for part in (
            f"zen={unique_id}",
            f"type={tx_type}" if tx_type else None,
            f"status={transaction.get('status')}" if transaction.get("status") else None,
            f"channel={channel}" if channel else None,
            f"related={transaction.get('relatedTransaction')}"
            if transaction.get("relatedTransaction")
            else None,
            title or None,
        )
        if part
    )
    line = {
        "date": when,
        "payment_ref": _payment_ref(status_label(transaction), partner, detail),
        "ref": unique_id,
        "unique_import_id": payment_unique_id(unique_id),
        "amount": _signed_amount(transaction),
        "transaction_type": tx_type,
        "partner_name": partner or False,
        "account_number": counterparty["account_number"],
        "narration": narration or False,
    }
    currency = (transaction.get("amount") or {}).get("currency")
    if currency:
        line["currency_code"] = currency
    return line


def fee_lines_from_transaction(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    main = statement_line_from_transaction(transaction)
    lines: list[dict[str, Any]] = []
    currency = (transaction.get("amount") or {}).get("currency")
    for index, (source, fee, amount) in enumerate(fee_items(transaction)):
        name = fee.get("name") or "FEE"
        lines.append(
            {
                "date": main["date"],
                "payment_ref": f"[fee] ZEN — {main['ref']}",
                "ref": main["ref"],
                "unique_import_id": fee_unique_id(str(main["ref"]), index),
                "amount": -amount,
                "transaction_type": "fee",
                "partner_name": "ZEN.COM",
                "account_number": False,
                "narration": f"{source} {name} for {main['payment_ref']}",
                **({"currency_code": currency} if currency else {}),
            }
        )
    return lines


def statement_lines_from_transaction(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    return [statement_line_from_transaction(transaction), *fee_lines_from_transaction(transaction)]


def iter_settled_transactions(transactions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only booked/settled movements (never pending or rejected)."""
    settled = []
    for transaction in transactions:
        status = (transaction.get("status") or transaction.get("transactionStatus") or "").upper()
        if status != SETTLED_STATUS:
            continue
        if not transaction.get("bookedAt") and status == SETTLED_STATUS:
            # Some payloads mark SETTLED before bookedAt is stamped; still keep them.
            pass
        settled.append(transaction)
    return settled


def statement_lines_from_transactions(
    transactions: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for tx in iter_settled_transactions(transactions):
        lines.extend(statement_lines_from_transaction(tx))
    return lines


class ZenClient:
    """Minimal Transfers API client.

    ``http_get`` is injectable so tests do not need the network:
    ``http_get(url, headers) -> (status_code, json_dict_or_text)``.
    """

    def __init__(
        self,
        api_key: str,
        *,
        api_base: str | None = None,
        account_id: str | None = None,
        iban: str | None = None,
        tls: ZenTLS | None = None,
        http_get: Callable[..., tuple[int, Any]] | None = None,
        page_limit: int = DEFAULT_PAGE_LIMIT,
    ):
        if not api_key:
            raise ZenConfigError("ZEN.COM API key is required")
        self.api_key = _normalize_api_key(api_key)
        self.api_base = (api_base or ZEN_DEFAULT_API_BASE).rstrip("/") + "/"
        self.account_id = account_id or None
        self.iban = (iban or "").replace(" ", "").upper() or None
        self.tls = tls
        self.page_limit = page_limit
        self._http_get = http_get

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        url = urljoin(self.api_base, path.lstrip("/"))
        if params:
            filtered = {key: value for key, value in params.items() if value not in (None, "")}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        return url

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path, params)
        if self._http_get is None:
            raise ZenConfigError("No HTTP backend configured")
        try:
            status, payload = self._http_get(url, self._headers(), self.tls)
        except TypeError:
            status, payload = self._http_get(url, self._headers())
        if status >= 400:
            body = payload if isinstance(payload, str) else str(payload)
            raise ZenHTTPError(status, body)
        return payload

    def list_accounts(self) -> list[dict[str, Any]]:
        payload = self._get(ACCOUNTS_PATH)
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            data = payload.get("data", payload.get("accounts", []))
            if isinstance(data, list):
                return data
        raise ZenHTTPError(200, f"Unexpected accounts payload: {payload!r}")

    def resolve_account_id(self) -> str:
        if self.account_id:
            return self.account_id
        if not self.iban:
            raise ZenConfigError(
                "Set the ZEN account UUID or the IBAN on the journal bank account"
            )
        for account in self.list_accounts():
            numbers = account.get("accountNumbers") or []
            if isinstance(numbers, dict):
                numbers = [numbers]
            candidates = [
                (account.get("accountNumber") or ""),
                *[item.get("accountNumber") or "" for item in numbers],
            ]
            for number in candidates:
                if number.replace(" ", "").upper() == self.iban:
                    account_id = account.get("accountId") or account.get("id")
                    if account_id:
                        return str(account_id)
        raise ZenConfigError(f"No ZEN.COM account matches IBAN {self.iban}")

    def iter_history(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
        *,
        account_id: str | None = None,
    ) -> list[dict[str, Any]]:
        resolved_id = account_id or self.resolve_account_id()
        collected: list[dict[str, Any]] = []
        last_entry_id = None
        while True:
            payload = self._get(
                HISTORY_PATH,
                {
                    "accountId": resolved_id,
                    "bookedAtFrom": _as_date(date_since),
                    "bookedAtTo": _as_date(date_until),
                    "limit": self.page_limit,
                    "lastEntryId": last_entry_id,
                },
            )
            rows, meta = unwrap_history_page(payload)
            if not rows:
                break
            collected.extend(rows)
            if not meta.get("hasNext"):
                break
            last_entry_id = meta.get("lastEntryId")
            if not last_entry_id:
                break
        return collected

    def get_payment(self, payment_id: str) -> dict[str, Any]:
        payload = self._get(f"{PAYMENT_PATH}/{payment_id}")
        payments = normalize_payments(payload)
        if not payments:
            raise ZenHTTPError(200, f"ZEN payment {payment_id} returned no details")
        return payments[0]

    def obtain_statement_lines_for_payment(
        self, payment_id: str
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        payment = self.get_payment(payment_id)
        return statement_lines_from_transactions([payment]), {}

    def obtain_statement_lines(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        transactions = self.iter_history(date_since, date_until)
        return statement_lines_from_transactions(transactions), {}
