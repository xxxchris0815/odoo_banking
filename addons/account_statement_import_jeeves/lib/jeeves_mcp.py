"""Jeeves MCP client (Odoo-independent).

Live server: ``https://mcp-prod.jeev.es/mcp`` (Streamable HTTP, often SSE).
Auth is ``Authorization: Bearer <key>``. Allowed tools: statement reads
plus ``list_vendors`` / ``create_vendor`` / ``update_vendor``. Card and
payment tools stay blocked.

``list_transactions`` needs ``startDate`` (ISO). Optional filters we send:
``endDate``, ``productAccountIds``, ``page``, ``pageSize``,
``transactionStatuses`` (settled), ``selectedFields`` (ids when the
server honours them). The text payload is
``total records: N, transactions: [{…}]``. Default rows have no Unique ID;
then we fingerprint createdAt + amount + party.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlparse

JEEVES_MCP_URL = "https://mcp-prod.jeev.es/mcp"
LIST_TRANSACTIONS_TOOL = "list_transactions"
LIST_ACCOUNTS_TOOL = "list_accounts"
LIST_VENDORS_TOOL = "list_vendors"
CREATE_VENDOR_TOOL = "create_vendor"
UPDATE_VENDOR_TOOL = "update_vendor"
READ_ONLY_TOOLS = frozenset({LIST_TRANSACTIONS_TOOL, LIST_ACCOUNTS_TOOL})
VENDOR_TOOLS = frozenset({LIST_VENDORS_TOOL, CREATE_VENDOR_TOOL, UPDATE_VENDOR_TOOL})
ALLOWED_TOOLS = READ_ONLY_TOOLS | VENDOR_TOOLS
MCP_PROTOCOL_VERSION = "2025-03-26"
PAGE_SIZE = 100
SETTLED_STATUSES = frozenset({"settled", "completed", "posted", "booked"})
WALLET_NAME_SUFFIX = " account"
SELECTED_TRANSACTION_FIELDS = {
    "id": True,
    "transactionId": True,
    "createdAt": True,
    "source": True,
    "destination": True,
    "transactionType": True,
    "transactionTypeTag": True,
    "transactionStatus": True,
    "transactionDate": True,
    "amounts": True,
}


class JeevesMCPError(RuntimeError):
    """MCP HTTP or payload error."""


class JeevesMCPConfigError(ValueError):
    """Provider is missing the API key or account id."""


def _naive_dt(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return datetime.combine(value, datetime.min.time())


def mcp_query_datetimes(
    date_since: datetime | date, date_until: datetime | date
) -> tuple[str, str]:
    """Inclusive ISO window. OCA ``date_until`` is exclusive next midnight."""
    start = _naive_dt(date_since)
    until = _naive_dt(date_until)
    if start is None or until is None:
        raise JeevesMCPConfigError("Jeeves MCP date window is missing")
    end = until
    if until.time() == datetime.min.time() and until > start:
        end = until - timedelta(seconds=1)
    if end < start:
        end = start
    return (
        start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _is_wallet_name(name: str) -> bool:
    return name.strip().lower().endswith(WALLET_NAME_SUFFIX)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _party(transaction: dict[str, Any]) -> dict[str, Any]:
    direction = (transaction.get("transactionType") or "").strip().lower()
    source = _as_dict(transaction.get("source"))
    dest = _as_dict(transaction.get("destination"))
    if direction == "credit":
        party = source
        wallet = dest
    else:
        party = dest
        wallet = source
    name = (party.get("name") or "").strip()
    if _is_wallet_name(name):
        name = ""
    return {
        "partner_name": name or False,
        "party_detail": (party.get("detail") or "").strip(),
        "wallet_name": (wallet.get("name") or "").strip(),
        "currency": (
            party.get("currencyAlphaCode")
            or wallet.get("currencyAlphaCode")
            or ""
        ).upper(),
    }


def _vendor(transaction: dict[str, Any]) -> dict[str, str]:
    vendor = transaction.get("vendor")
    if not isinstance(vendor, dict):
        vendor = {}
    vendor_id = (
        vendor.get("id")
        or vendor.get("vendorId")
        or transaction.get("vendorId")
        or ""
    )
    email = (
        vendor.get("email")
        or vendor.get("emailAddress")
        or transaction.get("vendorEmail")
        or ""
    )
    return {
        "jeeves_vendor_id": str(vendor_id).strip(),
        "partner_email": str(email).strip(),
    }


def _base_amount(transaction: dict[str, Any]) -> float:
    if transaction.get("totalBaseCurrencyAmount") is not None:
        return abs(float(transaction.get("totalBaseCurrencyAmount") or 0))
    amounts = transaction.get("amounts")
    if isinstance(amounts, dict):
        for key in (
            "totalBaseCurrencyAmount",
            "baseCurrencyAmount",
            "amount",
        ):
            if amounts.get(key) is not None:
                return abs(float(amounts[key] or 0))
    return 0.0


def transaction_unique_id(transaction: dict[str, Any]) -> str:
    official = (
        transaction.get("id")
        or transaction.get("transactionId")
        or transaction.get("uniqueId")
        or transaction.get("uniqueID")
    )
    if official:
        return f"jeeves:mcp:{official}"
    party = _party(transaction)
    raw = "|".join(
        [
            str(transaction.get("createdAt") or ""),
            str(
                transaction.get("transactionPostedDate")
                or transaction.get("postedAt")
                or ""
            ),
            str(transaction.get("transactionType") or ""),
            str(transaction.get("transactionTypeTag") or ""),
            f"{_base_amount(transaction):.2f}",
            str(party["partner_name"] or ""),
            str(party["party_detail"] or ""),
        ]
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"jeeves:mcp:{digest}"


def extract_json_array(text: str) -> list[Any]:
    if not text or not str(text).strip():
        return []
    blob = str(text).strip()
    match = re.search(r"transactions\s*:\s*(\[)", blob, flags=re.IGNORECASE)
    if match:
        blob = blob[match.start(1) :]
    start = blob.find("[")
    if start < 0:
        return []
    payload = json.loads(blob[start:])
    return payload if isinstance(payload, list) else []


def extract_transactions_from_text(text: str) -> list[dict[str, Any]]:
    """Parse ``total records: N, transactions: […]`` or a bare JSON array."""
    return [row for row in extract_json_array(text) if isinstance(row, dict)]


def extract_total_records(text: str) -> int | None:
    match = re.search(r"total records\s*:\s*(\d+)", str(text or ""), flags=re.I)
    if not match:
        return None
    return int(match.group(1))


def unwrap_mcp_json_value(payload: Any) -> Any:
    """Parse the JSON object/array hidden in MCP ``content[].text``."""
    for text in iter_mcp_text(payload):
        blob = str(text).strip()
        if not blob:
            continue
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            starts = [index for index in (blob.find("{"), blob.find("[")) if index >= 0]
            if not starts:
                continue
            try:
                return json.loads(blob[min(starts) :])
            except json.JSONDecodeError:
                continue
    if isinstance(payload, dict):
        if "data" in payload or "vendorCacheId" in payload or "vendorId" in payload:
            return payload
        result = payload.get("result")
        if isinstance(result, dict) and result.get("content") is None:
            return result
    if isinstance(payload, list):
        return payload
    return None


def mcp_tool_error_text(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict) or not result.get("isError"):
        return None
    texts = [text.strip() for text in iter_mcp_text(result) if str(text).strip()]
    return " ".join(texts) or "Jeeves MCP tool error"


def iter_mcp_text(payload: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(payload, list):
        for item in payload:
            texts.extend(iter_mcp_text(item))
        return texts
    if not isinstance(payload, dict):
        return texts
    result = payload.get("result")
    if result is not None and result is not payload:
        texts.extend(iter_mcp_text(result))
    content = payload.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("text") is not None:
                texts.append(str(block.get("text") or ""))
            else:
                texts.extend(iter_mcp_text(block))
    text = payload.get("text")
    if isinstance(text, str):
        texts.append(text)
    return texts


def unwrap_mcp_transactions(payload: Any) -> list[dict[str, Any]]:
    """Accept n8n MCP Client output, tools/call result, or a raw list."""
    if payload is None:
        return []
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        if "transactionType" in payload[0] or "totalBaseCurrencyAmount" in payload[0]:
            return [row for row in payload if isinstance(row, dict)]
        collected: list[dict[str, Any]] = []
        for item in payload:
            collected.extend(unwrap_mcp_transactions(item))
        return collected
    if not isinstance(payload, dict):
        return []
    if payload.get("transactionType") or payload.get("totalBaseCurrencyAmount"):
        return [payload]
    result = payload.get("result")
    if result is not None and result is not payload:
        found = unwrap_mcp_transactions(result)
        if found:
            return found
    content = payload.get("content")
    if isinstance(content, list):
        rows: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" or "text" in block:
                rows.extend(extract_transactions_from_text(str(block.get("text") or "")))
            else:
                rows.extend(unwrap_mcp_transactions(block))
        if rows:
            return rows
    text = payload.get("text")
    if isinstance(text, str):
        return extract_transactions_from_text(text)
    data = payload.get("transactions") or payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def unwrap_mcp_accounts(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    for text in iter_mcp_text(payload):
        rows = [row for row in extract_json_array(text) if isinstance(row, dict)]
        if rows:
            return rows
    if isinstance(payload, dict):
        data = payload.get("accounts") or payload.get("data")
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
    return []


def mcp_total_records(payload: Any) -> int | None:
    for text in iter_mcp_text(payload):
        total = extract_total_records(text)
        if total is not None:
            return total
    return None


def resolve_product_account_id(
    accounts: list[dict[str, Any]],
    *,
    account_id: str | None = None,
    currency: str | None = None,
) -> str:
    wanted = (account_id or "").strip()
    if wanted:
        return wanted
    active = [
        row
        for row in accounts
        if (row.get("accountStatus") or "").strip().lower() == "active"
        and (row.get("accountId") or "").strip()
    ]
    code = (currency or "").strip().upper()
    if code:
        matches = [
            row
            for row in active
            if (row.get("currencyAlphaCode") or "").strip().upper() == code
        ]
        if len(matches) == 1:
            return str(matches[0]["accountId"]).strip()
        if not matches:
            raise JeevesMCPConfigError(
                f"No active Jeeves cash account in {code}"
            )
        raise JeevesMCPConfigError(
            f"Multiple active Jeeves cash accounts in {code}"
        )
    if len(active) == 1:
        return str(active[0]["accountId"]).strip()
    raise JeevesMCPConfigError(
        "Set the Jeeves Cash account id on the provider "
        "(or leave it empty on a single-currency journal)"
    )


def statement_line_from_mcp_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    status = (transaction.get("transactionStatus") or "").strip().lower()
    if status and status not in SETTLED_STATUSES:
        raise ValueError(f"Jeeves MCP transaction is not settled: {status}")
    when = (
        _parse_datetime(transaction.get("transactionPostedDate"))
        or _parse_datetime(transaction.get("postedAt"))
        or _parse_datetime(transaction.get("transactionDate"))
        or _parse_datetime(transaction.get("createdAt"))
    )
    if when is None:
        raise ValueError("Jeeves MCP transaction has no date")
    direction = (transaction.get("transactionType") or "").strip().lower()
    tag = (transaction.get("transactionTypeTag") or "").strip()
    amount = _base_amount(transaction)
    if direction == "debit":
        amount = -amount
    party = _party(transaction)
    vendor = _vendor(transaction)
    partner = party["partner_name"] or None
    detail = tag or party["party_detail"] or ""
    if partner and detail and partner.casefold() != detail.casefold():
        payment_ref = f"{partner} — {detail}"
    else:
        payment_ref = partner or detail or tag or "Jeeves"
    unique_id = transaction_unique_id(transaction)
    narration = " ".join(
        part
        for part in (
            f"jeeves={unique_id}",
            f"type={tag or direction}" if (tag or direction) else None,
            f"status={status}" if status else None,
            f"wallet={party['wallet_name']}" if party["wallet_name"] else None,
            f"detail={party['party_detail']}" if party["party_detail"] else None,
        )
        if part
    )
    line = {
        "date": when,
        "payment_ref": payment_ref,
        "ref": unique_id,
        "unique_import_id": unique_id,
        "amount": amount,
        "partner_name": partner or False,
        "narration": narration or False,
        "transaction_type": tag or direction or False,
    }
    if party["currency"]:
        line["currency_code"] = party["currency"]
    if vendor["jeeves_vendor_id"]:
        line["jeeves_vendor_id"] = vendor["jeeves_vendor_id"]
    if vendor["partner_email"]:
        line["partner_email"] = vendor["partner_email"]
    return line


def statement_lines_from_mcp_transactions(
    transactions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for transaction in transactions:
        status = (transaction.get("transactionStatus") or "").strip().lower()
        if status and status not in SETTLED_STATUSES:
            continue
        lines.append(statement_line_from_mcp_transaction(transaction))
    return lines


def _decode_sse_json(body: str) -> Any:
    chunks: list[Any] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        chunks.append(json.loads(data))
    if not chunks:
        raise JeevesMCPError("MCP SSE response had no data frames")
    return chunks[-1]


def default_http_request(method, url, headers, data=None):
    """``requests`` backend used by the Odoo provider and vendor wizard."""
    import requests

    response = requests.request(
        method, url, headers=headers, data=data, timeout=60
    )
    return response.status_code, dict(response.headers), response.text


def parse_mcp_http_body(body: str | bytes, content_type: str = "") -> Any:
    text = body.decode() if isinstance(body, bytes) else (body or "")
    if not text.strip():
        return {}
    kind = (content_type or "").lower()
    stripped = text.lstrip()
    if "text/event-stream" in kind or stripped.startswith("event:") or stripped.startswith("data:"):
        return _decode_sse_json(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise JeevesMCPError(f"MCP response is not JSON: {text[:200]}") from error


class JeevesMCPClient:
    """Streamable-HTTP MCP client for read-only Jeeves tools."""

    def __init__(
        self,
        api_key: str,
        *,
        account_id: str | None = None,
        currency: str | None = None,
        mcp_url: str | None = None,
        http_request: Callable[..., tuple[int, dict[str, str], str]] | None = None,
    ):
        if not api_key:
            raise JeevesMCPConfigError("Jeeves MCP API key is required")
        self.api_key = api_key.strip()
        if self.api_key.lower().startswith("bearer "):
            self.api_key = self.api_key[7:].strip()
        self.account_id = (account_id or "").strip()
        self.currency = (currency or "").strip().upper()
        self.mcp_url = (mcp_url or JEEVES_MCP_URL).strip()
        parsed = urlparse(self.mcp_url if "://" in self.mcp_url else f"https://{self.mcp_url}")
        if not parsed.scheme:
            self.mcp_url = f"https://{self.mcp_url}"
        self._http_request = http_request
        self._session_id = ""
        self._initialized = False
        self.page_size = PAGE_SIZE

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "User-Agent": "odoo-jeeves/19.0",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _request(self, payload: dict[str, Any]) -> Any:
        if self._http_request is None:
            raise JeevesMCPConfigError("No HTTP backend configured")
        status, headers, body = self._http_request(
            "POST", self.mcp_url, self._headers(), json.dumps(payload)
        )
        session = ""
        if isinstance(headers, dict):
            session = (
                headers.get("Mcp-Session-Id")
                or headers.get("mcp-session-id")
                or ""
            )
        if session:
            self._session_id = session
        if status >= 400:
            raise JeevesMCPError(f"Jeeves MCP HTTP {status}: {body[:500]}")
        parsed = parse_mcp_http_body(
            body, (headers or {}).get("Content-Type") or (headers or {}).get("content-type") or ""
        )
        if isinstance(parsed, dict) and parsed.get("error"):
            raise JeevesMCPError(f"Jeeves MCP error: {parsed['error']}")
        tool_error = mcp_tool_error_text(parsed)
        if tool_error:
            raise JeevesMCPError(f"Jeeves MCP tool error: {tool_error}")
        return parsed

    def initialize(self) -> None:
        parsed = self._request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "odoo-jeeves", "version": "19.0.1.5"},
                },
            }
        )
        if isinstance(parsed, dict) and parsed.get("error"):
            raise JeevesMCPError(f"Jeeves MCP initialize failed: {parsed['error']}")
        try:
            self._request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except JeevesMCPError:
            # Some servers skip the notification and still accept tools/call.
            pass
        self._initialized = True

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        if name not in ALLOWED_TOOLS:
            raise JeevesMCPConfigError(f"Refusing Jeeves MCP write tool {name}")
        if not self._initialized:
            self.initialize()
        return self._request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )

    def list_accounts(self) -> list[dict[str, Any]]:
        return unwrap_mcp_accounts(self.call_tool(LIST_ACCOUNTS_TOOL, {}))

    def resolve_account_id(self) -> str:
        if self.account_id:
            return self.account_id
        return resolve_product_account_id(
            self.list_accounts(),
            account_id=self.account_id,
            currency=self.currency,
        )

    def list_transactions(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> list[dict[str, Any]]:
        start, end = mcp_query_datetimes(date_since, date_until)
        account_id = self.resolve_account_id()
        collected: list[dict[str, Any]] = []
        page = 1
        total = None
        while True:
            payload = self.call_tool(
                LIST_TRANSACTIONS_TOOL,
                {
                    "startDate": start,
                    "endDate": end,
                    "productAccountIds": [account_id],
                    "page": page,
                    "pageSize": self.page_size,
                    "transactionStatuses": ["settled"],
                    "selectedFields": dict(SELECTED_TRANSACTION_FIELDS),
                },
            )
            rows = unwrap_mcp_transactions(payload)
            collected.extend(rows)
            if total is None:
                total = mcp_total_records(payload)
            if total is not None and len(collected) >= total:
                break
            if len(rows) < self.page_size:
                break
            page += 1
            if page > 100:
                raise JeevesMCPError("Jeeves MCP list_transactions exceeded 100 pages")
        return collected

    def obtain_statement_lines(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = self.list_transactions(date_since, date_until)
        return statement_lines_from_mcp_transactions(rows), {}

    def list_vendors(
        self,
        search: str | None = None,
        *,
        page_size: int = 20,
    ) -> list[dict[str, Any]]:
        from .jeeves_vendors import unwrap_mcp_vendors

        collected: list[dict[str, Any]] = []
        page = 1
        total = None
        while True:
            arguments: dict[str, Any] = {"page": page, "pageSize": page_size}
            if search:
                arguments["searchQuery"] = search
            rows, reported = unwrap_mcp_vendors(
                self.call_tool(LIST_VENDORS_TOOL, arguments)
            )
            collected.extend(rows)
            if total is None:
                total = reported
            if total is not None and len(collected) >= total:
                break
            if len(rows) < page_size:
                break
            page += 1
            if page > 100:
                raise JeevesMCPError("Jeeves MCP list_vendors exceeded 100 pages")
        return collected

    def create_vendor(self, draft) -> dict[str, Any]:
        from .jeeves_vendors import (
            CREATE_VENDOR_TOOL,
            JeevesVendorError,
            build_create_contact_arguments,
            build_create_initial_arguments,
            build_create_payment_arguments,
            extract_created_vendor_id,
            extract_vendor_cache_id,
        )

        first = self.call_tool(
            CREATE_VENDOR_TOOL, build_create_initial_arguments(draft)
        )
        cache_id = extract_vendor_cache_id(first)
        second = self.call_tool(
            CREATE_VENDOR_TOOL, build_create_payment_arguments(draft, cache_id)
        )
        try:
            cache_id = extract_vendor_cache_id(second)
        except JeevesVendorError:
            pass
        third = self.call_tool(
            CREATE_VENDOR_TOOL, build_create_contact_arguments(draft, cache_id)
        )
        vendor_id = extract_created_vendor_id(third)
        return {"id": vendor_id, "payload": third}

    def update_vendor(self, draft) -> dict[str, Any]:
        from .jeeves_vendors import UPDATE_VENDOR_TOOL, build_update_arguments

        payload = self.call_tool(UPDATE_VENDOR_TOOL, build_update_arguments(draft))
        return {"id": draft.vendor_id, "payload": payload}
