"""Jeeves MCP vendor list / create / update helpers (Odoo-independent)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .jeeves_mcp import unwrap_mcp_json_value

LIST_VENDORS_TOOL = "list_vendors"
CREATE_VENDOR_TOOL = "create_vendor"
UPDATE_VENDOR_TOOL = "update_vendor"
VENDOR_TOOLS = frozenset(
    {LIST_VENDORS_TOOL, CREATE_VENDOR_TOOL, UPDATE_VENDOR_TOOL}
)

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

# ISO 3166-1 alpha-2 -> alpha-3 (Jeeves bankCountryCode / countryCode).
ISO2_TO_ISO3 = {
    "AD": "AND", "AE": "ARE", "AF": "AFG", "AG": "ATG", "AI": "AIA",
    "AL": "ALB", "AM": "ARM", "AO": "AGO", "AR": "ARG", "AS": "ASM",
    "AT": "AUT", "AU": "AUS", "AW": "ABW", "AZ": "AZE", "BA": "BIH",
    "BB": "BRB", "BD": "BGD", "BE": "BEL", "BF": "BFA", "BG": "BGR",
    "BH": "BHR", "BI": "BDI", "BJ": "BEN", "BL": "BLM", "BM": "BMU",
    "BN": "BRN", "BO": "BOL", "BQ": "BES", "BR": "BRA", "BS": "BHS",
    "BT": "BTN", "BW": "BWA", "BY": "BLR", "BZ": "BLZ", "CA": "CAN",
    "CC": "CCK", "CD": "COD", "CF": "CAF", "CG": "COG", "CH": "CHE",
    "CI": "CIV", "CK": "COK", "CL": "CHL", "CM": "CMR", "CN": "CHN",
    "CO": "COL", "CR": "CRI", "CU": "CUB", "CV": "CPV", "CW": "CUW",
    "CX": "CXR", "CY": "CYP", "CZ": "CZE", "DE": "DEU", "DJ": "DJI",
    "DK": "DNK", "DM": "DMA", "DO": "DOM", "DZ": "DZA", "EC": "ECU",
    "EE": "EST", "EG": "EGY", "ER": "ERI", "ES": "ESP", "ET": "ETH",
    "FI": "FIN", "FJ": "FJI", "FK": "FLK", "FM": "FSM", "FO": "FRO",
    "FR": "FRA", "GA": "GAB", "GB": "GBR", "GD": "GRD", "GE": "GEO",
    "GF": "GUF", "GG": "GGY", "GH": "GHA", "GI": "GIB", "GL": "GRL",
    "GM": "GMB", "GN": "GIN", "GP": "GLP", "GQ": "GNQ", "GR": "GRC",
    "GT": "GTM", "GU": "GUM", "GW": "GNB", "GY": "GUY", "HK": "HKG",
    "HN": "HND", "HR": "HRV", "HT": "HTI", "HU": "HUN", "ID": "IDN",
    "IE": "IRL", "IL": "ISR", "IM": "IMN", "IN": "IND", "IQ": "IRQ",
    "IR": "IRN", "IS": "ISL", "IT": "ITA", "JE": "JEY", "JM": "JAM",
    "JO": "JOR", "JP": "JPN", "KE": "KEN", "KG": "KGZ", "KH": "KHM",
    "KI": "KIR", "KM": "COM", "KN": "KNA", "KP": "PRK", "KR": "KOR",
    "KW": "KWT", "KY": "CYM", "KZ": "KAZ", "LA": "LAO", "LB": "LBN",
    "LC": "LCA", "LI": "LIE", "LK": "LKA", "LR": "LBR", "LS": "LSO",
    "LT": "LTU", "LU": "LUX", "LV": "LVA", "LY": "LBY", "MA": "MAR",
    "MC": "MCO", "MD": "MDA", "ME": "MNE", "MF": "MAF", "MG": "MDG",
    "MH": "MHL", "MK": "MKD", "ML": "MLI", "MM": "MMR", "MN": "MNG",
    "MO": "MAC", "MP": "MNP", "MQ": "MTQ", "MR": "MRT", "MS": "MSR",
    "MT": "MLT", "MU": "MUS", "MV": "MDV", "MW": "MWI", "MX": "MEX",
    "MY": "MYS", "MZ": "MOZ", "NA": "NAM", "NC": "NCL", "NE": "NER",
    "NG": "NGA", "NI": "NIC", "NL": "NLD", "NO": "NOR", "NP": "NPL",
    "NR": "NRU", "NU": "NIU", "NZ": "NZL", "OM": "OMN", "PA": "PAN",
    "PE": "PER", "PF": "PYF", "PG": "PNG", "PH": "PHL", "PK": "PAK",
    "PL": "POL", "PM": "SPM", "PR": "PRI", "PS": "PSE", "PT": "PRT",
    "PW": "PLW", "PY": "PRY", "QA": "QAT", "RE": "REU", "RO": "ROU",
    "RS": "SRB", "RU": "RUS", "RW": "RWA", "SA": "SAU", "SB": "SLB",
    "SC": "SYC", "SD": "SDN", "SE": "SWE", "SG": "SGP", "SH": "SHN",
    "SI": "SVN", "SJ": "SJM", "SK": "SVK", "SL": "SLE", "SM": "SMR",
    "SN": "SEN", "SO": "SOM", "SR": "SUR", "SS": "SSD", "ST": "STP",
    "SV": "SLV", "SX": "SXM", "SY": "SYR", "SZ": "SWZ", "TC": "TCA",
    "TD": "TCD", "TG": "TGO", "TH": "THA", "TJ": "TJK", "TK": "TKL",
    "TL": "TLS", "TM": "TKM", "TN": "TUN", "TO": "TON", "TR": "TUR",
    "TT": "TTO", "TV": "TUV", "TW": "TWN", "TZ": "TZA", "UA": "UKR",
    "UG": "UGA", "US": "USA", "UY": "URY", "UZ": "UZB", "VA": "VAT",
    "VC": "VCT", "VE": "VEN", "VG": "VGB", "VI": "VIR", "VN": "VNM",
    "VU": "VUT", "WF": "WLF", "WS": "WSM", "XK": "XKX", "YE": "YEM",
    "YT": "MYT", "ZA": "ZAF", "ZM": "ZMB", "ZW": "ZWE",
}

CALLING_CODES = {
    "AT": "43", "AU": "61", "BE": "32", "BR": "55", "CA": "1", "CH": "41",
    "CZ": "420", "DE": "49", "DK": "45", "ES": "34", "FI": "358", "FR": "33",
    "GB": "44", "GR": "30", "HR": "385", "HU": "36", "IE": "353", "IN": "91",
    "IT": "39", "LU": "352", "MX": "52", "NL": "31", "NO": "47", "PL": "48",
    "PT": "351", "RO": "40", "SE": "46", "US": "1",
}

PAYMENT_METHODS = (
    ("SEPA", "SEPA"),
    ("SEPA Instant/SEPA", "SEPA Instant/SEPA"),
    ("SWIFT", "SWIFT"),
    ("ACH", "ACH"),
    ("FEDWIRE", "FEDWIRE"),
    ("FASTER_PAYMENTS", "Faster Payments"),
    ("BANK_TRANSFER", "Bank transfer"),
)


class JeevesVendorError(ValueError):
    """Vendor payload is incomplete or Jeeves rejected it."""


@dataclass
class JeevesVendorDraft:
    entity_type: str = "COMPANY"
    company_name: str = ""
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    postcode: str = ""
    country_iso3: str = ""
    bank_country_iso3: str = ""
    currency: str = "EUR"
    payment_method: str = "SEPA"
    iban: str = ""
    account_number: str = ""
    account_name: str = ""
    swift: str = ""
    bank_name: str = ""
    vendor_id: str = ""
    net_terms: int | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        if self.entity_type == "PERSONAL":
            return " ".join(
                part for part in (self.first_name, self.last_name) if part
            ).strip()
        return (self.company_name or "").strip()


def iso3_from_country_code(code: str | None) -> str:
    text = (code or "").strip().upper()
    if not text:
        raise JeevesVendorError("Country is required for a Jeeves vendor")
    if len(text) == 3:
        return text
    mapped = ISO2_TO_ISO3.get(text)
    if not mapped:
        raise JeevesVendorError(f"No ISO3 country mapping for {text}")
    return mapped


def default_payment_method(currency: str, bank_iso3: str) -> str:
    currency = (currency or "").strip().upper()
    bank = (bank_iso3 or "").strip().upper()
    if currency == "EUR":
        return "SEPA"
    if currency == "GBP" and bank == "GBR":
        return "FASTER_PAYMENTS"
    if currency == "USD" and bank == "USA":
        return "ACH"
    if bank == "USA":
        return "FEDWIRE"
    return "SWIFT"


def format_jeeves_phone(raw: str | None, iso2: str | None = "DE") -> str:
    """Jeeves wants ``+CC number`` (space after the calling code)."""
    text = (raw or "").strip()
    if not text:
        raise JeevesVendorError("Phone is required for a Jeeves vendor")
    country = (iso2 or "DE").strip().upper()
    if len(country) == 3:
        reverse = {iso3: iso2 for iso2, iso3 in ISO2_TO_ISO3.items()}
        country = reverse.get(country, "DE")
    digits = re.sub(r"\D", "", text)
    if digits.startswith("00"):
        digits = digits[2:]
    calling = CALLING_CODES.get(country, "49")
    if text.startswith("+"):
        rest = digits
        if rest.startswith(calling):
            rest = rest[len(calling) :]
        elif len(calling) == 1 and rest.startswith(calling):
            rest = rest[1:]
    elif digits.startswith(calling):
        rest = digits[len(calling) :]
    elif digits.startswith("0"):
        rest = digits.lstrip("0")
    else:
        rest = digits
    if not rest:
        raise JeevesVendorError("Phone is required for a Jeeves vendor")
    formatted = f"+{calling} {rest}"
    if not re.match(r"^\+\d{1,4} [0-9()\s-]+$", formatted):
        raise JeevesVendorError(f"Phone {text!r} is not a Jeeves +CC number")
    return formatted


def sanitize_iban(value: str | None) -> str:
    return re.sub(r"\s+", "", value or "").upper()


def split_personal_name(name: str | None) -> tuple[str, str]:
    parts = [part for part in (name or "").strip().split() if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return parts[0], " ".join(parts[1:])


def partner_phone(record: Any) -> str:
    """Read phone, then mobile, but only if the field exists (Odoo 19)."""
    fields = getattr(record, "_fields", None)
    for name in ("phone", "mobile"):
        if fields is not None and name not in fields:
            continue
        try:
            value = record[name] if fields is not None else getattr(record, name, None)
        except (AttributeError, KeyError):
            continue
        if value not in (None, False, ""):
            return str(value).strip()
    return ""


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_dicts(item)


def unwrap_mcp_vendors(payload: Any) -> tuple[list[dict[str, Any]], int]:
    parsed = unwrap_mcp_json_value(payload)
    if isinstance(parsed, list):
        rows = [row for row in parsed if isinstance(row, dict)]
        return rows, len(rows)
    if isinstance(parsed, dict):
        data = parsed.get("data") or parsed.get("vendors")
        if isinstance(data, list):
            rows = [row for row in data if isinstance(row, dict)]
            total = parsed.get("totalCount")
            if total is None:
                total = parsed.get("count")
            return rows, int(total if total is not None else len(rows))
    return [], 0


def extract_vendor_cache_id(payload: Any) -> str:
    parsed = unwrap_mcp_json_value(payload)
    for row in _walk_dicts(parsed):
        cache = row.get("vendorCacheId")
        if cache:
            return str(cache).strip()
    raise JeevesVendorError("Jeeves create_vendor did not return vendorCacheId")


def extract_created_vendor_id(payload: Any) -> str:
    parsed = unwrap_mcp_json_value(payload)
    for key in ("vendorId", "id"):
        for row in _walk_dicts(parsed):
            if "vendorCacheId" in row and key == "id":
                continue
            value = row.get(key)
            if value and UUID_RE.match(str(value).strip()):
                return str(value).strip()
    raise JeevesVendorError("Jeeves create_vendor did not return a vendor id")


def match_vendor(
    vendors: list[dict[str, Any]],
    *,
    vendor_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
) -> dict[str, Any] | None:
    if vendor_id:
        hits = [
            row
            for row in vendors
            if str(row.get("id") or "").strip() == vendor_id.strip()
        ]
        if len(hits) == 1:
            return hits[0]
    if email and "@" in email:
        needle = email.strip().casefold()
        hits = [
            row
            for row in vendors
            if str(row.get("emailAddress") or "").strip().casefold() == needle
        ]
        if len(hits) == 1:
            return hits[0]
    if name:
        needle = name.strip().casefold()
        hits = [
            row
            for row in vendors
            if str(row.get("vendorName") or row.get("companyName") or "")
            .strip()
            .casefold()
            == needle
        ]
        if len(hits) == 1:
            return hits[0]
    return None


def _payment_information(draft: JeevesVendorDraft) -> dict[str, str]:
    info: dict[str, str] = {
        "paymentMethod": (draft.payment_method or "SEPA").strip(),
    }
    account_name = (draft.account_name or draft.display_name).strip()
    if account_name:
        info["accountName"] = account_name
    iban = sanitize_iban(draft.iban)
    if iban:
        info["iban"] = iban
    if draft.account_number and not iban:
        info["accountNumber"] = draft.account_number.strip()
    if draft.swift:
        info["swiftCode"] = draft.swift.strip()
    if draft.bank_name:
        info["bankName"] = draft.bank_name.strip()
    return info


def validate_draft(draft: JeevesVendorDraft, *, for_update: bool = False) -> None:
    if for_update and not draft.vendor_id:
        raise JeevesVendorError("Jeeves vendor id is required to update")
    if draft.entity_type not in {"COMPANY", "PERSONAL"}:
        raise JeevesVendorError("Jeeves vendor type must be COMPANY or PERSONAL")
    if draft.entity_type == "COMPANY" and not draft.company_name.strip():
        raise JeevesVendorError("Company name is required")
    if draft.entity_type == "PERSONAL" and not (
        draft.first_name.strip() and draft.last_name.strip()
    ):
        raise JeevesVendorError("First and last name are required")
    if not draft.email or "@" not in draft.email:
        raise JeevesVendorError("E-mail is required for a Jeeves vendor")
    format_jeeves_phone(draft.phone, draft.country_iso3 or draft.bank_country_iso3)
    if not draft.street.strip() or not draft.city.strip() or not draft.postcode.strip():
        raise JeevesVendorError("Street, city and ZIP are required")
    iso3_from_country_code(draft.country_iso3)
    iso3_from_country_code(draft.bank_country_iso3)
    if not sanitize_iban(draft.iban) and not (draft.account_number or "").strip():
        raise JeevesVendorError("IBAN or account number is required")
    if not (draft.currency or "").strip():
        raise JeevesVendorError("Account currency is required")


def build_create_initial_arguments(draft: JeevesVendorDraft) -> dict[str, Any]:
    validate_draft(draft)
    args: dict[str, Any] = {
        "step": "ADD_INITIAL_DETAILS",
        "bankCountryCode": iso3_from_country_code(draft.bank_country_iso3),
        "accountCurrency": draft.currency.strip().upper(),
        "type": draft.entity_type,
    }
    if draft.entity_type == "COMPANY":
        args["companyName"] = draft.company_name.strip()
    else:
        args["firstName"] = draft.first_name.strip()
        args["lastName"] = draft.last_name.strip()
    if draft.net_terms is not None:
        args["netTerms"] = int(draft.net_terms)
    return args


def build_create_payment_arguments(
    draft: JeevesVendorDraft, vendor_cache_id: str
) -> dict[str, Any]:
    if not vendor_cache_id:
        raise JeevesVendorError("vendorCacheId is missing")
    return {
        "step": "ADD_PAYMENT_INFORMATION",
        "vendorCacheId": vendor_cache_id,
        "paymentInformation": _payment_information(draft),
    }


def build_create_contact_arguments(
    draft: JeevesVendorDraft, vendor_cache_id: str
) -> dict[str, Any]:
    if not vendor_cache_id:
        raise JeevesVendorError("vendorCacheId is missing")
    return {
        "step": "ADD_CONTACT_INFORMATION",
        "vendorCacheId": vendor_cache_id,
        "countryCode": iso3_from_country_code(draft.country_iso3),
        "postcode": draft.postcode.strip(),
        "streetAddress": draft.street.strip(),
        "city": draft.city.strip(),
        "state": (draft.state or "n/a").strip() or "n/a",
        "phoneNumber": format_jeeves_phone(
            draft.phone, draft.country_iso3 or draft.bank_country_iso3
        ),
        "emailAddress": draft.email.strip(),
    }


def build_update_arguments(draft: JeevesVendorDraft) -> dict[str, Any]:
    validate_draft(draft, for_update=True)
    args: dict[str, Any] = {
        "vendorId": draft.vendor_id.strip(),
        "accountCurrency": draft.currency.strip().upper(),
        "bankCountryCode": iso3_from_country_code(draft.bank_country_iso3),
        "entityType": draft.entity_type,
    }
    if draft.entity_type == "COMPANY":
        args["companyName"] = draft.company_name.strip()
    else:
        args["firstName"] = draft.first_name.strip()
        args["lastName"] = draft.last_name.strip()
    args["emailAddress"] = draft.email.strip()
    args["phoneNumber"] = format_jeeves_phone(
        draft.phone, draft.country_iso3 or draft.bank_country_iso3
    )
    args["streetAddress"] = draft.street.strip()
    args["city"] = draft.city.strip()
    args["state"] = (draft.state or "n/a").strip() or "n/a"
    args["postcode"] = draft.postcode.strip()
    args["countryCode"] = iso3_from_country_code(draft.country_iso3)
    args["paymentInformation"] = _payment_information(draft)
    return args
