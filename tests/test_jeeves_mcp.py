from datetime import datetime

from account_statement_import_jeeves.lib.jeeves_mcp import (
    JEEVES_MCP_URL,
    JeevesMCPClient,
    extract_transactions_from_text,
    statement_line_from_mcp_transaction,
    statement_lines_from_mcp_transactions,
    transaction_unique_id,
    unwrap_mcp_transactions,
)

N8N_TEXT = (
    "total records: 12, transactions: ["
    '{"transactionType":"debit","transactionTypeTag":"PAYMENT",'
    '"transactionStatus":"settled","transactionDate":"2026-09-03T12:43:02.526Z",'
    '"transactionPostedDate":"2026-09-03T12:43:16.852Z",'
    '"createdAt":"2026-09-03T12:43:02.546Z","totalBaseCurrencyAmount":450,'
    '"source":{"name":"EUR Account","detail":"9330","currency":978,'
    '"currencyAlphaCode":"EUR"},'
    '"destination":{"name":"Klara Hoffmann","detail":"7552","currency":978,'
    '"currencyAlphaCode":"EUR"}},'
    '{"transactionType":"credit","transactionTypeTag":"DEPOSIT",'
    '"transactionStatus":"settled","transactionDate":"2026-08-31T00:53:27.756Z",'
    '"transactionPostedDate":"2026-08-31T00:53:27.801Z",'
    '"createdAt":"2026-08-31T00:53:27.801Z","totalBaseCurrencyAmount":220,'
    '"source":{"name":"Karen Naber","detail":"","currency":978,'
    '"currencyAlphaCode":"EUR"},'
    '"destination":{"name":"EUR Account","detail":"9330","currency":978,'
    '"currencyAlphaCode":"EUR"}}]'
)

N8N_WRAP = [{"content": [{"type": "text", "text": N8N_TEXT}]}]


def test_unwraps_n8n_mcp_client_text_wrapper():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    assert len(rows) == 2
    assert rows[0]["destination"]["name"] == "Klara Hoffmann"
    assert rows[1]["source"]["name"] == "Karen Naber"


def test_extract_ignores_total_records_prefix():
    rows = extract_transactions_from_text(N8N_TEXT)
    assert len(rows) == 2


def test_debit_bill_and_credit_deposit_signs():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    debit = statement_line_from_mcp_transaction(rows[0])
    credit = statement_line_from_mcp_transaction(rows[1])
    assert debit["amount"] == -450.0
    assert debit["partner_name"] == "Klara Hoffmann"
    assert debit["payment_ref"] == "Klara Hoffmann — PAYMENT"
    assert debit["currency_code"] == "EUR"
    assert debit["date"] == datetime(2026, 9, 3, 12, 43, 16, 852000)
    assert debit["unique_import_id"].startswith("jeeves:mcp:")
    assert credit["amount"] == 220.0
    assert credit["partner_name"] == "Karen Naber"
    assert credit["payment_ref"] == "Karen Naber — DEPOSIT"
    assert "EUR Account" not in (credit["partner_name"] or "")


def test_fingerprint_is_stable_and_skips_pending():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    first = transaction_unique_id(rows[0])
    assert transaction_unique_id(rows[0]) == first
    pending = dict(rows[0], transactionStatus="pending")
    lines = statement_lines_from_mcp_transactions([pending, rows[0]])
    assert [line["unique_import_id"] for line in lines] == [first]


def test_client_initialize_then_list_transaction():
    calls = []

    def http_request(method, url, headers, data):
        payload = data if isinstance(data, str) else data.decode()
        calls.append((method, url, headers, payload))
        if '"method": "initialize"' in payload or '"method":"initialize"' in payload:
            return 200, {"Mcp-Session-Id": "sess-1", "Content-Type": "application/json"}, (
                '{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2024-11-05"}}'
            )
        if "notifications/initialized" in payload:
            return 202, {"Mcp-Session-Id": "sess-1"}, ""
        import json as _json

        return (
            200,
            {"Content-Type": "application/json", "Mcp-Session-Id": "sess-1"},
            _json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"content": [{"type": "text", "text": N8N_TEXT}]},
                }
            ),
        )

    client = JeevesMCPClient(
        "test-key",
        account_id="acc-eur-1",
        http_request=http_request,
    )
    lines, extras = client.obtain_statement_lines(
        datetime(2026, 8, 31), datetime(2026, 9, 4)
    )
    assert extras == {}
    assert len(lines) == 2
    assert lines[0]["amount"] == -450.0
    assert any("list_transaction" in (data or "") for _m, _u, _h, data in calls)
    assert any(call[2].get("Authorization") == "Bearer test-key" for call in calls)
    assert client.mcp_url == JEEVES_MCP_URL
