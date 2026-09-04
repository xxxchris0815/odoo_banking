from datetime import datetime

import pytest

from account_statement_import_online_gocardless_payments.lib.gocardless_payments import (
    GC_API_BASE,
    GoCardlessConfigError,
    GoCardlessHTTPError,
    GoCardlessPaymentsClient,
    _as_iso,
    clearing_balance,
    format_customer_name,
    payment_amount,
    statement_line_from_payment,
    statement_line_from_refund,
    statement_lines_from_payout,
    verify_webhook_signature,
)


PAYMENT = {
    "id": "PM123",
    "created_at": "2026-07-01T09:00:00.000Z",
    "charge_date": "2026-07-03",
    "amount": 10000,
    "currency": "EUR",
    "status": "submitted",
    "reference": "INV-1042",
    "description": "Invoice 1042",
    "links": {"mandate": "MD1", "customer": "CU1"},
}


def _payment(**overrides):
    values = dict(PAYMENT)
    values.update(overrides)
    return values


def test_api_dates_are_utc_with_z_suffix():
    assert _as_iso(datetime(2026, 4, 2)) == "2026-04-02T00:00:00Z"
    assert _as_iso(datetime(2026, 7, 2, 15, 30, 1)) == "2026-07-02T15:30:01Z"


def test_pending_collection_is_visible_with_zero_amount():
    line = statement_line_from_payment(_payment())
    assert line["amount"] == 0.0
    assert line["unique_import_id"] == "gc:pay:PM123"
    assert line["payment_ref"].startswith("[submitted]")
    assert line["date"] == datetime(2026, 7, 3)


def test_confirmed_and_paid_out_book_the_collection():
    assert payment_amount(_payment(status="confirmed")) == 100.0
    assert payment_amount(_payment(status="paid_out")) == 100.0
    line = statement_line_from_payment(_payment(status="confirmed"))
    assert line["amount"] == 100.0
    assert "INV-1042" in line["payment_ref"]


def test_status_changes_reuse_the_same_import_id():
    submitted = statement_line_from_payment(_payment(status="submitted"))
    confirmed = statement_line_from_payment(_payment(status="confirmed"))
    failed = statement_line_from_payment(_payment(status="failed"))
    assert submitted["unique_import_id"] == confirmed["unique_import_id"] == failed["unique_import_id"]
    assert [submitted["amount"], confirmed["amount"], failed["amount"]] == [0.0, 100.0, 0.0]


def test_failed_and_charged_back_clear_the_amount():
    assert payment_amount(_payment(status="failed")) == 0.0
    assert payment_amount(_payment(status="cancelled")) == 0.0
    assert payment_amount(_payment(status="charged_back")) == 0.0
    failed = statement_line_from_payment(_payment(status="failed"))
    assert failed["payment_ref"] == "[failed] INV-1042"
    assert failed["unique_import_id"] == "gc:pay:PM123"


def test_payment_line_carries_customer_for_reconciliation():
    line = statement_line_from_payment(
        _payment(status="confirmed"),
        customer_name="Ada Lovelace (Expect Magic)",
        customer_email="ada@example.com",
        customer_company="Expect Magic",
        customer_id="CU1",
        account_number="DE89370400440532013000",
    )
    assert line["partner_name"] == "Ada Lovelace (Expect Magic)"
    assert line["partner_email"] == "ada@example.com"
    assert line["account_number"] == "DE89370400440532013000"
    assert line["payment_ref"] == "[confirmed] Ada Lovelace (Expect Magic) — INV-1042"
    assert "email=ada@example.com" in line["narration"]
    assert "customer=CU1" in line["narration"]


def test_format_customer_name_prefers_person_and_company():
    assert (
        format_customer_name(
            {"given_name": "Ada", "family_name": "Lovelace", "company_name": "Acme"}
        )
        == "Ada Lovelace (Acme)"
    )
    assert format_customer_name({"company_name": "Acme GmbH"}) == "Acme GmbH"
    assert format_customer_name({}) is None


