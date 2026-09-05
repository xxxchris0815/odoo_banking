from datetime import datetime
from urllib.parse import parse_qs, urlparse

import pytest

from account_statement_import_online_paypal_reporting.lib.paypal_transactions import (
    PAYPAL_API_BASE,
    TRANSACTIONS_SCOPE,
    PayPalClient,
    PayPalConfigError,
    PayPalHTTPError,
    as_rfc3339,
    format_payer_name,
    statement_line_from_transaction,
    statement_lines_from_transaction,
    statement_lines_from_transactions,
)


def _tx(**overrides):
    info = {
        "transaction_id": "7S153172KG271445K",
        "transaction_event_code": "T0006",
        "transaction_initiation_date": "2026-08-06T11:25:53Z",
        "transaction_updated_date": "2026-08-06T11:25:53Z",
        "transaction_amount": {"currency_code": "EUR", "value": "4500.00"},
        "fee_amount": {"currency_code": "EUR", "value": "-112.40"},
        "transaction_status": "S",
        "available_balance": {"currency_code": "EUR", "value": "4387.60"},
        "ending_balance": {"currency_code": "EUR", "value": "4387.60"},
    }
    payer = {
        "email_address": "ada@example.com",
        "payer_name": {
            "given_name": "Ada",
            "surname": "Lovelace",
            "alternate_full_name": "Ada Lovelace",
        },
    }
    cart = {
        "item_details": [
            {
                "item_name": "Live ORGASMIC",
                "item_description": "Live ORGASMIC",
            }
        ]
    }
    info.update(overrides.pop("transaction_info", {}))
    payer.update(overrides.pop("payer_info", {}))
    if "payer_name" in overrides:
        payer["payer_name"] = overrides.pop("payer_name")
    cart = overrides.pop("cart_info", cart)
    tx = {
        "transaction_info": info,
        "payer_info": payer,
        "shipping_info": overrides.pop("shipping_info", {"name": "Ada Lovelace"}),
        "cart_info": cart,
    }
    tx.update(overrides)
    return tx


WITHDRAWAL = {
    "transaction_info": {
        "transaction_id": "5UM17125DF557833M",
        "transaction_event_code": "T0400",
        "transaction_initiation_date": "2026-08-07T00:10:09Z",
        "transaction_updated_date": "2026-08-07T00:10:09Z",
        "transaction_amount": {"currency_code": "EUR", "value": "-4387.60"},
        "transaction_status": "S",
        "bank_reference_id": "YYW1052196567228",
        "available_balance": {"currency_code": "EUR", "value": "0.00"},
    },
    "payer_info": {"payer_name": {}},
    "shipping_info": {},
    "cart_info": {},
}

SUBSCRIPTION = {
    "transaction_info": {
        "transaction_id": "6TY56711E9174731X",
        "transaction_event_code": "T0003",
        "transaction_initiation_date": "2026-08-29T14:04:02Z",
        "transaction_updated_date": "2026-08-29T14:04:02Z",
        "transaction_amount": {"currency_code": "EUR", "value": "-16.99"},
        "transaction_status": "S",
        "invoice_id": "038220827605781587",
        "transaction_subject": "100 GB (Google One)",
    },
    "payer_info": {
        "email_address": "paypal-gpil-pl-eur@example.com",
        "payer_name": {"alternate_full_name": "Google Payment Ireland Limited"},
    },
    "shipping_info": {"name": "Alexandra, Wennmacher"},
    "cart_info": {
        "item_details": [{"item_name": "100 GB (Google One)"}]
    },
}

FUNDING = {
    "transaction_info": {
        "transaction_id": "4KR8439568983591L",
        "paypal_reference_id": "6TY56711E9174731X",
        "transaction_event_code": "T0300",
        "transaction_initiation_date": "2026-08-29T14:04:02Z",
        "transaction_updated_date": "2026-08-29T14:04:02Z",
        "transaction_amount": {"currency_code": "EUR", "value": "16.99"},
        "transaction_status": "S",
        "invoice_id": "038220827605781587",
        "bank_reference_id": "1051335892837",
    },
    "payer_info": {"payer_name": {}},
    "shipping_info": {},
    "cart_info": {},
}

REFUND = {
    "transaction_info": {
        "transaction_id": "5MY68312XY824091W",
        "paypal_reference_id": "0XN16320MU043023Y",
        "transaction_event_code": "T1107",
        "transaction_initiation_date": "2026-09-04T19:36:57Z",
        "transaction_updated_date": "2026-09-04T19:36:57Z",
        "transaction_amount": {"currency_code": "EUR", "value": "-4500.00"},
        "fee_amount": {"currency_code": "EUR", "value": "112.40"},
        "transaction_status": "S",
        "transaction_subject": "agreed with the customer",
        "transaction_note": "agreed with the customer",
    },
    "payer_info": {
        "email_address": "eva@example.com",
        "payer_name": {
            "given_name": "Eva",
            "surname": "Muster",
            "alternate_full_name": "Eva Muster",
        },
    },
    "shipping_info": {"name": "Eva, Muster"},
    "cart_info": {"item_details": [{"item_name": "Live ORGASMIC"}]},
}


