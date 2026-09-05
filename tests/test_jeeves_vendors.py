import json

from account_statement_import_jeeves.lib.jeeves_mcp import (
    JeevesMCPClient,
    JeevesMCPConfigError,
    parse_mcp_http_body,
    unwrap_mcp_json_value,
)
from account_statement_import_jeeves.lib.jeeves_vendors import (
    JeevesVendorDraft,
    JeevesVendorError,
    build_create_contact_arguments,
    build_create_initial_arguments,
    build_create_payment_arguments,
    build_update_arguments,
    default_payment_method,
    extract_created_vendor_id,
    contact_from_vendor,
    extract_vendor_cache_id,
    format_jeeves_phone,
    iban_country_iso2,
    iso2_from_country_code,
    iso3_from_country_code,
    missing_jeeves_requirements,
    parse_partner_email,
    partner_phone,
    match_vendor,
    account_last4,
    sanitize_iban,
    usable_account_number,
    split_personal_name,
    unwrap_mcp_vendors,
)

VENDORS_TEXT = json.dumps(
    {
        "count": 1,
        "totalCount": 1,
        "data": [
            {
                "id": "55ee019e-510e-4f3c-8158-6411f9369d49",
                "vendorName": "naturrauch",
                "companyName": "naturrauch",
                "emailAddress": "info@naturrauch.de",
                "entityType": "COMPANY",
                "vendorPaymentDetails": [
                    {
                        "accountNumber": "****3012",
                        "bankCountryCode": "DEU",
                        "bankCurrencyCode": "EUR",
                        "paymentMethod": "SEPA",
                    }
                ],
                "status": "active",
            }
        ],
    }
)

CREATE_STEP1 = json.dumps(
    {
        "message": "Initial details added successfully.",
        "currentStep": "ADD_INITIAL_DETAILS",
        "nextStep": "ADD_PAYMENT_INFORMATION",
        "result": {"vendorCacheId": "d81f2b5c-ac41-4ea3-9e18-ebc0da6d1db8"},
    }
)

CREATE_STEP3 = json.dumps(
    {
        "message": "Vendor created",
        "result": {"vendorId": "55ee019e-510e-4f3c-8158-6411f9369d49"},
    }
)


def _sse(text: str) -> str:
    inner = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    )
    return f"event: message\ndata: {inner}\n\n"


def _draft(**overrides) -> JeevesVendorDraft:
    values = dict(
        entity_type="COMPANY",
        company_name="naturrauch",
        email="info@naturrauch.de",
        phone="+49 15146575973",
        street="Hauptstrasse 1",
        city="Berlin",
        state="n/a",
        postcode="10115",
        country_iso3="DEU",
        bank_country_iso3="DEU",
        currency="EUR",
        payment_method="SEPA",
        iban="DE89370400440532013000",
        account_name="naturrauch",
    )
    values.update(overrides)
    return JeevesVendorDraft(**values)