def test_payout_and_fees_clear_confirmed_collections():
    collection = statement_line_from_payment(_payment(status="confirmed"))
    payout_lines = statement_lines_from_payout(
        {
            "id": "PO99",
            "amount": 9700,
            "deducted_fees": 300,
            "currency": "EUR",
            "status": "paid",
            "arrival_date": "2026-07-10",
            "created_at": "2026-07-09T12:00:00.000Z",
            "reference": "payout-july",
        }
    )
    assert payout_lines[0]["amount"] == -97.0
    assert payout_lines[1]["amount"] == -3.0
    assert payout_lines[0]["unique_import_id"] == "gc:payout:PO99"
    assert clearing_balance([collection, *payout_lines]) == 0.0


def test_bounced_payout_does_not_leave_the_clearing_account():
    lines = statement_lines_from_payout(
        {
            "id": "PO1",
            "amount": 5000,
            "deducted_fees": 0,
            "currency": "EUR",
            "status": "bounced",
            "created_at": "2026-07-09T12:00:00.000Z",
        }
    )
    assert lines[0]["amount"] == 0.0


def test_refund_reduces_clearing():
    line = statement_line_from_refund(
        {
            "id": "RF1",
            "amount": 2500,
            "currency": "EUR",
            "status": "paid",
            "created_at": "2026-07-11T08:00:00.000Z",
            "reference": "RF-1",
        }
    )
    assert line["amount"] == -25.0
    cancelled = statement_line_from_refund(
        {
            "id": "RF2",
            "amount": 2500,
            "currency": "EUR",
            "status": "cancelled",
            "created_at": "2026-07-11T08:00:00.000Z",
        }
    )
    assert cancelled["amount"] == 0.0


def test_webhook_signature_roundtrip():
    body = b'{"events":[]}'
    secret = "whsec_test"
    import hashlib
    import hmac

    header = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, body, header)
    assert not verify_webhook_signature(secret, body, "deadbeef")
    assert not verify_webhook_signature("", body, header)


def test_client_paginates_payments_and_builds_lines():
    pages = {
        "payments": [
            {
                "payments": [_payment(status="confirmed")],
                "meta": {"cursors": {"after": "cursor-2"}},
            },
            {
                "payments": [_payment(id="PM999", status="failed", reference="X")],
                "meta": {"cursors": {"after": None}},
            },
        ],
        "payouts": [{"payouts": [], "meta": {"cursors": {}}}],
        "refunds": [{"refunds": [], "meta": {"cursors": {}}}],
        "customers/CU1": {
            "customers": {"given_name": "Ada", "family_name": "Lovelace"}
        },
    }
    calls = []

    def http_get(url, headers):
        calls.append(url)
        assert headers["Authorization"] == "Bearer tok"
        assert headers["GoCardless-Version"] == "2015-07-06"
        if "customers/CU1" in url:
            return 200, pages["customers/CU1"]
        if url.startswith(f"{GC_API_BASE}/payments"):
            idx = 1 if "after=cursor-2" in url else 0
            return 200, pages["payments"][idx]
        if "/payouts" in url:
            return 200, pages["payouts"][0]
        if "/refunds" in url:
            return 200, pages["refunds"][0]
        return 404, "missing"

    client = GoCardlessPaymentsClient("tok", http_get=http_get, status_lookback_days=10)
    lines = client.obtain_statement_lines(datetime(2026, 7, 1), datetime(2026, 7, 31))
    assert [line["unique_import_id"] for line in lines] == ["gc:pay:PM123", "gc:pay:PM999"]
    assert lines[0]["partner_name"] == "Ada Lovelace"
    assert lines[0]["payment_ref"] == "[confirmed] Ada Lovelace — INV-1042"
    assert lines[0]["amount"] == 100.0
    assert lines[1]["amount"] == 0.0
    payment_urls = [url for url in calls if "/payments" in url]
    assert payment_urls
    assert all("customer_bank_account" not in url for url in payment_urls)
    assert any("include=customer" in url and "mandate" in url for url in payment_urls)
    assert any("created_at" in url and "gte" in url.replace("%5B", "[").replace("%5D", "]") or "created_at" in url for url in calls)


