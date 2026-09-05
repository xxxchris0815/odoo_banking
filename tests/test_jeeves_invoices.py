import json
from datetime import datetime
from pathlib import Path

from account_statement_import_jeeves.lib.jeeves_invoices import (
    build_bulk_payments_csv,
    detect_jeeves_bulk_payments_csv,
    enrich_statement_line_with_invoice,
    format_bulk_account_number,
    match_transaction_to_invoice,
    unwrap_mcp_invoices,
)
from account_statement_import_jeeves.lib.jeeves_csv import detect_jeeves_csv
from account_statement_import_jeeves.lib.jeeves_mcp import (
    JeevesMCPClient,
    parse_mcp_http_body,
    statement_line_from_mcp_transaction,
)

INVOICES = [
    {
        "invoiceId": "26ae6d6f-b1b5-4514-8ebb-5441d5173466",
        "invoiceNumber": "BILL/2026/09/0001",
        "paymentReferenceNumber": "JPP61Y2CT1S9Z",
        "transactionAmount": "325.000000",
        "status": "completed",
        "source": "BULK_PAYMENT",
        "createdAt": "2026-09-03T12:43:00.000Z",
        "total": {"amount": 325, "currencyAlphaCode": "EUR"},
        "vendor": {
            "id": "9efdc906-5452-4dff-bbfc-1e9028cb0318",
            "vendorName": "LALAVANDA vl. Dajana Grgic",
            "emailAddress": "dajana@orgasmic.live",
        },
    },
    {
        "invoiceId": "63555f80-c7d9-4386-b14f-e4c0a0bd1755",
        "invoiceNumber": "PROV00352/2026/08/0001",
        "paymentReferenceNumber": "JPPKLARA",
        "transactionAmount": "450.000000",
        "status": "completed",
        "createdAt": "2026-09-03T12:43:00.000Z",
        "total": {"amount": 450, "currencyAlphaCode": "EUR"},
        "vendor": {
            "id": "a4e6287f-dd8d-4234-b1c2-b08fd367623c",
            "vendorName": "Klara Hoffmann",
            "emailAddress": "hoffmannklara01@gmail.com",
        },
    },
    {
        "invoiceId": "b34e83f4-0d8b-44b4-a313-f3f1d53b4a0c",
        "invoiceNumber": "BILL/2026/08/0006",
        "transactionAmount": "225.000000",
        "status": "completed",
        "createdAt": "2026-08-12T15:42:00.000Z",
        "total": {"amount": 225, "currencyAlphaCode": "EUR"},
        "vendor": {
            "id": "9efdc906-5452-4dff-bbfc-1e9028cb0318",
            "vendorName": "LALAVANDA vl. Dajana Grgic",
        },
    },
]

LALAVANDA_TX = {
    "transactionType": "debit",
    "transactionTypeTag": "PAYMENT",
    "transactionStatus": "settled",
    "transactionPostedDate": "2026-09-03T12:43:35.511Z",
    "createdAt": "2026-09-03T12:43:02.392Z",
    "totalBaseCurrencyAmount": 325,
    "source": {"name": "EUR Account", "detail": "9330", "currencyAlphaCode": "EUR"},
    "destination": {
        "name": "LALAVANDA vl. Dajana Grgic",
        "detail": "5087",
        "currencyAlphaCode": "EUR",
    },
}

KLARA_TX = {
    "transactionType": "debit",
    "transactionTypeTag": "PAYMENT",
    "transactionStatus": "settled",
    "transactionPostedDate": "2026-09-03T12:43:16.852Z",
    "createdAt": "2026-09-03T12:43:02.546Z",
    "totalBaseCurrencyAmount": 450,
    "source": {"name": "EUR Account", "currencyAlphaCode": "EUR"},
    "destination": {"name": "Klara Hoffmann", "currencyAlphaCode": "EUR"},
}

DEPOSIT_TX = {
    "transactionType": "credit",
    "transactionTypeTag": "DEPOSIT",
    "transactionStatus": "settled",
    "transactionPostedDate": "2026-08-31T00:53:27.801Z",
    "totalBaseCurrencyAmount": 220,
    "source": {"name": "Karen Naber", "currencyAlphaCode": "EUR"},
    "destination": {"name": "EUR Account", "currencyAlphaCode": "EUR"},
}


