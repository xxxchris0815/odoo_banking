"""ZEN.COM Transfers API helpers (Odoo-independent).

Maps the public Transfers API (accounts + payment history) onto the
line dicts expected by OCA ``online.bank.statement.provider``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable
from urllib.parse import urlencode, urljoin

ZEN_DEFAULT_API_BASE = "https://api-services.zen.com"
ZEN_TEST_API_BASE = "https://api-services.zen-test.com"
ACCOUNTS_PATH = "accounts/v1.0"
HISTORY_PATH = "payments/v1.0"
SETTLED_STATUS = "SETTLED"
DEFAULT_PAGE_LIMIT = 100


class ZenConfigError(ValueError):
    """Provider is missing credentials or account identification."""


class ZenHTTPError(RuntimeError):
    """ZEN.COM API returned a non-success status."""

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"ZEN.COM API error {status_code}: {body}")


def _as_date(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


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
    direction = (transaction.get("direction") or "").upper()
    if direction == "OUTGOING":
        return -value
    return value


def _counterparty(transaction: dict[str, Any]) -> dict[str, Any]:
    direction = (transaction.get("direction") or "").upper()
    if direction == "OUTGOING":
        party = transaction.get("receiver") or {}
    else:
        party = transaction.get("sender") or {}
    return {
        "partner_name": party.get("name") or False,
        "account_number": party.get("accountNumber") or False,
    }


def statement_line_from_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    """Convert one ZEN payment-history item to an OCA statement line."""
    booked = _parse_datetime(transaction.get("bookedAt"))
    created = _parse_datetime(transaction.get("createdAt"))
    when = booked or created
    if when is None:
        raise ValueError(f"ZEN transaction {transaction.get('id')} has no date")

    title = (transaction.get("title") or "").strip()
    tx_type = transaction.get("transactionType") or "PAYMENT"
    counterparty = _counterparty(transaction)
    payment_ref = title or counterparty["partner_name"] or tx_type
    unique_id = str(transaction.get("id") or "")
    if not unique_id:
        raise ValueError("ZEN transaction is missing id")

    line = {
        "date": when,
        "payment_ref": payment_ref,
        "ref": unique_id,
        "unique_import_id": unique_id,
        "amount": _signed_amount(transaction),
        "transaction_type": tx_type,
        "partner_name": counterparty["partner_name"],
        "account_number": counterparty["account_number"],
        "narration": title or False,
    }
    currency = (transaction.get("amount") or {}).get("currency")
    if currency:
        line["currency_code"] = currency
    return line


def iter_settled_transactions(transactions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only booked/settled movements (never pending or rejected)."""
    settled = []
    for transaction in transactions:
        status = (transaction.get("status") or "").upper()
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
    return [
        statement_line_from_transaction(tx)
        for tx in iter_settled_transactions(transactions)
    ]


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
        http_get: Callable[[str, dict[str, str]], tuple[int, Any]] | None = None,
        page_limit: int = DEFAULT_PAGE_LIMIT,
    ):
        if not api_key:
            raise ZenConfigError("ZEN.COM API key is required")
        self.api_key = api_key
        self.api_base = (api_base or ZEN_DEFAULT_API_BASE).rstrip("/") + "/"
        self.account_id = account_id or None
        self.iban = (iban or "").replace(" ", "").upper() or None
        self.page_limit = page_limit
        self._http_get = http_get

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self.api_key,
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
            rows = payload.get("data") if isinstance(payload, dict) else payload
            if not rows:
                break
            collected.extend(rows)
            meta = payload.get("meta") if isinstance(payload, dict) else {}
            if not meta.get("hasNext"):
                break
            last_entry_id = meta.get("lastEntryId")
            if not last_entry_id:
                break
        return collected

    def obtain_statement_lines(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        transactions = self.iter_history(date_since, date_until)
        return statement_lines_from_transactions(transactions), {}
