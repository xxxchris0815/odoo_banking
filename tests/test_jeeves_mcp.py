from datetime import datetime

from account_statement_import_jeeves.lib.jeeves_mcp import (
    JEEVES_MCP_URL,
    JeevesMCPClient,
    JeevesMCPConfigError,
    extract_transactions_from_text,
    mcp_query_datetimes,
    parse_mcp_http_body,
    resolve_product_account_id,
    statement_line_from_mcp_transaction,
    statement_lines_from_mcp_transactions,
    transaction_unique_id,
    unwrap_mcp_accounts,
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

ACCOUNTS_TEXT = (
    '[{"accountId":"20ae41af-4f9b-43a6-8c55-147ea0611e66",'
    '"accountName":"EUR Account","accountNumberLastFour":"9330",'
    '"accountStatus":"active","currencyAlphaCode":"EUR","type":"self-funded"},'
    '{"accountId":"5b633820-aec4-47b4-83f6-1db2caa16da6",'
    '"accountName":"USD Account","accountNumberLastFour":"1506",'
    '"accountStatus":"active","currencyAlphaCode":"USD","type":"self-funded"},'
    '{"accountName":"Credit account","accountStatus":"inactive",'
    '"currencyAlphaCode":"USD","type":"jeeves-pay-credit"}]'
)

SSE_TOOLS_CALL = (
    "event: message\n"
    'data: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":'
    + __import__("json").dumps(N8N_TEXT)
    + "}]}}\n\n"
)


def test_unwraps_n8n_mcp_client_text_wrapper():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    assert len(rows) == 2
    assert rows[0]["destination"]["name"] == "Klara Hoffmann"
    assert rows[1]["source"]["name"] == "Karen Naber"


def test_extract_ignores_total_records_prefix():
    rows = extract_transactions_from_text(N8N_TEXT)
    assert len(rows) == 2


def test_parses_sse_tools_call():
    parsed = parse_mcp_http_body(SSE_TOOLS_CALL, "text/event-stream")
    rows = unwrap_mcp_transactions(parsed)
    assert len(rows) == 2
    assert rows[0]["totalBaseCurrencyAmount"] == 450


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


def test_official_id_beats_fingerprint():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    with_id = dict(rows[0], id="bec29741-c1b1-43ed-ad5a-2525dcf84021")
    assert transaction_unique_id(with_id) == (
        "jeeves:mcp:bec29741-c1b1-43ed-ad5a-2525dcf84021"
    )


def test_vendor_fields_on_line():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    with_vendor = dict(
        rows[0],
        vendor={
            "id": "55ee019e-510e-4f3c-8158-6411f9369d49",
            "email": "info@naturrauch.de",
        },
    )
    line = statement_line_from_mcp_transaction(with_vendor)
    assert line["jeeves_vendor_id"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
    assert line["partner_email"] == "info@naturrauch.de"


def test_fingerprint_is_stable_and_skips_pending():
    rows = unwrap_mcp_transactions(N8N_WRAP)
    first = transaction_unique_id(rows[0])
    assert transaction_unique_id(rows[0]) == first
    pending = dict(rows[0], transactionStatus="pending")
    lines = statement_lines_from_mcp_transactions([pending, rows[0]])
    assert [line["unique_import_id"] for line in lines] == [first]


def test_resolves_eur_cash_account_and_skips_credit_line():
    accounts = unwrap_mcp_accounts(
        {"result": {"content": [{"type": "text", "text": ACCOUNTS_TEXT}]}}
    )
    assert len(accounts) == 3
    assert resolve_product_account_id(accounts, currency="EUR") == (
        "20ae41af-4f9b-43a6-8c55-147ea0611e66"
    )
    assert resolve_product_account_id(
        accounts, account_id="5b633820-aec4-47b4-83f6-1db2caa16da6"
    ) == "5b633820-aec4-47b4-83f6-1db2caa16da6"


def test_oca_until_midnight_is_exclusive():
    start, end = mcp_query_datetimes(
        datetime(2026, 8, 1, 0, 0, 0), datetime(2026, 9, 6, 0, 0, 0)
    )
    assert start == "2026-08-01T00:00:00Z"
    assert end == "2026-09-05T23:59:59Z"


def test_client_initialize_then_list_transactions():
    calls = []

    def http_request(method, url, headers, data):
        payload = data if isinstance(data, str) else data.decode()
        calls.append((method, url, headers, payload))
        if '"method": "initialize"' in payload or '"method":"initialize"' in payload:
            return 200, {"Content-Type": "text/event-stream"}, (
                "event: message\n"
                'data: {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26"}}\n\n'
            )
        if "notifications/initialized" in payload:
            return 202, {}, ""
        import json as _json

        body = _json.loads(payload)
        assert body["params"]["name"] == "list_transactions"
        args = body["params"]["arguments"]
        assert args["startDate"] == "2026-08-31T00:00:00Z"
        assert args["endDate"] == "2026-09-03T23:59:59Z"
        assert args["productAccountIds"] == ["acc-eur-1"]
        assert args["page"] == 1
        assert args["transactionStatuses"] == ["settled"]
        assert args["selectedFields"]["id"] is True
        return (
            200,
            {"Content-Type": "text/event-stream"},
            SSE_TOOLS_CALL,
        )

    client = JeevesMCPClient(
        "Bearer test-key",
        account_id="acc-eur-1",
        http_request=http_request,
    )
    lines, extras = client.obtain_statement_lines(
        datetime(2026, 8, 31), datetime(2026, 9, 4)
    )
    assert extras == {}
    assert len(lines) == 2
    assert lines[0]["amount"] == -450.0
    assert any("list_transactions" in (data or "") for _m, _u, _h, data in calls)
    assert not any(
        '"name": "list_transaction"' in (data or "")
        and "list_transactions" not in (data or "")
        for _m, _u, _h, data in calls
    )
    assert any(call[2].get("Authorization") == "Bearer test-key" for call in calls)
    assert any(call[2].get("User-Agent") == "odoo-jeeves/19.0" for call in calls)
    assert client.mcp_url == JEEVES_MCP_URL


def test_client_paginates_and_resolves_account_from_list_accounts():
    calls = []

    def page_text(page: int) -> str:
        if page == 1:
            return (
                "total records: 3, transactions: ["
                '{"transactionType":"debit","transactionTypeTag":"PAYMENT",'
                '"transactionStatus":"settled","createdAt":"2026-09-01T00:00:00Z",'
                '"transactionPostedDate":"2026-09-01T00:00:01Z",'
                '"totalBaseCurrencyAmount":10,'
                '"source":{"name":"EUR Account","detail":"9330","currencyAlphaCode":"EUR"},'
                '"destination":{"name":"A","detail":"1","currencyAlphaCode":"EUR"}}]'
            )
        return (
            "total records: 3, transactions: ["
            '{"transactionType":"credit","transactionTypeTag":"DEPOSIT",'
            '"transactionStatus":"settled","createdAt":"2026-09-02T00:00:00Z",'
            '"transactionPostedDate":"2026-09-02T00:00:01Z",'
            '"totalBaseCurrencyAmount":20,'
            '"source":{"name":"B","detail":"","currencyAlphaCode":"EUR"},'
            '"destination":{"name":"EUR Account","detail":"9330","currencyAlphaCode":"EUR"}},'
            '{"transactionType":"debit","transactionTypeTag":"PAYMENT",'
            '"transactionStatus":"settled","createdAt":"2026-09-03T00:00:00Z",'
            '"transactionPostedDate":"2026-09-03T00:00:01Z",'
            '"totalBaseCurrencyAmount":30,'
            '"source":{"name":"EUR Account","detail":"9330","currencyAlphaCode":"EUR"},'
            '"destination":{"name":"C","detail":"2","currencyAlphaCode":"EUR"}}]'
        )

    def http_request(method, url, headers, data):
        payload = data if isinstance(data, str) else data.decode()
        calls.append(payload)
        if '"method": "initialize"' in payload or '"method":"initialize"' in payload:
            return 200, {"Content-Type": "application/json"}, (
                '{"jsonrpc":"2.0","id":1,"result":{}}'
            )
        if "notifications/initialized" in payload:
            return 202, {}, ""
        import json as _json

        body = _json.loads(payload)
        name = body["params"]["name"]
        if name == "list_accounts":
            return 200, {"Content-Type": "application/json"}, _json.dumps(
                {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": ACCOUNTS_TEXT}]}}
            )
        assert name == "list_transactions"
        page = body["params"]["arguments"]["page"]
        assert body["params"]["arguments"]["productAccountIds"] == [
            "20ae41af-4f9b-43a6-8c55-147ea0611e66"
        ]
        text = page_text(page)
        return 200, {"Content-Type": "application/json"}, _json.dumps(
            {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}}
        )

    client = JeevesMCPClient(
        "test-key",
        currency="EUR",
        http_request=http_request,
    )
    client.page_size = 1
    lines, _extras = client.obtain_statement_lines(
        datetime(2026, 8, 31), datetime(2026, 9, 4)
    )

    assert [line["amount"] for line in lines] == [-10.0, 20.0, -30.0]
    assert any("list_accounts" in payload for payload in calls)
    assert sum("list_transactions" in payload for payload in calls) == 2


def test_refuses_write_tools():
    client = JeevesMCPClient("key", account_id="x", http_request=lambda *a, **k: (200, {}, "{}"))
    try:
        client.call_tool("create_card", {"step": "CAPTURE_THE_DETAILS"})
    except JeevesMCPConfigError as error:
        assert "write tool" in str(error)
    else:
        raise AssertionError("expected write tools to be refused")