def test_matches_debit_to_unique_bill_by_vendor_and_amount():
    invoice = match_transaction_to_invoice(LALAVANDA_TX, INVOICES)
    assert invoice["invoiceNumber"] == "BILL/2026/09/0001"
    assert invoice["invoiceId"] == "26ae6d6f-b1b5-4514-8ebb-5441d5173466"
    other = match_transaction_to_invoice(KLARA_TX, INVOICES)
    assert other["invoiceNumber"] == "PROV00352/2026/08/0001"
    assert match_transaction_to_invoice(DEPOSIT_TX, INVOICES) is None


def test_enriches_statement_line_with_odoo_bill_number():
    line = statement_line_from_mcp_transaction(LALAVANDA_TX)
    enrich_statement_line_with_invoice(line, LALAVANDA_TX, INVOICES)
    assert line["invoice_number"] == "BILL/2026/09/0001"
    assert line["jeeves_invoice_id"] == "26ae6d6f-b1b5-4514-8ebb-5441d5173466"
    assert line["payment_ref"] == "LALAVANDA vl. Dajana Grgic — BILL/2026/09/0001"
    assert line["jeeves_vendor_id"] == "9efdc906-5452-4dff-bbfc-1e9028cb0318"
    assert "invoice=BILL/2026/09/0001" in line["narration"]


def test_unwraps_billpay_envelope():
    payload = parse_mcp_http_body(
        "event: message\ndata: "
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {"count": 1, "totalCount": 1, "data": INVOICES[:1]}
                            ),
                        }
                    ]
                },
            }
        )
        + "\n\n",
        "text/event-stream",
    )
    rows, total = unwrap_mcp_invoices(payload)
    assert total == 1
    assert rows[0]["invoiceNumber"] == "BILL/2026/09/0001"


def test_client_pull_attaches_invoice_number():
    def http_request(method, url, headers, data):
        payload = data if isinstance(data, str) else data.decode()
        if '"method": "initialize"' in payload or '"method":"initialize"' in payload:
            return 200, {"Content-Type": "application/json"}, '{"jsonrpc":"2.0","id":1,"result":{}}'
        if "notifications/initialized" in payload:
            return 202, {}, ""
        body = json.loads(payload)
        name = body["params"]["name"]
        if name == "list_billpay_invoices":
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(
                                    {"count": 3, "totalCount": 3, "data": INVOICES}
                                ),
                            }
                        ]
                    },
                }
            )
        assert name == "list_transactions"
        text = (
            "total records: 1, transactions: ["
            + json.dumps(LALAVANDA_TX)
            + "]"
        )
        return 200, {"Content-Type": "application/json"}, json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"content": [{"type": "text", "text": text}]},
            }
        )

    client = JeevesMCPClient("key", account_id="acc-eur", http_request=http_request)
    lines, _extras = client.obtain_statement_lines(
        datetime(2026, 9, 3), datetime(2026, 9, 4)
    )
    assert lines[0]["invoice_number"] == "BILL/2026/09/0001"
    assert lines[0]["amount"] == -325.0


def test_bulk_template_is_not_a_bank_statement():
    fixture = Path(__file__).parent / "fixtures" / "jeeves_bulk_payments.csv"
    raw = fixture.read_bytes()
    assert detect_jeeves_bulk_payments_csv(raw)
    assert not detect_jeeves_csv(raw)
    csv_text = build_bulk_payments_csv(
        [
            {
                "vendor_name": "naturrauch",
                "account_number": "DE20642914200026823012",
                "currency": "EUR",
                "amount": 14,
                "memo": "RE4583",
                "invoice_id": "BILL/2026/09/0002",
                "invoice_date": "03/09/2026",
                "invoice_due_date": "10/09/2026",
            }
        ]
    )
    assert detect_jeeves_bulk_payments_csv(csv_text)
    assert "'DE20642914200026823012" in csv_text
    assert "BILL/2026/09/0002" in csv_text
    assert format_bulk_account_number("DE20 6429 1420 0026 8230 12") == (
        "'DE20642914200026823012"
    )
