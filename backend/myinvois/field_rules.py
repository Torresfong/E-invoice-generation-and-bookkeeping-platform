"""Whether one company's document has what LHDN requires (ticket #122, D14, D59, D60).

The check is handed the values for one company, one customer, one item or one line. It does not query, so it cannot
read another company's rows. Each problem names the field in words the owner can act on and never repeats the value,
so a TIN or a registration number cannot leak into a message, a log or an audit entry.

The rules are the invoice v1.1 field table (https://sdk.myinvois.hasil.gov.my/documents/invoice-v1-1/), the supplier
and buyer validation in the SDK FAQ (https://sdk.myinvois.hasil.gov.my/faq/) and IRBM's e-Invoice Guideline Appendix 1,
all read on 2026-11-11. `address_line1` is LHDN's Address Line 0, the only mandatory address line; `address_line2` and
`address_line3` are the two optional lines. The company's e-invoice setting stores two of them.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from django.utils.translation import gettext as _

from core.ports.interfaces import ID_TYPES

from .code_lists import classification_codes, country_codes, currency_codes, msic_codes, state_codes, tax_types, unit_codes

# Maxima from the v1.1 field table. The TIN example on that page is 11 characters, so 14 is a maximum, not an exact
# length. The same reading applies to the registration-number lengths, which the table gives as VARCHAR(n).
TIN_MAX = 14
BRN_MAX = 20
ID_MAX = {"NRIC": 12, "PASSPORT": 12, "BRN": 20, "ARMY": 12}
NAME_MAX = 300
ADDRESS_MAX = 150
CITY_MAX = 50
STATE_MAX = 50
POSTCODE_MAX = 50
PHONE_MIN, PHONE_MAX = 8, 20
SST_MAX = 35
TTX_MAX = 17
INVOICE_NUMBER_MAX = 50
EXEMPTION_MAX = 300
MALAYSIA = "MYS"
EXEMPT = "E"

# FAQ: letters and digits, a dash, and for SST one semicolon between two numbers. "NA" is accepted before this.
_SST = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:;[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)?")
_TTX = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*")
_PHONE = re.compile(r"\+?[0-9]+")


@dataclass(frozen=True)
class FieldError:
    """One field the owner has to correct. `field` is the input's name; `message` is the words the owner sees."""

    field: str
    message: str


def check_einvoice_setting(details):
    """The supplier fields of one company's e-invoice setting, plus the name, phone, SST and tourism-tax number.

    Those last four are required on LHDN's document and are not columns of the setting. The caller passes the values
    it already holds for this company. Returns one `FieldError` per missing or invalid field, or none.
    """
    problems = []
    labels = _company_labels()
    _text(problems, "name", details.get("name"), NAME_MAX, required=True, labels=labels)
    _bounded(problems, "tin", details.get("tin"), TIN_MAX, labels)
    _bounded(problems, "brn", details.get("brn"), BRN_MAX, labels)
    _sst(problems, "sst", details.get("sst"), labels)
    _ttx(problems, "ttx", details.get("ttx"), labels)
    _code(problems, "msic_code", details.get("msic_code"), msic_codes(), labels)
    _phone(problems, "phone", details.get("phone"), labels)
    _address(problems, details, labels)
    return tuple(problems)


def check_customer(details):
    """The buyer fields of one customer. A customer has no tourism-tax number on LHDN's buyer table.

    Returns one `FieldError` per missing or invalid field, or none. The caller passes one customer's values.
    """
    problems = []
    labels = _customer_labels()
    _text(problems, "legal_name", details.get("legal_name"), NAME_MAX, required=True, labels=labels)
    _bounded(problems, "tin", details.get("tin"), TIN_MAX, labels)
    _identity(problems, details.get("id_type"), details.get("id_value"), labels)
    _sst(problems, "sst", details.get("sst"), labels)
    _phone(problems, "phone", details.get("phone"), labels)
    _address(problems, details, labels)
    return tuple(problems)


def check_item(item):
    """The item's name and the three LHDN codes it carries. An unknown code is invalid; a blank one is missing."""
    problems = []
    labels = _item_labels()
    _text(problems, "name", item.get("name"), NAME_MAX, required=True, labels=labels)
    _code(problems, "classification_code", item.get("classification_code"), classification_codes(), labels)
    _code(problems, "unit_code", item.get("unit_code"), unit_codes(), labels)
    _code(problems, "default_tax_type", item.get("default_tax_type"), tax_types(), labels)
    return tuple(problems)