def test_rfc3339_drops_microseconds_and_uses_z():
    assert as_rfc3339(datetime(2026, 8, 6, 11, 25, 53, 123456)) == "2026-08-06T11:25:53Z"


def test_live_payload_has_no_full_name_but_still_maps_partner():
    name = format_payer_name(_tx())
    assert name == "Ada Lovelace"
    assert format_payer_name(
        _tx(payer_name={"given_name": "Sandy", "surname": "Bernau"})
    ) == "Sandy Bernau"


def test_checkout_uses_customer_and_cart_item():
    line, fee = statement_lines_from_transaction(_tx())
    assert line["amount"] == 4500.0
    assert line["unique_import_id"] == "pp:tx:7S153172KG271445K"
    assert line["partner_name"] == "Ada Lovelace"
    assert line["account_number"] == "ada@example.com"
    assert line["payment_ref"] == "[paid] Ada Lovelace — Live ORGASMIC"
    assert line["date"] == datetime(2026, 8, 6, 11, 25, 53)
    assert fee["unique_import_id"] == "pp:tx:7S153172KG271445K:fee"
    assert fee["amount"] == -112.40
    assert fee["partner_name"] == "PayPal"
    assert "fee" in fee["payment_ref"]


def test_withdrawal_has_no_false_partner():
    line = statement_line_from_transaction(WITHDRAWAL)
    assert line["amount"] == -4387.60
    assert line["partner_name"] is False
    assert line["payment_ref"] == "[paid] Withdrawal — YYW1052196567228"
    assert "bank=YYW1052196567228" in line["narration"]


def test_outgoing_payment_uses_merchant_not_shipping_name():
    line = statement_line_from_transaction(SUBSCRIPTION)
    assert line["partner_name"] == "Google Payment Ireland Limited"
    assert "Alexandra" not in (line["partner_name"] or "")
    assert line["payment_ref"] == (
        "[paid] Google Payment Ireland Limited — 100 GB (Google One)"
    )
    assert line["amount"] == -16.99


def test_account_funding_is_labelled_without_a_person():
    line = statement_line_from_transaction(FUNDING)
    assert line["partner_name"] is False
    assert line["payment_ref"].startswith("[paid] Account funding")
    assert "038220827605781587" in line["payment_ref"]
    assert line["amount"] == 16.99


def test_refund_keeps_customer_and_reverses_fee():
    line, fee = statement_lines_from_transaction(REFUND)
    assert line["amount"] == -4500.0
    assert line["partner_name"] == "Eva Muster"
    assert line["unique_import_id"] == "pp:tx:5MY68312XY824091W"
    assert "Refund" in line["payment_ref"]
    assert "Live ORGASMIC" in line["payment_ref"]
    assert fee["amount"] == 112.40
    assert fee["unique_import_id"] == "pp:tx:5MY68312XY824091W:fee"


def test_window_keeps_in_range_and_drops_future():
    future = _tx(
        transaction_info={
            "transaction_id": "FUTURE1",
            "transaction_initiation_date": "2026-10-13T00:00:00Z",
            "transaction_updated_date": "2026-10-13T00:00:00Z",
            "transaction_amount": {"currency_code": "EUR", "value": "1.00"},
            "fee_amount": None,
        }
    )
    lines = statement_lines_from_transactions(
        [_tx(), WITHDRAWAL, future],
        datetime(2026, 8, 6),
        datetime(2026, 8, 8),
    )
    ids = [line["unique_import_id"] for line in lines]
    assert "pp:tx:7S153172KG271445K" in ids
    assert "pp:tx:5UM17125DF557833M" in ids
    assert "pp:tx:FUTURE1" not in ids


def test_updated_in_window_is_kept_when_initiation_is_older():
    pending = _tx(
        transaction_info={
            "transaction_id": "HOLD1",
            "transaction_event_code": "T1105",
            "transaction_initiation_date": "2026-07-27T15:19:47Z",
            "transaction_updated_date": "2026-07-28T09:00:00Z",
            "transaction_amount": {"currency_code": "EUR", "value": "33.93"},
            "fee_amount": None,
            "transaction_status": "S",
        }
    )
    lines = statement_lines_from_transactions(
        [pending], datetime(2026, 7, 28), datetime(2026, 7, 29)
    )
    assert len(lines) == 1
    assert lines[0]["date"] == datetime(2026, 7, 28, 9, 0, 0)
    assert "Hold release" in lines[0]["payment_ref"]


def test_currency_filter_drops_other_journals():
    usd = _tx(
        transaction_info={
            "transaction_id": "USD1",
            "transaction_amount": {"currency_code": "USD", "value": "10.00"},
            "fee_amount": None,
        }
    )
    lines = statement_lines_from_transactions(
        [_tx(), usd],
        datetime(2026, 8, 6),
        datetime(2026, 8, 7),
        currency="EUR",
    )
    assert [line["unique_import_id"] for line in lines] == [
        "pp:tx:7S153172KG271445K",
        "pp:tx:7S153172KG271445K:fee",
    ]