def test_customer_resolved_via_mandate_when_payment_has_no_customer_link():
    """Live GoCardless payments only link the mandate, not the customer."""
    payment = _payment(status="confirmed", links={"mandate": "MD1"})

    def http_get(url, headers):
        if url.startswith(f"{GC_API_BASE}/payments"):
            return 200, {"payments": [payment], "meta": {"cursors": {}}}
        if url.startswith(f"{GC_API_BASE}/payouts"):
            return 200, {"payouts": [], "meta": {"cursors": {}}}
        if url.startswith(f"{GC_API_BASE}/refunds"):
            return 200, {"refunds": [], "meta": {"cursors": {}}}
        if "mandates/MD1" in url:
            return 200, {
                "mandates": {
                    "id": "MD1",
                    "links": {"customer": "CU9", "customer_bank_account": "BA1"},
                }
            }
        if "customers/CU9" in url:
            return 200, {
                "customers": {
                    "given_name": "Test",
                    "family_name": "Client",
                    "company_name": "Test Client",
                    "email": "test.client@example.com",
                }
            }
        if "customer_bank_accounts/BA1" in url:
            return 200, {
                "customer_bank_accounts": {"iban": "DE89370400440532013000"}
            }
        return 404, url

    client = GoCardlessPaymentsClient("tok", http_get=http_get, status_lookback_days=1)
    lines = client.obtain_statement_lines(datetime(2026, 7, 1), datetime(2026, 7, 31))
    assert lines[0]["partner_name"] == "Test Client"
    assert lines[0]["partner_email"] == "test.client@example.com"
    assert lines[0]["account_number"] == "DE89370400440532013000"
    assert lines[0]["payment_ref"] == "[confirmed] Test Client — INV-1042"


def test_included_linked_customers_avoid_extra_gets():
    payment = _payment(status="confirmed", links={"mandate": "MD1"})
    calls = []

    def http_get(url, headers):
        calls.append(url)
        if url.startswith(f"{GC_API_BASE}/payments"):
            return 200, {
                "payments": [payment],
                "linked": {
                    "customers": [
                        {
                            "id": "CU1",
                            "given_name": "Ada",
                            "family_name": "Lovelace",
                            "email": "ada@example.com",
                        }
                    ],
                    "mandates": [{"id": "MD1", "links": {"customer": "CU1"}}],
                },
                "meta": {"cursors": {}},
            }
        if url.startswith(f"{GC_API_BASE}/payouts"):
            return 200, {"payouts": [], "meta": {"cursors": {}}}
        if url.startswith(f"{GC_API_BASE}/refunds"):
            return 200, {"refunds": [], "meta": {"cursors": {}}}
        return 404, url

    client = GoCardlessPaymentsClient("tok", http_get=http_get, status_lookback_days=1)
    lines = client.obtain_statement_lines(datetime(2026, 7, 1), datetime(2026, 7, 31))
    assert lines[0]["partner_name"] == "Ada Lovelace"
    assert lines[0]["partner_email"] == "ada@example.com"
    assert not any("/customers/" in url for url in calls)
    assert not any("/mandates/" in url for url in calls)


