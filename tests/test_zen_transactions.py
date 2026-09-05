from datetime import datetime

import pytest

from account_statement_import_online_zen.lib.zen_transactions import (
    HISTORY_PATH,
    PAYMENT_PATH,
    WEBHOOK_PATH_PREFIX,
    ZEN_DEFAULT_API_BASE,
    ZenClient,
    ZenConfigError,
    ZenHTTPError,
    ZenTLS,
    build_ssl_context,
    parse_webhook_events,
    payment_unique_id,
    zen_query_dates,
    public_https_base,
    requests_get_mtls,
    statement_line_from_transaction,
    statement_lines_from_transaction,
    statement_lines_from_transactions,
    unwrap_history_page,
    webhook_url,
)


SETTLED_IN = {
    "id": "552f9d42-2e30-45e3-aca8-24b04e9c7246",
    "title": "Invoice 1042",
    "createdAt": "2026-08-07T10:57:06Z",
    "bookedAt": "2026-08-07T11:02:00Z",
    "status": "SETTLED",
    "direction": "INCOMING",
    "amount": {"value": "150.50", "currency": "EUR"},
    "sender": {
        "name": "Acme GmbH",
        "accountNumber": "DE89370400440532013000",
    },
    "receiver": {
        "name": "Us",
        "accountNumber": "LT093130010187172305",
    },
    "transactionType": "PAYMENT",
}

SETTLED_OUT = {
    **SETTLED_IN,
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "direction": "OUTGOING",
    "title": "Supplier payout",
    "amount": {"value": "20.00", "currency": "EUR"},
    "receiver": {
        "name": "Vendor Ltd",
        "accountNumber": "PL10241477434169818188057910",
    },
}

PENDING = {
    **SETTLED_IN,
    "id": "pending-1",
    "status": "IN_PROGRESS",
    "bookedAt": None,
}

REJECTED = {
    **SETTLED_IN,
    "id": "rejected-1",
    "status": "REJECTED",
}


def test_incoming_line_uses_booked_date_and_positive_amount():
    line = statement_line_from_transaction(SETTLED_IN)
    assert line["amount"] == 150.50
    assert line["date"] == datetime(2026, 8, 7, 11, 2, 0)
    assert line["unique_import_id"] == payment_unique_id(SETTLED_IN["id"])
    assert line["partner_name"] == "Acme GmbH"
    assert line["account_number"] == "DE89370400440532013000"
    assert line["payment_ref"] == "Acme GmbH — Invoice 1042"
    assert "iban=DE89370400440532013000" in line["narration"]
    assert line["currency_code"] == "EUR"


def test_outgoing_line_is_negative_and_uses_receiver():
    line = statement_line_from_transaction(SETTLED_OUT)
    assert line["amount"] == -20.00
    assert line["partner_name"] == "Vendor Ltd"


def test_pending_and_rejected_are_dropped():
    lines = statement_lines_from_transactions(
        [SETTLED_IN, PENDING, REJECTED, SETTLED_OUT]
    )
    assert [line["unique_import_id"] for line in lines] == [
        payment_unique_id(SETTLED_IN["id"]),
        payment_unique_id(SETTLED_OUT["id"]),
    ]


def test_missing_id_raises():
    broken = dict(SETTLED_IN, id=None)
    with pytest.raises(ValueError, match="missing id"):
        statement_line_from_transaction(broken)


def _payloads():
    accounts = {
        "data": [
            {
                "accountId": "acc-zen-1",
                "currencyCode": "EUR",
                "accountNumbers": [{"accountNumber": "LT093130010187172305"}],
            }
        ]
    }
    page1 = {
        "data": [SETTLED_IN],
        "meta": {"hasNext": True, "lastEntryId": SETTLED_IN["id"]},
    }
    page2 = {
        "data": [SETTLED_OUT, PENDING],
        "meta": {"hasNext": False, "lastEntryId": SETTLED_OUT["id"]},
    }
    return accounts, page1, page2


def test_client_resolves_iban_and_paginates():
    accounts, page1, page2 = _payloads()
    calls = []

    def http_get(url, headers):
        calls.append(url)
        assert headers["Authorization"] == "Bearer secret-key"
        if url.rstrip("/").endswith("accounts/v1.0"):
            return 200, accounts
        if "lastEntryId=" in url:
            return 200, [page2]
        return 200, [page1]

    client = ZenClient(
        "secret-key",
        iban="LT09 3130 0101 8717 2305",
        http_get=http_get,
    )
    lines, extras = client.obtain_statement_lines(
        datetime(2026, 8, 1), datetime(2026, 8, 31)
    )
    assert extras == {}
    assert [line["amount"] for line in lines] == [150.50, -20.00]
    assert any("accountId=acc-zen-1" in url for url in calls)
    assert any("bookedAtFrom=2026-08-01" in url for url in calls)
    assert any("bookedAtTo=2026-08-31" in url for url in calls)
    assert not any("createdAtFrom=" in url for url in calls)
    assert any(f"/{HISTORY_PATH}?" in url for url in calls)
    assert "/history" not in "".join(calls)
    assert client.api_base.startswith(ZEN_DEFAULT_API_BASE)


