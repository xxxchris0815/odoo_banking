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
    assert statements[0]["transactions"][0]["payment_ref"] == "AWS"
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