def check_line(line):
    """One invoice line, plus the document's currency and invoice number, which travel with the line.

    Measurement (the unit code) is optional on the line. A tax type of exemption ("E") also needs the exemption
    details and the exempted amount. Amounts are `Decimal`; a float is refused.
    """
    problems = []
    labels = _line_labels()
    _code(problems, "classification_code", line.get("classification_code"), classification_codes(), labels)
    _text(problems, "description", line.get("description"), NAME_MAX, required=True, labels=labels)
    unit = line.get("unit_code")
    if isinstance(unit, str) and unit.strip():
        _code(problems, "unit_code", unit, unit_codes(), labels)
    _amount(problems, "unit_price", line.get("unit_price"), labels)
    tax_type = line.get("tax_type")
    _code(problems, "tax_type", tax_type, tax_types(), labels)
    _amount(problems, "tax_amount", line.get("tax_amount"), labels)
    _amount(problems, "subtotal", line.get("subtotal"), labels)
    _amount(problems, "total_excluding_tax", line.get("total_excluding_tax"), labels)
    _code(problems, "currency_code", line.get("currency_code"), currency_codes(), labels)
    _text(problems, "invoice_number", line.get("invoice_number"), INVOICE_NUMBER_MAX, required=True, labels=labels)
    if tax_type == EXEMPT:
        _text(problems, "tax_exemption_reason", line.get("tax_exemption_reason"), EXEMPTION_MAX, required=True, labels=labels)
        _amount(problems, "amount_exempted", line.get("amount_exempted"), labels)
    return tuple(problems)


def _company_labels():
    return {
        "name": _("company's name"),
        "tin": _("company's TIN"),
        "brn": _("company's BRN"),
        "sst": _("company's SST registration number"),
        "ttx": _("company's tourism tax registration number"),
        "msic_code": _("company's MSIC code"),
        "phone": _("company's phone number"),
        "address_line1": _("company's address"),
        "address_line2": _("company's address line 2"),
        "address_line3": _("company's address line 3"),
        "city": _("company's city"),
        "postal_zone": _("company's postcode"),
        "state_code": _("company's state code"),
        "country_code": _("company's country code"),
    }


def _customer_labels():
    return {
        "legal_name": _("customer's name"),
        "tin": _("customer's TIN"),
        "id_type": _("customer's ID type"),
        "id_value": _("customer's ID value"),
        "sst": _("customer's SST registration number"),
        "phone": _("customer's phone number"),
        "address_line1": _("customer's address"),
        "address_line2": _("customer's address line 2"),
        "address_line3": _("customer's address line 3"),
        "city": _("customer's city"),
        "postal_zone": _("customer's postcode"),
        "state_code": _("customer's state code"),
        "country_code": _("customer's country code"),
    }


def _item_labels():
    return {
        "name": _("item name"),
        "classification_code": _("classification code"),
        "unit_code": _("unit code"),
        "default_tax_type": _("tax type"),
    }


def _line_labels():
    return {
        "classification_code": _("classification code"),
        "description": _("description"),
        "unit_code": _("unit code"),
        "unit_price": _("unit price"),
        "tax_type": _("tax type"),
        "tax_amount": _("tax amount"),
        "subtotal": _("subtotal"),
        "total_excluding_tax": _("total excluding tax"),
        "currency_code": _("currency code"),
        "invoice_number": _("invoice number"),
        "tax_exemption_reason": _("tax exemption details"),
        "amount_exempted": _("amount exempted from tax"),
    }


def _missing(field, labels):
    return FieldError(field, _("Enter the %(field)s.") % {"field": labels[field]})


def _too_long(field, limit, labels):
    return FieldError(
        field, _("The %(field)s must be %(limit)s characters or fewer.") % {"field": labels[field], "limit": limit}
    )


def _not_listed(field, labels):
    return FieldError(field, _("The %(field)s is not on LHDN's list.") % {"field": labels[field]})


def _blank(value):
    return not isinstance(value, str) or not value.strip()


def _text(problems, field, value, limit, *, required, labels):
    if _blank(value):
        if required:
            problems.append(_missing(field, labels))
        return
    if len(value) > limit:
        problems.append(_too_long(field, limit, labels))


def _bounded(problems, field, value, limit, labels):
    _text(problems, field, value, limit, required=True, labels=labels)