def test_unwraps_list_vendors_envelope():
    parsed = parse_mcp_http_body(_sse(VENDORS_TEXT), "text/event-stream")
    rows, total = unwrap_mcp_vendors(parsed)
    assert total == 1
    assert rows[0]["id"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
    assert match_vendor(rows, email="info@naturrauch.de")["vendorName"] == "naturrauch"
    assert match_vendor(rows, name="naturrauch")
    assert match_vendor(rows, email="missing@example.com") is None


def test_country_phone_iban_helpers():
    assert iso3_from_country_code("DE") == "DEU"
    assert iso3_from_country_code("hrv") == "HRV"
    assert iso2_from_country_code("LTU") == "LT"
    assert iban_country_iso2("LT20 3130 0101 7615 8339") == "LT"
    assert iban_country_iso2("DE20642914200026823012") == "DE"
    assert parse_partner_email(
        "Wachstumsakademie Maximus <info@wachstumsakademie.de>"
    ) == "info@wachstumsakademie.de"
    assert parse_partner_email("info@naturrauch.de") == "info@naturrauch.de"
    assert parse_partner_email("kein mail") == ""
    assert default_payment_method("EUR", "DEU") == "SEPA"
    assert default_payment_method("USD", "USA") == "ACH"
    assert format_jeeves_phone("+49 15146575973", "DE") == "+49 15146575973"
    assert format_jeeves_phone("015146575973", "DE") == "+49 15146575973"
    assert format_jeeves_phone("15146575973", "DE") == "+49 15146575973"
    assert sanitize_iban("de89 3704 0044 0532 0130 00") == "DE89370400440532013000"
    assert split_personal_name("Klara Hoffmann") == ("Klara", "Hoffmann")


def test_lists_all_missing_jeeves_fields():
    missing = missing_jeeves_requirements(
        JeevesVendorDraft(entity_type="COMPANY", company_name="X")
    )
    assert "e-mail" in missing
    assert "phone (+CC number)" in missing
    assert "street" in missing
    assert "ZIP" in missing
    assert "city" in missing
    assert "bank country" in missing


def test_reads_bank_country_from_list_vendors():
    contact = contact_from_vendor(
        {
            "id": "98801079-03b5-4359-bc86-b1607ea94836",
            "emailAddress": "Rapid Scaling <info@wachstumsakademie.de>",
            "phoneNumber": "+49 15146575973",
            "streetAddress": "Hauptstrasse 1",
            "city": "Berlin",
            "postcode": "10115",
            "countryCode": "DEU",
            "vendorPaymentDetails": [
                {"bankCountryCode": "LTU", "bankCurrencyCode": "EUR", "paymentMethod": "SEPA"}
            ],
        }
    )
    assert contact["bank_iso3"] == "LTU"
    assert contact["email"] == "info@wachstumsakademie.de"
    assert contact["street"] == "Hauptstrasse 1"
    assert usable_account_number("****3012") == ""
    assert usable_account_number("LT203130010176158339") == "LT203130010176158339"
    assert contact_from_vendor(
        {"vendorPaymentDetails": [{"accountNumber": "****3012", "iban": ""}]}
    )["iban"] == ""


def test_create_and_update_payloads():
    draft = _draft()
    initial = build_create_initial_arguments(draft)
    assert initial["step"] == "ADD_INITIAL_DETAILS"
    assert initial["type"] == "COMPANY"
    assert initial["bankCountryCode"] == "DEU"
    payment = build_create_payment_arguments(draft, "cache-1")
    assert payment["paymentInformation"]["paymentMethod"] == "SEPA"
    assert payment["paymentInformation"]["iban"].startswith("DE89")
    contact = build_create_contact_arguments(draft, "cache-1")
    assert contact["phoneNumber"] == "+49 15146575973"
    assert contact["countryCode"] == "DEU"
    update = build_update_arguments(_draft(vendor_id="55ee019e-510e-4f3c-8158-6411f9369d49"))
    assert update["vendorId"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
    assert update["entityType"] == "COMPANY"


def test_rejects_incomplete_draft():
    try:
        build_create_initial_arguments(_draft(email=""))
    except JeevesVendorError as error:
        assert "e-mail" in str(error)
    else:
        raise AssertionError("expected missing email to fail")


def test_extracts_cache_and_vendor_ids():
    step1 = parse_mcp_http_body(_sse(CREATE_STEP1), "text/event-stream")
    assert extract_vendor_cache_id(step1) == "d81f2b5c-ac41-4ea3-9e18-ebc0da6d1db8"
    step3 = parse_mcp_http_body(_sse(CREATE_STEP3), "text/event-stream")
    assert extract_created_vendor_id(step3) == "55ee019e-510e-4f3c-8158-6411f9369d49"


def test_client_lists_creates_and_updates_vendors():
    calls = []

    def http_request(method, url, headers, data):
        payload = data if isinstance(data, str) else data.decode()
        calls.append(payload)
        if '"method": "initialize"' in payload or '"method":"initialize"' in payload:
            return 200, {"Content-Type": "application/json"}, '{"jsonrpc":"2.0","id":1,"result":{}}'
        if "notifications/initialized" in payload:
            return 202, {}, ""
        body = json.loads(payload)
        name = body["params"]["name"]
        args = body["params"]["arguments"]
        if name == "list_vendors":
            assert args["page"] == 1
            return 200, {"Content-Type": "text/event-stream"}, _sse(VENDORS_TEXT)
        if name == "create_vendor":
            step = args["step"]
            if step == "ADD_INITIAL_DETAILS":
                assert args["companyName"] == "naturrauch"
                return 200, {"Content-Type": "text/event-stream"}, _sse(CREATE_STEP1)
            if step == "ADD_PAYMENT_INFORMATION":
                assert args["vendorCacheId"] == "d81f2b5c-ac41-4ea3-9e18-ebc0da6d1db8"
                return 200, {"Content-Type": "text/event-stream"}, _sse(CREATE_STEP1)
            assert step == "ADD_CONTACT_INFORMATION"
            assert args["emailAddress"] == "info@naturrauch.de"
            return 200, {"Content-Type": "text/event-stream"}, _sse(CREATE_STEP3)
        if name == "update_vendor":
            assert args["vendorId"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
            return 200, {"Content-Type": "text/event-stream"}, _sse(
                json.dumps({"message": "updated"})
            )
        raise AssertionError(name)

    client = JeevesMCPClient("key", http_request=http_request)
    rows = client.list_vendors("naturrauch")
    assert rows[0]["emailAddress"] == "info@naturrauch.de"
    created = client.create_vendor(_draft())
    assert created["id"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
    updated = client.update_vendor(
        _draft(vendor_id="55ee019e-510e-4f3c-8158-6411f9369d49")
    )
    assert updated["id"] == "55ee019e-510e-4f3c-8158-6411f9369d49"
    assert any("list_vendors" in payload for payload in calls)
    assert sum("create_vendor" in payload for payload in calls) == 3
    assert any("update_vendor" in payload for payload in calls)


class _FakePartner:
    def __init__(self, fields, **values):
        self._fields = fields
        self._values = values

    def __getitem__(self, name):
        if name not in self._fields:
            raise AttributeError(name)
        return self._values.get(name)


def test_partner_phone_skips_missing_mobile_field():
    odoo19 = _FakePartner({"phone": True, "name": True}, phone="+49 151 000")
    assert partner_phone(odoo19) == "+49 151 000"
    only_mobile = _FakePartner({"phone": True, "mobile": True}, mobile="+49 160 111")
    assert partner_phone(only_mobile) == "+49 160 111"
    empty = _FakePartner({"phone": True}, phone=False)
    assert partner_phone(empty) == ""


LIVE_LIST_VENDORS = [
    {
        "content": [
            {
                "type": "text",
                "text": {
                    "count": 1,
                    "data": [
                        {
                            "id": "98801079-03b5-4359-bc86-b1607ea94836",
                            "vendorName": "Rapid Scaling Company LLC",
                            "companyName": "Rapid Scaling Company LLC",
                            "emailAddress": "info@wachstumsakademie-maximus.de",
                            "entityType": "COMPANY",
                            "vendorPaymentDetails": [
                                {
                                    "accountNumber": "****8339",
                                    "bankCountryCode": "LTU",
                                    "bankCurrencyCode": "EUR",
                                    "paymentMethod": "SEPA",
                                }
                            ],
                            "status": "active",
                        }
                    ],
                    "totalCount": 63,
                },
            }
        ]
    }
]


def test_unwraps_live_list_vendors_object_text():
    rows, total = unwrap_mcp_vendors(LIVE_LIST_VENDORS)
    assert len(rows) == 1
    assert rows[0]["id"] == "98801079-03b5-4359-bc86-b1607ea94836"
    assert total == 63
    parsed = unwrap_mcp_json_value(LIVE_LIST_VENDORS)
    assert parsed["count"] == 1
    contact = contact_from_vendor(rows[0])
    assert contact["email"] == "info@wachstumsakademie-maximus.de"
    assert contact["bank_iso3"] == "LTU"
    assert contact["phone"] == ""
    assert contact["street"] == ""
    assert contact["iban"] == ""
    assert contact["account_last4"] == "8339"
    assert account_last4("LT203130010176158339") == "8339"


def test_find_vendor_uses_list_vendors_not_get_vendor():
    calls = []

    def http_request(method, url, headers, data):
        payload = data if isinstance(data, str) else data.decode()
        calls.append(payload)
        if '"method": "initialize"' in payload or '"method":"initialize"' in payload:
            return 200, {"Content-Type": "application/json"}, '{"jsonrpc":"2.0","id":1,"result":{}}'
        if "notifications/initialized" in payload:
            return 202, {}, ""
        body = json.loads(payload)
        name = body["params"]["name"]
        if name == "list_vendors":
            assert "selectedFields" not in body["params"]["arguments"]
            return 200, {"Content-Type": "application/json"}, json.dumps(
                {"jsonrpc": "2.0", "id": 2, "result": LIVE_LIST_VENDORS[0]}
            )
        raise AssertionError(name)

    client = JeevesMCPClient("key", http_request=http_request)
    found = client.find_vendor(vendor_id="98801079-03b5-4359-bc86-b1607ea94836")
    assert found["emailAddress"] == "info@wachstumsakademie-maximus.de"
    assert found["vendorPaymentDetails"][0]["bankCountryCode"] == "LTU"
    assert all("get_vendor" not in payload for payload in calls)


def test_still_refuses_card_and_payment_tools():
    client = JeevesMCPClient("key", http_request=lambda *a, **k: (200, {}, "{}"))
    try:
        client.call_tool("create_card", {"step": "CAPTURE_THE_DETAILS"})
    except JeevesMCPConfigError as error:
        assert "write tool" in str(error)
    else:
        raise AssertionError("create_card must stay blocked")
