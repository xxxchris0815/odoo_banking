from datetime import datetime
from pathlib import Path

import pytest

from account_statement_import_jeeves.lib.jeeves_csv import (
    JeevesCSVError,
    detect_jeeves_csv,
    parse_jeeves_csv,
    statement_from_rows,
)


ACTIVITY_CSV = """Transaction ID,Posted Date,Merchant,Amount,Currency,Status,Type,Memo
txn-100,2026-07-02,AWS,120.00,EUR,Completed,Purchase,Cloud
txn-101,2026-07-03,Hotel Berlin,80.50,EUR,Pending,Authorization,Hold
txn-102,2026-07-04,Acme Refund,25.00,EUR,Completed,Refund,Partial
txn-103,2026-07-05,Figma,15,EUR,Completed,Charge,Design
"""

STATEMENT_CSV = """Date;Description;Amount;Currency;Status;Id
02.07.2026;AWS;-120,00;EUR;Posted;stmt-1
03.07.2026;Pending cafe;12,00;EUR;Pending;stmt-2
"""

GENERIC_BANK_CSV = """Valuta,Buchungstext,Betrag
2026-07-01,Miete,-800.00
"""


def test_detects_jeeves_and_rejects_generic_bank_csv():
    assert detect_jeeves_csv(ACTIVITY_CSV)
    assert detect_jeeves_csv(STATEMENT_CSV)
    assert not detect_jeeves_csv(GENERIC_BANK_CSV)
    assert not detect_jeeves_csv(b"not,a,csv")


def test_activity_export_skips_pending_and_inverts_card_charges():
    lines = parse_jeeves_csv(ACTIVITY_CSV)
    by_id = {line["unique_import_id"]: line for line in lines}
    assert set(by_id) == {"txn-100", "txn-102", "txn-103"}
    assert by_id["txn-100"]["amount"] == -120.00
    assert by_id["txn-100"]["partner_name"] == "AWS"
    assert by_id["txn-100"]["date"] == datetime(2026, 7, 2)
    assert by_id["txn-102"]["amount"] == 25.00
    assert by_id["txn-103"]["amount"] == -15.00


def test_semicolon_statement_with_european_numbers():
    lines = parse_jeeves_csv(STATEMENT_CSV)
    assert len(lines) == 1
    assert lines[0]["unique_import_id"] == "stmt-1"
    assert lines[0]["amount"] == -120.00
    assert lines[0]["date"] == datetime(2026, 7, 2)


def test_parse_file_triple_single_currency():
    lines = parse_jeeves_csv(ACTIVITY_CSV)
    currency, account, statements = statement_from_rows(lines)
    assert currency == "EUR"
    assert account is False
    assert len(statements) == 1
    assert statements[0]["transactions"][0]["payment_ref"] == "AWS — Cloud"
    assert "currency_code" not in statements[0]["transactions"][0]


def test_missing_required_columns_raises():
    with pytest.raises(JeevesCSVError, match="missing columns"):
        parse_jeeves_csv("Foo,Bar\n1,2\n")


def test_fixture_file_matches_inline_activity_csv():
    fixture = Path(__file__).parent / "fixtures" / "jeeves_activity.csv"
    lines = parse_jeeves_csv(fixture.read_bytes())
    assert [line["unique_import_id"] for line in lines] == [
        "txn-100",
        "txn-102",
        "txn-103",
    ]


def test_parenthetical_amount_is_negative():
    csv_text = (
        "Transaction ID,Posted Date,Merchant,Amount,Currency,Status\n"
        "txn-9,2026-07-02,Cafe,(12.50),EUR,Completed\n"
    )
    lines = parse_jeeves_csv(csv_text, invert_card_charges=False)
    assert lines[0]["amount"] == -12.50


def test_empty_posted_rows_can_be_detected_as_jeeves_but_have_no_lines():
    csv_text = "Transaction ID,Posted Date,Merchant,Amount,Currency,Status\n"
    assert detect_jeeves_csv(csv_text)
    assert parse_jeeves_csv(csv_text) == []


def test_live_cash_usd_uses_credit_or_debit_and_unique_id():
    fixture = Path(__file__).parent / "fixtures" / "jeeves_cash_usd.csv"
    raw = fixture.read_bytes()
    assert detect_jeeves_csv(raw)
    lines = parse_jeeves_csv(raw)
    by_id = {line["unique_import_id"]: line for line in lines}
    assert len(lines) == 6
    assert all(line["amount"] < 0 for line in lines)
    withdraw = by_id["89b48681-ecab-4838-a307-8378a3b67f29"]
    assert withdraw["amount"] == -815.97
    assert withdraw["payment_ref"] == "MOBILE PMT"
    assert withdraw["partner_name"] is False
    assert withdraw["currency_code"] == "USD"
    assert withdraw["date"] == datetime(2026, 8, 27, 16, 5, 4)
    card = by_id["35f533e9-42e4-4543-a232-ef44e1574526"]
    assert card["amount"] == -19.99
    assert card["partner_name"] == "Google"
    assert card["payment_ref"] == "Google — Google One"


def test_live_cash_eur_maps_bills_deposits_and_vendor_ids():
    fixture = Path(__file__).parent / "fixtures" / "jeeves_cash_eur.csv"
    raw = fixture.read_bytes()
    assert detect_jeeves_csv(raw)
    lines = parse_jeeves_csv(raw)
    by_id = {line["unique_import_id"]: line for line in lines}
    assert len(lines) == 16
    stripe = by_id["9beba259-c557-4e35-9254-2b0549860928"]
    assert stripe["amount"] == 245.01
    assert stripe["payment_ref"] == "STRIPE"
    assert stripe["partner_name"] is False
    invoice = by_id["11e75816-65fd-4869-b2ee-6e46367d2bde"]
    assert invoice["amount"] == 1326.67
    assert invoice["payment_ref"] == "INV/2026/00036"
    bill = by_id["09f98241-cba0-4cb3-b358-71b7eebf59a9"]
    assert bill["amount"] == -737.19
    assert bill["partner_name"] == "naturrauch"
    assert bill["payment_ref"] == "naturrauch — RE4583"
    assert bill["partner_email"] == "info@naturrauch.de"
    assert bill["jeeves_vendor_id"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
    assert "vendor=55ee019e-510e-4f3c-8158-6411f9369d49" in bill["narration"]
    sophia = by_id["a74b9885-42df-4c25-9fd4-da8d72ef6684"]
    assert sophia["amount"] == -135.00
    assert sophia["payment_ref"] == "Sophia Hahn — Juli 2026"
    empty_in = by_id["bec29741-c1b1-43ed-ad5a-2525dcf84021"]
    assert empty_in["amount"] == 220.00
    assert empty_in["payment_ref"] == "Jeeves Cash Credit"
    currency, _account, statements = statement_from_rows(lines)
    assert currency == "EUR"
    assert "jeeves_vendor_id" not in statements[0]["transactions"][3]
    assert "partner_email" not in statements[0]["transactions"][3]


def test_bill_payment_without_credit_column_is_still_an_outflow():
    csv_text = (
        "Unique ID,Posted Date,Transaction Type,Amount,Currency,Status\n"
        "row-1,2026-08-20,PAYMENT,100.00,EUR,settled\n"
    )
    lines = parse_jeeves_csv(csv_text)
    assert lines[0]["amount"] == -100.00