def _code(problems, field, value, known, labels):
    if _blank(value):
        problems.append(_missing(field, labels))
    elif value not in known:
        problems.append(_not_listed(field, labels))


def _phone(problems, field, value, labels):
    if _blank(value):
        problems.append(_missing(field, labels))
    elif not _PHONE.fullmatch(value) or not PHONE_MIN <= len(value) <= PHONE_MAX:
        problems.append(FieldError(
            field,
            _("The %(field)s must be 8 to 20 characters, with an optional + at the start and no spaces.")
            % {"field": labels[field]},
        ))


def _sst(problems, field, value, labels):
    if _blank(value):
        problems.append(FieldError(field, _("Enter the %(field)s, or NA if there is none.") % {"field": labels[field]}))
    elif value == "NA":
        return
    elif len(value) > SST_MAX:
        problems.append(_too_long(field, SST_MAX, labels))
    elif not _SST.fullmatch(value):
        problems.append(FieldError(
            field,
            _("The %(field)s can only use letters, digits, a dash, and one semicolon between two numbers.")
            % {"field": labels[field]},
        ))


def _ttx(problems, field, value, labels):
    if _blank(value):
        problems.append(FieldError(field, _("Enter the %(field)s, or NA if there is none.") % {"field": labels[field]}))
    elif value == "NA":
        return
    elif len(value) > TTX_MAX:
        problems.append(_too_long(field, TTX_MAX, labels))
    elif not _TTX.fullmatch(value):
        problems.append(FieldError(
            field, _("The %(field)s can only use letters, digits and a dash.") % {"field": labels[field]}
        ))


def _identity(problems, id_type, id_value, labels):
    if _blank(id_type):
        problems.append(_missing("id_type", labels))
    elif id_type not in ID_TYPES:
        problems.append(_not_listed("id_type", labels))
    if _blank(id_value):
        problems.append(_missing("id_value", labels))
    elif id_type in ID_MAX and len(id_value) > ID_MAX[id_type]:
        problems.append(_too_long("id_value", ID_MAX[id_type], labels))


def _address(problems, details, labels):
    country = details.get("country_code")
    _text(problems, "address_line1", details.get("address_line1"), ADDRESS_MAX, required=True, labels=labels)
    _text(problems, "address_line2", details.get("address_line2"), ADDRESS_MAX, required=False, labels=labels)
    _text(problems, "address_line3", details.get("address_line3"), ADDRESS_MAX, required=False, labels=labels)
    _text(problems, "city", details.get("city"), CITY_MAX, required=True, labels=labels)
    _postal(problems, details.get("postal_zone"), country, labels)
    _state(problems, details.get("state_code"), country, labels)
    _code(problems, "country_code", country, country_codes(), labels)


def _postal(problems, value, country, labels):
    # The field table marks the postal zone optional. The FAQ says a Malaysian one is "maximum 5-digit numbers
    # only"; the brief, and every Malaysian postcode, uses exactly five digits. Elsewhere the FAQ allows 50 characters.
    if value is None or value == "":
        return
    if not isinstance(value, str):
        problems.append(_missing("postal_zone", labels))
        return
    if country == MALAYSIA:
        if not re.fullmatch(r"[0-9]{5}", value):
            problems.append(FieldError("postal_zone", _("The %(field)s must be 5 digits.") % {"field": labels["postal_zone"]}))
    elif len(value) > POSTCODE_MAX:
        problems.append(_too_long("postal_zone", POSTCODE_MAX, labels))


def _state(problems, value, country, labels):
    # "00" is no longer a state code. Malaysia must use a code from the list, which includes 17 for "Not Applicable".
    # Any other country may send a state name, or 17, of at most 50 characters.
    if _blank(value):
        problems.append(_missing("state_code", labels))
        return
    invalid = value == "00" or (country == MALAYSIA and value not in state_codes())
    if invalid:
        problems.append(FieldError("state_code", _("The %(field)s is not a valid LHDN state.") % {"field": labels["state_code"]}))
    elif len(value) > STATE_MAX:
        problems.append(_too_long("state_code", STATE_MAX, labels))


def _amount(problems, field, value, labels):
    if value is None or value == "" or (isinstance(value, str) and not value.strip()):
        problems.append(_missing(field, labels))
    elif type(value) is not Decimal or not value.is_finite():
        problems.append(FieldError(field, _("The %(field)s must be a decimal amount.") % {"field": labels[field]}))