def test_authorization_is_bearer_and_strips_pasted_prefix():
    seen = []

    def http_get(url, headers):
        seen.append(headers["Authorization"])
        return 200, {"data": [], "meta": {"hasNext": False}}

    ZenClient("Bearer already-prefixed", account_id="x", http_get=http_get).iter_history(
        datetime(2026, 1, 1), datetime(2026, 1, 2)
    )
    assert seen == ["Bearer already-prefixed"]


def test_client_uses_explicit_account_id():
    def http_get(url, headers):
        assert "accountId=explicit-id" in url
        return 200, {"data": [], "meta": {"hasNext": False}}

    client = ZenClient("k", account_id="explicit-id", http_get=http_get)
    lines, _extras = client.obtain_statement_lines(
        datetime(2026, 1, 1), datetime(2026, 1, 2)
    )
    assert lines == []


def test_client_http_error_and_missing_key():
    with pytest.raises(ZenConfigError):
        ZenClient("")

    def http_get(url, headers):
        return 403, {"error": {"code": "FORBIDDEN"}}

    client = ZenClient("k", account_id="x", http_get=http_get)
    with pytest.raises(ZenHTTPError) as error:
        client.iter_history(datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert error.value.status_code == 403


def test_iban_mismatch_raises():
    def http_get(url, headers):
        return 200, {"data": [{"accountId": "x", "accountNumbers": [{"accountNumber": "DE00"}]}]}

    client = ZenClient("k", iban="LT093130010187172305", http_get=http_get)
    with pytest.raises(ZenConfigError, match="No ZEN.COM account"):
        client.resolve_account_id()


def _ephemeral_pem_pair():
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    if not shutil.which("openssl"):
        pytest.skip("openssl is required to build a test certificate")
    with tempfile.TemporaryDirectory() as tmp:
        cert = Path(tmp) / "cert.pem"
        key = Path(tmp) / "key.pem"
        subprocess.check_call(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-keyout",
                str(key),
                "-out",
                str(cert),
                "-days",
                "1",
                "-nodes",
                "-subj",
                "/CN=zen-test",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return cert.read_text(), key.read_text()


def test_mtls_pem_loads_into_ssl_context():
    cert, key = _ephemeral_pem_pair()
    tls = ZenTLS(client_cert=cert, client_key=key)
    context, temps = build_ssl_context(tls)
    try:
        assert context is not None
    finally:
        import os

        for path in temps:
            try:
                os.unlink(path)
            except OSError:
                pass


def test_mtls_rejects_non_pem():
    with pytest.raises(ZenConfigError, match="PEM"):
        ZenTLS(client_cert="not-a-cert", client_key="not-a-key").validate()


def test_live_http_backend_requires_mtls():
    with pytest.raises(ZenConfigError, match="mTLS"):
        requests_get_mtls("https://api-services.zen.com/accounts/v1.0", {})


def test_client_forwards_tls_to_http():
    seen = {}

    def http_get(url, headers, tls=None):
        seen["tls"] = tls
        return 200, {"data": [], "meta": {"hasNext": False}}

    tls = ZenTLS(
        client_cert="-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----",
        client_key="-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----",
    )
    client = ZenClient("k", account_id="x", tls=tls, http_get=http_get)
    client.obtain_statement_lines(datetime(2026, 1, 1), datetime(2026, 1, 2))
    assert seen["tls"] is tls


LIVE_WEBHOOK = {
    "paymentId": "0956cff7-edab-7066-8254-01a06c133bb1",
    "externalId": None,
    "direction": "IN",
    "transactionStatus": "SETTLED",
    "accountId": "58d85a6c-5a3c-4bd8-8078-cf0d5f9ec2db",
}

LIVE_PAYMENT = {
    "id": "0956cff7-edab-7066-8254-01a06c133bb1",
    "title": "EXPECTMAGIC-N5N98Y",
    "relatedTransaction": None,
    "createdAt": "2026-09-04T10:59:55Z",
    "lastModifiedAt": "2026-09-04T10:59:59Z",
    "bookedAt": "2026-09-04T10:59:56Z",
    "status": "SETTLED",
    "direction": "INCOMING",
    "transactionType": "PAYMENT",
    "amount": {"value": "246.20", "currency": "EUR"},
    "senderFees": [],
    "receiverFees": [
        {
            "amount": {"value": "0.00", "currency": "EUR"},
            "name": "STANDARD_FEE",
            "type": "STANDARD",
        }
    ],
    "channel": "SEPA",
    "sender": {
        "name": "GOCARDLESS LTD",
        "country": "GB",
        "accountNumber": "FR7630004021180001015622692",
        "bic": "BNPAFRPPXXX",
    },
    "receiver": {
        "name": "EXPECT MAGIC LLC",
        "country": "US",
        "accountNumber": "LT693130010179880026",
        "bic": "BZENLT22XXX",
    },
}


def test_webhook_parses_n8n_wrapper_and_in_direction():
    events = parse_webhook_events(
        {
            "body": LIVE_WEBHOOK,
            "webhookUrl": "https://automation.example.com/webhook/zen-webhook",
            "executionMode": "production",
        }
    )
    assert events == [
        {
            "payment_id": LIVE_WEBHOOK["paymentId"],
            "account_id": LIVE_WEBHOOK["accountId"],
            "status": "SETTLED",
            "direction": "INCOMING",
            "external_id": None,
        }
    ]


def test_live_gocardless_payout_into_zen_has_no_zero_fee_line():
    lines = statement_lines_from_transaction(LIVE_PAYMENT)
    assert len(lines) == 1
    line = lines[0]
    assert line["amount"] == 246.20
    assert line["date"] == datetime(2026, 9, 4, 10, 59, 56)
    assert line["partner_name"] == "GOCARDLESS LTD"
    assert line["account_number"] == "FR7630004021180001015622692"
    assert line["payment_ref"] == "GOCARDLESS LTD — EXPECTMAGIC-N5N98Y"
    assert "[paid]" not in line["payment_ref"]
    assert "iban=FR7630004021180001015622692" in line["narration"]
    assert line["unique_import_id"] == payment_unique_id(LIVE_PAYMENT["id"])


def test_sender_iban_field_becomes_account_number_without_paid_prefix():
    tx = dict(
        SETTLED_IN,
        sender={
            "name": "ISABELL WERNER",
            "iban": "DE89 3704 0044 0532 0130 00",
        },
        title="INV/2026/00119",
    )
    line = statement_line_from_transaction(tx)
    assert line["account_number"] == "DE89370400440532013000"
    assert line["partner_name"] == "ISABELL WERNER"
    assert line["payment_ref"] == "ISABELL WERNER — INV/2026/00119"
    assert "[paid]" not in line["payment_ref"]
    assert "iban=DE89370400440532013000" in line["narration"]


def test_outgoing_fee_is_a_separate_line():
    paid = dict(
        LIVE_PAYMENT,
        id="fee-out",
        direction="OUTGOING",
        title="Supplier",
        senderFees=[
            {
                "amount": {"value": "0.42", "currency": "EUR"},
                "name": "STANDARD_FEE",
                "type": "STANDARD",
            }
        ],
        receiverFees=[],
        receiver={"name": "Vendor Ltd", "accountNumber": "PL00"},
    )
    lines = statement_lines_from_transaction(paid)
    assert [line["amount"] for line in lines] == [-246.20, -0.42]
    assert lines[1]["unique_import_id"].endswith(":fee")
    assert lines[1]["partner_name"] == "ZEN.COM"


def test_client_loads_payment_details_from_array():
    calls = []

    def http_get(url, headers):
        calls.append(url)
        assert url.endswith(f"{PAYMENT_PATH}/{LIVE_PAYMENT['id']}")
        return 200, [LIVE_PAYMENT]

    client = ZenClient("k", account_id=LIVE_WEBHOOK["accountId"], http_get=http_get)
    lines, extras = client.obtain_statement_lines_for_payment(LIVE_PAYMENT["id"])
    assert extras == {}
    assert lines[0]["amount"] == 246.20
    assert any(LIVE_PAYMENT["id"] in url for url in calls)


def test_live_history_is_wrapped_array_without_history_suffix():
    envelope = [
        {
            "data": [LIVE_PAYMENT],
            "meta": {
                "lastEntryId": LIVE_PAYMENT["id"],
                "hasNext": False,
                "direction": "DESC",
                "sortedBy": "createdAt",
            },
        }
    ]
    rows, meta = unwrap_history_page(envelope)
    assert rows[0]["id"] == LIVE_PAYMENT["id"]
    assert meta["hasNext"] is False

    calls = []

    def http_get(url, headers):
        calls.append(url)
        return 200, envelope

    client = ZenClient("k", account_id=LIVE_WEBHOOK["accountId"], http_get=http_get)
    lines, _extras = client.obtain_statement_lines(
        datetime(2026, 9, 4), datetime(2026, 9, 5)
    )
    assert lines[0]["amount"] == 246.20
    assert lines[0]["partner_name"] == "GOCARDLESS LTD"
    assert "payments/v1.0?" in calls[0]
    assert "payments/v1.0/history" not in calls[0]
    assert "bookedAtFrom=2026-09-04" in calls[0]
    assert "bookedAtTo=2026-09-05" in calls[0]


def test_zen_query_dates_make_oca_until_inclusive_and_not_future():
    start, end = zen_query_dates(
        datetime(2026, 9, 4, 0, 0), datetime(2026, 9, 5, 0, 0)
    )
    assert start == "2026-09-04"
    assert end == "2026-09-04"


def test_webhook_url_is_https_per_account():
    assert public_https_base("http://erp.example.com:8069") == "https://erp.example.com"
    url = webhook_url("http://erp.example.com:8069", "tok-zen")
    assert url == f"https://erp.example.com{WEBHOOK_PATH_PREFIX}/tok-zen"
