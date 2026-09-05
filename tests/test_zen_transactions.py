from datetime import datetime

import pytest

from account_statement_import_online_zen.lib.zen_transactions import (
    HISTORY_PATH,
    ZEN_DEFAULT_API_BASE,
    ZenClient,
    ZenConfigError,
    ZenHTTPError,
    ZenTLS,
    build_ssl_context,
    requests_get_mtls,
    statement_line_from_transaction,
    statement_lines_from_transactions,
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
    assert line["unique_import_id"] == SETTLED_IN["id"]
    assert line["partner_name"] == "Acme GmbH"
    assert line["account_number"] == "DE89370400440532013000"
    assert line["payment_ref"] == "Invoice 1042"
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
        SETTLED_IN["id"],
        SETTLED_OUT["id"],
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
        assert headers["Authorization"] == "secret-key"
        if url.rstrip("/").endswith("accounts/v1.0"):
            return 200, accounts
        if "lastEntryId=" in url:
            return 200, page2
        return 200, page1

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
    assert any(HISTORY_PATH in url for url in calls)
    assert client.api_base.startswith(ZEN_DEFAULT_API_BASE)


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