def test_old_collections_are_pulled_via_their_payout():
    """A payout today must still yield the Direct Debits that funded it."""
    old_payment = _payment(
        id="PMOLD",
        status="paid_out",
        created_at="2025-01-02T10:00:00.000Z",
        charge_date="2025-01-04",
        reference="INV-9",
        links={"mandate": "MD1", "customer": "CU1"},
    )
    payout = {
        "id": "PO99",
        "amount": 9700,
        "deducted_fees": 300,
        "currency": "EUR",
        "status": "paid",
        "arrival_date": "2026-09-04",
        "created_at": "2026-09-04T08:00:00.000Z",
        "reference": "today",
    }

    def http_get(url, headers):
        if "/payouts" in url:
            return 200, {"payouts": [payout], "meta": {"cursors": {}}}
        if "/refunds" in url:
            return 200, {"refunds": [], "meta": {"cursors": {}}}
        if "/payments" in url and "payout=PO99" in url:
            return 200, {"payments": [old_payment], "meta": {"cursors": {}}}
        if "/payments" in url:
            return 200, {"payments": [], "meta": {"cursors": {}}}
        if "customers/CU1" in url:
            return 200, {
                "customers": {"given_name": "Test", "family_name": "Client"}
            }
        return 404, url

    client = GoCardlessPaymentsClient("tok", http_get=http_get, status_lookback_days=10)
    lines = client.obtain_statement_lines(datetime(2026, 9, 4), datetime(2026, 9, 5))
    ids = [line["unique_import_id"] for line in lines]
    assert "gc:pay:PMOLD" in ids
    assert "gc:payout:PO99" in ids
    assert "gc:payout:PO99:fees" in ids
    collection = next(line for line in lines if line["unique_import_id"] == "gc:pay:PMOLD")
    assert collection["amount"] == 100.0
    assert collection["partner_name"] == "Test Client"
    assert "INV-9" in collection["payment_ref"]


def test_payout_webhook_also_imports_the_collections():
    payout = {
        "id": "PO1",
        "amount": 10000,
        "deducted_fees": 0,
        "currency": "EUR",
        "status": "paid",
        "arrival_date": "2026-09-04",
        "created_at": "2026-09-04T08:00:00.000Z",
    }

    def http_get(url, headers):
        if "payouts/PO1" in url:
            return 200, {"payouts": payout}
        if "/payments" in url and "payout=PO1" in url:
            return 200, {
                "payments": [_payment(status="paid_out")],
                "meta": {"cursors": {}},
            }
        if "customers/CU1" in url:
            return 200, {"customers": {"company_name": "Acme GmbH"}}
        return 404, url

    client = GoCardlessPaymentsClient("tok", http_get=http_get)
    lines = client.lines_for_event(
        {"resource_type": "payouts", "action": "paid", "links": {"payout": "PO1"}}
    )
    ids = [line["unique_import_id"] for line in lines]
    assert "gc:payout:PO1" in ids
    assert "gc:pay:PM123" in ids


def test_client_event_failed_payment():
    def http_get(url, headers):
        if "payments/PM123" in url:
            return 200, {"payments": _payment(status="failed")}
        if "customers/CU1" in url:
            return 200, {"customers": {"company_name": "Acme GmbH"}}
        return 404, "no"

    client = GoCardlessPaymentsClient("tok", http_get=http_get)
    lines = client.lines_for_event(
        {
            "resource_type": "payments",
            "action": "failed",
            "links": {"payment": "PM123"},
            "details": {"cause": "insufficient_funds", "description": "NSF"},
        }
    )
    assert lines[0]["amount"] == 0.0
    assert lines[0]["payment_ref"] == "[failed] Acme GmbH — INV-1042"
    assert "insufficient_funds" in lines[0]["narration"]
    assert lines[0]["partner_name"] == "Acme GmbH"


def test_client_errors():
    with pytest.raises(GoCardlessConfigError):
        GoCardlessPaymentsClient("")

    def http_get(url, headers):
        return 401, {"error": "unauthorized"}

    client = GoCardlessPaymentsClient("tok", http_get=http_get)
    with pytest.raises(GoCardlessHTTPError) as error:
        client.get_payment("PM1")
    assert error.value.status_code == 401