def test_unique_ids_stay_stable_without_a_timestamp():
    first = statement_line_from_transaction(_tx())
    later = statement_line_from_transaction(
        _tx(
            transaction_info={
                "transaction_updated_date": "2026-08-07T00:00:00Z",
            }
        )
    )
    assert first["unique_import_id"] == later["unique_import_id"] == "pp:tx:7S153172KG271445K"


class _FakeHttp:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, headers, data=None):
        self.calls.append((method, url, headers, data))
        parsed = urlparse(url)
        key = (method, parsed.path)
        handler = self.routes.get(key)
        if handler is None:
            return 404, {"name": "NOT_FOUND", "message": parsed.path}
        return handler(parsed, headers, data)


def _token_ok(_parsed, _headers, _data):
    return 200, {
        "token_type": "Bearer",
        "access_token": "tok-1",
        "scope": f"openid {TRANSACTIONS_SCOPE}",
    }


def test_client_requires_credentials():
    with pytest.raises(PayPalConfigError):
        PayPalClient("", "secret")
    with pytest.raises(PayPalConfigError):
        PayPalClient("id", "")


def test_client_rejects_missing_transaction_search_scope():
    http = _FakeHttp(
        {
            ("POST", "/v1/oauth2/token"): lambda *_: (
                200,
                {"token_type": "Bearer", "access_token": "x", "scope": "openid"},
            )
        }
    )
    client = PayPalClient("id", "secret", http_request=http)
    with pytest.raises(PayPalConfigError, match="Transaction Search"):
        client.get_token()


def test_client_chunks_and_paginates():
    pages = {
        1: {
            "transaction_details": [_tx()],
            "total_pages": 2,
        },
        2: {
            "transaction_details": [WITHDRAWAL],
            "total_pages": 2,
        },
    }

    def transactions(parsed, _headers, _data):
        query = parse_qs(parsed.query)
        page = int(query["page"][0])
        start = query["start_date"][0]
        assert start.endswith("Z")
        assert "." not in start
        assert query["transaction_currency"] == ["EUR"]
        return 200, pages[page]

    http = _FakeHttp(
        {
            ("POST", "/v1/oauth2/token"): _token_ok,
            ("GET", "/v1/reporting/transactions"): transactions,
        }
    )
    client = PayPalClient("id", "secret", http_request=http, page_size=1)
    lines, extras = client.obtain_statement_lines(
        datetime(2026, 8, 6), datetime(2026, 8, 8), currency="EUR"
    )
    ids = [line["unique_import_id"] for line in lines]
    assert ids == [
        "pp:tx:7S153172KG271445K",
        "pp:tx:7S153172KG271445K:fee",
        "pp:tx:5UM17125DF557833M",
    ]
    assert extras["balance_start"] == 0.0
    assert extras["balance_end_real"] == 0.0
    tx_calls = [call for call in http.calls if call[0] == "GET"]
    assert len(tx_calls) == 2
    assert http.calls[0][1] == f"{PAYPAL_API_BASE}/v1/oauth2/token"


def test_client_splits_ranges_wider_than_31_days():
    starts = []

    def transactions(parsed, _headers, _data):
        query = parse_qs(parsed.query)
        starts.append(query["start_date"][0])
        return 200, {"transaction_details": [], "total_pages": 1}

    http = _FakeHttp(
        {
            ("POST", "/v1/oauth2/token"): _token_ok,
            ("GET", "/v1/reporting/transactions"): transactions,
        }
    )
    client = PayPalClient("id", "secret", http_request=http)
    client.list_transactions(datetime(2026, 6, 1), datetime(2026, 8, 10), currency="EUR")
    assert starts == [
        "2026-06-01T00:00:00Z",
        "2026-07-02T00:00:00Z",
        "2026-08-02T00:00:00Z",
    ]


def test_client_maps_paypal_errors():
    http = _FakeHttp(
        {
            ("POST", "/v1/oauth2/token"): lambda *_: (
                401,
                {
                    "error": "invalid_client",
                    "error_description": "Client Authentication failed",
                },
            )
        }
    )
    client = PayPalClient("id", "secret", http_request=http)
    with pytest.raises(PayPalHTTPError, match="invalid_client"):
        client.get_token()


def test_september_live_shape_nets_refund_and_fee():
    payment = _tx(
        transaction_info={
            "transaction_id": "0XN16320MU043023Y",
            "transaction_initiation_date": "2026-09-04T19:19:54Z",
            "transaction_updated_date": "2026-09-04T19:19:54Z",
            "available_balance": {"currency_code": "EUR", "value": "4387.60"},
        },
        payer_info={
            "email_address": "eva@example.com",
            "payer_name": {
                "given_name": "Eva",
                "surname": "Muster",
                "alternate_full_name": "Eva Muster",
            },
        },
    )
    lines = statement_lines_from_transactions(
        [payment, REFUND], datetime(2026, 9, 4), datetime(2026, 9, 5)
    )
    amounts = [line["amount"] for line in lines]
    assert amounts == [4500.0, -112.40, -4500.0, 112.40]
    assert {line["partner_name"] for line in lines} == {"Eva Muster", "PayPal"}
