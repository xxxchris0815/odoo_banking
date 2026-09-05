from datetime import datetime, timezone

import pytest

from account_statement_import_online_stripe_reporting.lib.stripe_transactions import (
    STRIPE_API_BASE,
    WEBHOOK_PATH_PREFIX,
    StripeClient,
    StripeConfigError,
    StripeHTTPError,
    public_https_base,
    statement_line_from_transaction,
    statement_lines_from_transaction,
    statement_lines_from_transactions,
    verify_webhook_signature,
    webhook_url,
)


CHARGE = {
    "id": "txn_3U8M8iFYJ6FYzgBl1UxzKRsx",
    "object": "balance_transaction",
    "amount": 24900,
    "created": 1787671193,
    "currency": "eur",
    "description": "Tantric Quickies für Paare",
    "fee": 399,
    "net": 24501,
    "status": "available",
    "type": "charge",
    "source": {
        "id": "ch_3U8M8iFYJ6FYzgBl1EWCZFpk",
        "object": "charge",
        "billing_details": {"name": "Ada Lovelace", "email": None},
        "customer": {
            "id": "cus_TEST",
            "description": "Ada Lovelace",
            "email": "ada@example.com",
            "name": None,
        },
        "description": "Tantric Quickies für Paare",
        "metadata": {"Customer Email": "ada@example.com"},
        "payment_method_details": {"type": "card"},
    },
}

KLARNA = {
    "id": "txn_3TsLzdFYJ6FYzgBl0cM1OdgX",
    "amount": 110000,
    "created": 1783857776,
    "currency": "eur",
    "description": None,
    "fee": 3324,
    "net": 106676,
    "status": "available",
    "type": "payment",
    "source": {
        "id": "py_3TsLzdFYJ6FYzgBl0U60YGfE",
        "object": "charge",
        "billing_details": {"name": None, "email": "buyer@example.com"},
        "customer": None,
        "payment_method_details": {"type": "klarna"},
    },
}

PAYOUT = {
    "id": "txn_1U9DSSFYJ6FYzgBltH88n5tM",
    "amount": -24501,
    "created": 1787876148,
    "currency": "eur",
    "description": "STRIPE PAYOUT",
    "fee": 0,
    "net": -24501,
    "status": "available",
    "type": "payout",
    "source": {
        "id": "po_1U9DSSFYJ6FYzgBl7fqFVzQX",
        "object": "payout",
        "description": "STRIPE PAYOUT",
    },
}

REFUND = {
    "id": "txn_3QIaQLFYJ6FYzgBl0Z22eji6",
    "amount": -23000,
    "created": 1731099682,
    "currency": "eur",
    "description": "REFUND FOR CHARGE (Dakini Tempel für Frauen)",
    "fee": 0,
    "net": -23000,
    "status": "available",
    "type": "refund",
    "source": {"id": "re_3QIaQLFYJ6FYzgBl0ebrHYSW", "object": "refund"},
}


def test_charge_uses_billing_name_and_splits_fee():
    line, fee = statement_lines_from_transaction(CHARGE)
    assert line["amount"] == 249.0
    assert line["unique_import_id"] == "st:txn:txn_3U8M8iFYJ6FYzgBl1UxzKRsx"
    assert line["partner_name"] == "Ada Lovelace"
    assert line["account_number"] == "ada@example.com"
    assert line["payment_ref"] == "[paid] Ada Lovelace — Tantric Quickies für Paare"
    assert line["date"] == datetime(2026, 8, 25, 15, 19, 53)
    assert fee["amount"] == -3.99
    assert fee["partner_name"] == "Stripe"
    assert fee["unique_import_id"].endswith(":fee")
    assert "fee" in fee["payment_ref"]


def test_klarna_payment_uses_email_when_name_is_missing():
    line, fee = statement_lines_from_transaction(KLARNA)
    assert line["partner_name"] == "buyer@example.com"
    assert line["account_number"] == "buyer@example.com"
    assert "Klarna" in line["payment_ref"]
    assert line["amount"] == 1100.0
    assert fee["amount"] == -33.24


def test_payout_has_no_false_partner():
    line = statement_line_from_transaction(PAYOUT)
    assert line["amount"] == -245.01
    assert line["partner_name"] is False
    assert line["payment_ref"] == "[paid] Payout — po_1U9DSSFYJ6FYzgBl7fqFVzQX"


