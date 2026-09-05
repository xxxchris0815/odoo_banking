"""Jeeves MCP ``list_transaction`` → statement lines (Odoo-independent).

n8n/Cursor call ``https://mcp-prod.jeev.es/mcp``. The tool returns MCP
``content[].text`` with ``total records: N, transactions: [{…}]``.
There is no Unique ID in that payload — we fingerprint createdAt +
amount + party so a second daily pull does not duplicate.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

JEEVES_MCP_URL = "https://mcp-prod.jeev.es/mcp"
LIST_TRANSACTION_TOOL = "list_transaction"
SETTLED_STATUSES = frozenset({"settled", "completed", "posted", "booked"})
WALLET_NAME_SUFFIX = " account"


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


def _iso(value: datetime | date) -> str:
    when = _naive_dt(value)
    if when is None:
        raise JeevesMCPConfigError("Jeeves MCP date window is missing")
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _party(transaction: dict[str, Any]) -> dict[str, Any]:
    direction = (transaction.get("transactionType") or "").strip().lower()
    source = transaction.get("source") or {}
    dest = transaction.get("destination") or {}
    if not isinstance(source, dict):
        source = {}
    if not isinstance(dest, dict):
        dest = {}
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
            str(transaction.get("transactionPostedDate") or ""),
            str(transaction.get("transactionType") or ""),
            str(transaction.get("transactionTypeTag") or ""),
            f"{float(transaction.get('totalBaseCurrencyAmount') or 0):.2f}",
            str(party["partner_name"] or ""),
            str(party["party_detail"] or ""),
        ]
    )
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"jeeves:mcp:{digest}"


def extract_transactions_from_text(text: str) -> list[dict[str, Any]]:
    """Parse ``total records: N, transactions: […]`` or a bare JSON array."""
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
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


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


def statement_line_from_mcp_transaction(transaction: dict[str, Any]) -> dict[str, Any]:
    status = (transaction.get("transactionStatus") or "").strip().lower()
    if status and status not in SETTLED_STATUSES:
        raise ValueError(f"Jeeves MCP transaction is not settled: {status}")
    when = (
        _parse_datetime(transaction.get("transactionPostedDate"))
        or _parse_datetime(transaction.get("transactionDate"))
        or _parse_datetime(transaction.get("createdAt"))
    )
    if when is None:
        raise ValueError("Jeeves MCP transaction has no date")
    direction = (transaction.get("transactionType") or "").strip().lower()
    tag = (transaction.get("transactionTypeTag") or "").strip()
    amount = abs(float(transaction.get("totalBaseCurrencyAmount") or 0))
    if direction == "debit":
        amount = -amount
    party = _party(transaction)
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
    """Minimal Streamable-HTTP MCP client for ``list_transaction``."""

    def __init__(
        self,
        api_key: str,
        *,
        account_id: str,
        mcp_url: str | None = None,
        http_request: Callable[..., tuple[int, dict[str, str], str]] | None = None,
    ):
        if not api_key:
            raise JeevesMCPConfigError("Jeeves MCP API key is required")
        if not account_id:
            raise JeevesMCPConfigError("Jeeves MCP account id is required")
        self.api_key = api_key.strip()
        if self.api_key.lower().startswith("bearer "):
            self.api_key = self.api_key[7:].strip()
        self.account_id = account_id.strip()
        self.mcp_url = (mcp_url or JEEVES_MCP_URL).strip()
        parsed = urlparse(self.mcp_url if "://" in self.mcp_url else f"https://{self.mcp_url}")
        if not parsed.scheme:
            self.mcp_url = f"https://{self.mcp_url}"
        self._http_request = http_request
        self._session_id = ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2024-11-05",
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
        return parsed

    def initialize(self) -> None:
        parsed = self._request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "odoo-jeeves", "version": "19.0.1.3"},
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

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if not self._session_id:
            self.initialize()
        return self._request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )

    def list_transactions(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> list[dict[str, Any]]:
        payload = self.call_tool(
            LIST_TRANSACTION_TOOL,
            {
                "accountId": self.account_id,
                "start": _iso(date_since),
                "end": _iso(date_until),
            },
        )
        return unwrap_mcp_transactions(payload)

    def obtain_statement_lines(
        self,
        date_since: datetime | date,
        date_until: datetime | date,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows = self.list_transactions(date_since, date_until)
        return statement_lines_from_mcp_transactions(rows), {}