def test_refund_keeps_product_in_the_label():
    line = statement_line_from_transaction(REFUND)
    assert line["amount"] == -230.0
    assert "Refund" in line["payment_ref"]
    assert "Dakini Tempel für Frauen" in line["payment_ref"]


def test_window_and_currency_filters():
    future = dict(CHARGE, id="txn_FUTURE", created=1790000000)
    usd = dict(CHARGE, id="txn_USD", currency="usd")
    lines = statement_lines_from_transactions(
        [CHARGE, PAYOUT, future, usd],
        datetime(2026, 8, 25),
        datetime(2026, 8, 29),
        currency="EUR",
    )
    ids = [line["unique_import_id"] for line in lines]
    assert "st:txn:txn_3U8M8iFYJ6FYzgBl1UxzKRsx" in ids
    assert "st:txn:txn_1U9DSSFYJ6FYzgBltH88n5tM" in ids
    assert "st:txn:txn_FUTURE" not in ids
    assert "st:txn:txn_USD" not in ids


def test_webhook_url_is_https_per_account():
    assert public_https_base("http://erp.example.com:8069") == "https://erp.example.com"
    url_a = webhook_url("http://erp.example.com:8069", "tok-a")
    url_b = webhook_url("http://erp.example.com:8069", "tok-b")
    assert url_a == f"https://erp.example.com{WEBHOOK_PATH_PREFIX}/tok-a"
    assert url_a != url_b


def test_webhook_signature_accepts_valid_v1():
    secret = "whsec_test"
    body = b'{"id":"evt_1"}'
    timestamp = 1787876148
    import hashlib
    import hmac

    signed = b"1787876148." + body
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={sig}"
    assert verify_webhook_signature(
        secret, body, header, now=timestamp, tolerance_seconds=300
    )
    assert not verify_webhook_signature(
        secret, body, "t=1,v1=deadbeef", now=timestamp
    )


class _FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, data=None):
        self.calls.append((method, url, headers, data))
        from urllib.parse import urlparse

        parsed = urlparse(url)
        handler = self.routes.get((method, parsed.path))
        if handler is None:
            return 404, {"error": {"message": parsed.path}}
        return handler(parsed, headers, data)


def test_client_requires_key_and_maps_errors():
    with pytest.raises(StripeConfigError):
        StripeClient("")
    http = _FakeHttp(
        {
            ("GET", "/v1/balance_transactions"): lambda *_: (
                401,
                {"error": {"message": "Invalid API Key provided"}},
            )
        }
    )
    client = StripeClient("rk_test", http_request=http)
    with pytest.raises(StripeHTTPError, match="Invalid API Key"):
        client.list_transactions(datetime(2026, 8, 1), datetime(2026, 8, 2))


def test_client_paginates_and_expands_source():
    pages = [
        {"data": [CHARGE], "has_more": True},
        {"data": [PAYOUT], "has_more": False},
    ]

    def listing(parsed, _headers, _data):
        from urllib.parse import parse_qs

        query = parse_qs(parsed.query)
        assert "data.source" in query.get("expand[]", [])
        if "starting_after" in query:
            return 200, pages[1]
        return 200, pages[0]

    http = _FakeHttp(
        {
            ("GET", "/v1/balance_transactions"): listing,
            ("GET", "/v1/balance"): lambda *_: (
                200,
                {"available": [{"amount": 0, "currency": "eur"}]},
            ),
        }
    )
    client = StripeClient("rk_test", http_request=http, page_limit=1)
    lines, extras = client.obtain_statement_lines(
        datetime(2026, 8, 25), datetime(2026, 8, 29), currency="EUR"
    )
    ids = [line["unique_import_id"] for line in lines]
    assert ids[0].startswith("st:txn:")
    assert extras["balance_end_real"] == 0.0
    assert any(call[1].startswith(STRIPE_API_BASE) for call in http.calls)


def test_live_shape_charge_then_payout_nets_fee():
    lines = statement_lines_from_transactions(
        [CHARGE, PAYOUT], datetime(2026, 8, 25), datetime(2026, 8, 29)
    )
    assert [line["amount"] for line in lines] == [249.0, -3.99, -245.01]
