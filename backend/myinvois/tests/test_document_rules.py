"""LHDN code lists and the required-field check for one document (ticket #122, D14, D59, D60).

The lists are LHDN's own downloads. The check says, for one company's e-invoice setting, one customer, one item and one
invoice line, which fields LHDN requires are missing or invalid. It is given those values; it never reads the database,
so it cannot see another company's rows. Made-up registration numbers only: no real TIN or BRN belongs here.
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import translation

from myinvois.code_lists import code_list_sources, classification_codes, country_codes, currency_codes, msic_codes, state_codes, tax_types, unit_codes
from myinvois.field_rules import FieldError, check_customer, check_einvoice_setting, check_item, check_line

DATA = Path(__file__).resolve().parents[1] / "codes"
# Fictional values, the same kind the setting tests use. C0000000001 is 11 characters, inside the field table's 14.
TIN, BRN = "C0000000001", "000000000001"
LISTS = (
    ("classification", "ClassificationCodes.json", "Description", "022", "Others"),
    ("unit", "UnitTypes.json", "Name", "C62", "one"),
    ("state", "StateCodes.json", "State", "14", "Wilayah Persekutuan Kuala Lumpur"),
    ("country", "CountryCodes.json", "Country", "MYS", "MALAYSIA"),
    ("tax_type", "TaxTypes.json", "Description", "06", "Not Applicable"),
    ("msic", "MSICSubCategoryCodes.json", "Description", "82193", "Secretarial support services"),
    ("currency", "CurrencyCodes.json", "Currency", "MYR", "Malaysian Ringgit"),
)
LOADERS = {
    "classification": classification_codes,
    "unit": unit_codes,
    "state": state_codes,
    "country": country_codes,
    "tax_type": tax_types,
    "msic": msic_codes,
    "currency": currency_codes,
}


def setting(**overrides):
    values = dict(
        name="Acme Test Sdn Bhd",
        tin=TIN,
        brn=BRN,
        sst="NA",
        ttx="NA",
        msic_code="82193",
        phone="+60123456789",
        address_line1="1 Test Street",
        address_line2="",
        address_line3="",
        city="Kuala Lumpur",
        postal_zone="50000",
        state_code="14",
        country_code="MYS",
    )
    return {**values, **overrides}


def customer(**overrides):
    values = dict(
        legal_name="Beta Buyer",
        tin=TIN,
        id_type="BRN",
        id_value=BRN,
        sst="NA",
        phone="+60123456780",
        address_line1="2 Test Street",
        address_line2="",
        address_line3="",
        city="Kuala Lumpur",
        postal_zone="50000",
        state_code="14",
        country_code="MYS",
    )
    return {**values, **overrides}


def item(**overrides):
    values = dict(name="Consulting hour", classification_code="022", unit_code="C62", default_tax_type="06")
    return {**values, **overrides}


def line(**overrides):
    values = dict(
        classification_code="022",
        description="Consulting hour",
        unit_price=Decimal("100.00"),
        tax_type="06",
        tax_amount=Decimal("0.00"),
        subtotal=Decimal("100.00"),
        total_excluding_tax=Decimal("100.00"),
        currency_code="MYR",
        invoice_number="INV-0001",
    )
    return {**values, **overrides}


def fields(problems):
    return [problem.field for problem in problems]


# --- the code lists ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name, filename, label, code, description", LISTS)
def test_each_code_list_is_loaded_from_its_versioned_file(name, filename, label, code, description):
    raw = json.loads((DATA / filename).read_text(encoding="utf-8"))

    loaded = LOADERS[name]()

    assert loaded[code] == description
    assert set(loaded) == {row["Code"] for row in raw}
    assert all(loaded[row["Code"]] == row[label].strip() for row in raw)


def test_each_code_list_records_its_source_and_the_date_it_was_taken():
    sources = code_list_sources()

    assert sources["taken_on"] == "2026-11-11"
    for name, filename, _label, _code, _description in LISTS:
        entry = sources["lists"][name]
        assert entry["page"].startswith("https://sdk.myinvois.hasil.gov.my/codes/")
        assert entry["file"] == f"https://sdk.myinvois.hasil.gov.my/files/{filename}"
        assert entry["filename"] == filename


@pytest.mark.parametrize(
    "check, payload, field, unknown",
    [
        (check_item, item, "classification_code", "999"),
        (check_item, item, "unit_code", "ZZZ"),
        (check_item, item, "default_tax_type", "99"),
        (check_line, line, "classification_code", "999"),
        (check_line, line, "currency_code", "ZZZ"),
        (check_einvoice_setting, setting, "msic_code", "99999"),
        (check_einvoice_setting, setting, "state_code", "99"),
        (check_einvoice_setting, setting, "country_code", "ZZZ"),
        (check_customer, customer, "state_code", "99"),
        (check_customer, customer, "country_code", "ZZZ"),
    ],
)
def test_an_unknown_code_is_named_as_invalid(check, payload, field, unknown):
    (problem,) = check(payload(**{field: unknown}))

    assert problem.field == field
    assert isinstance(problem, FieldError)
    assert unknown not in problem.message


# --- a complete record, and each missing field ---------------------------------------------------------------------


def test_a_complete_setting_customer_item_and_line_produce_no_errors():
    assert check_einvoice_setting(setting()) == ()
    assert check_customer(customer()) == ()
    assert check_item(item()) == ()
    assert check_line(line()) == ()


SETTING_REQUIRED = (
    "name", "tin", "brn", "sst", "ttx", "msic_code", "phone", "address_line1", "city", "state_code", "country_code",
)
CUSTOMER_REQUIRED = (
    "legal_name", "tin", "id_type", "id_value", "sst", "phone", "address_line1", "city", "state_code", "country_code",
)
ITEM_REQUIRED = ("name", "classification_code", "unit_code", "default_tax_type")
LINE_REQUIRED = (
    "classification_code", "description", "unit_price", "tax_type", "tax_amount", "subtotal",
    "total_excluding_tax", "currency_code", "invoice_number",
)


@pytest.mark.parametrize(
    "check, payload, required",
    [
        (check_einvoice_setting, setting, SETTING_REQUIRED),
        (check_customer, customer, CUSTOMER_REQUIRED),
        (check_item, item, ITEM_REQUIRED),
        (check_line, line, LINE_REQUIRED),
    ],
)
def test_every_missing_required_field_is_named(check, payload, required):
    assert fields(check({})) == list(required)


@pytest.mark.parametrize(
    "check, payload, required",
    [
        (check_einvoice_setting, setting, SETTING_REQUIRED),
        (check_customer, customer, CUSTOMER_REQUIRED),
        (check_item, item, ITEM_REQUIRED),
        (check_line, line, LINE_REQUIRED),
    ],
)
def test_one_missing_field_is_named_and_a_complete_record_is_not(check, payload, required):
    for field in required:
        assert fields(check(payload(**{field: ""}))) == [field]
        assert fields(check(payload(**{field: "   "}))) == [field]


def test_several_missing_fields_are_all_named():
    details = setting(name="", phone="", msic_code="")

    assert fields(check_einvoice_setting(details)) == ["name", "msic_code", "phone"]


# --- the field-length and format rules from the brief --------------------------------------------------------------


@pytest.mark.parametrize(
    "check, payload, field",
    [
        (check_einvoice_setting, setting, "name"),
        (check_customer, customer, "legal_name"),
        (check_item, item, "name"),
        (check_line, line, "description"),
    ],
)
def test_a_name_may_be_300_characters_and_no_longer(check, payload, field):
    assert check(payload(**{field: "A" * 300})) == ()
    (problem,) = check(payload(**{field: "A" * 301}))

    assert problem.field == field
    assert "300" in problem.message


@pytest.mark.parametrize(
    "number",
    ["1234567", "1" * 21, "+1" * 1 + "2" * 20, "60 12345678", "+60 1234567", "6012-345678", "NA"],
)
@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_a_phone_number_is_8_to_20_characters_with_an_optional_plus_and_no_spaces(check, payload, number):
    (problem,) = check(payload(phone=number))

    assert problem.field == "phone"
    assert "8" in problem.message and "20" in problem.message
    assert number not in problem.message


@pytest.mark.parametrize("number", ["12345678", "1" * 20, "+1234567", "+60123456789"])
@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_a_phone_number_of_8_to_20_characters_is_accepted(check, payload, number):
    assert check(payload(phone=number)) == ()


@pytest.mark.parametrize(
    "check, payload, field",
    [
        (check_einvoice_setting, setting, "address_line1"),
        (check_einvoice_setting, setting, "address_line2"),
        (check_einvoice_setting, setting, "address_line3"),
        (check_customer, customer, "address_line1"),
        (check_customer, customer, "address_line2"),
        (check_customer, customer, "address_line3"),
    ],
)
def test_an_address_line_may_be_150_characters_and_no_longer(check, payload, field):
    assert check(payload(**{field: "A" * 150})) == ()
    (problem,) = check(payload(**{field: "A" * 151}))

    assert problem.field == field
    assert "150" in problem.message


@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
@pytest.mark.parametrize("postcode", ["1234", "123456", "5000A", "50000 ", "12 000"])
def test_a_malaysian_postcode_is_5_digits(check, payload, postcode):
    (problem,) = check(payload(country_code="MYS", postal_zone=postcode))

    assert problem.field == "postal_zone"
    assert "5 digits" in problem.message
    assert postcode not in problem.message


@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_a_postcode_is_optional_and_a_foreign_one_may_be_50_characters(check, payload):
    assert check(payload(postal_zone="")) == ()
    assert check(payload(country_code="SGP", state_code="Singapore", postal_zone="SW1A 1AA")) == ()
    assert check(payload(country_code="SGP", state_code="Singapore", postal_zone="A" * 50)) == ()
    (problem,) = check(payload(country_code="SGP", state_code="Singapore", postal_zone="A" * 51))

    assert problem.field == "postal_zone"
    assert "50" in problem.message


@pytest.mark.parametrize(
    "value",
    ["NA", "A01-2345-67891012", "A01-2345-67891012;B02-2345-67891012"],
)
@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_sst_accepts_na_and_up_to_two_numbers(check, payload, value):
    assert check(payload(sst=value)) == ()


@pytest.mark.parametrize("value", ["A01 2345", "A01/2345", "A;B;C", "A;", ";B"])
@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_sst_rejects_anything_other_than_letters_digits_a_dash_and_one_semicolon(check, payload, value):
    (problem,) = check(payload(sst=value))

    assert problem.field == "sst"
    assert value not in problem.message


def test_sst_is_at_most_35_characters():
    assert check_einvoice_setting(setting(sst="A" * 35)) == ()
    (problem,) = check_einvoice_setting(setting(sst="A" * 36))

    assert problem.field == "sst"
    assert "35" in problem.message


@pytest.mark.parametrize("value", ["NA", "123-4567-89012345"])
def test_tourism_tax_accepts_na(value):
    assert check_einvoice_setting(setting(ttx=value)) == ()


@pytest.mark.parametrize("value", ["123 4567", "123/4567", "123;4567"])
def test_tourism_tax_allows_only_letters_digits_and_a_dash(value):
    (problem,) = check_einvoice_setting(setting(ttx=value))

    assert problem.field == "ttx"
    assert value not in problem.message


def test_tourism_tax_is_at_most_17_characters():
    assert check_einvoice_setting(setting(ttx="1" * 17)) == ()
    (problem,) = check_einvoice_setting(setting(ttx="1" * 18))

    assert problem.field == "ttx"
    assert "17" in problem.message


def test_a_customer_has_no_tourism_tax_field():
    assert "ttx" not in fields(check_customer({}))
    assert check_customer(customer()) == ()


@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_state_code_00_is_not_valid(check, payload):
    (problem,) = check(payload(state_code="00"))

    assert problem.field == "state_code"
    assert "00" not in problem.message
    assert "state" in problem.message.lower()


@pytest.mark.parametrize("check, payload", [(check_einvoice_setting, setting), (check_customer, customer)])
def test_state_code_00_is_not_valid_for_a_foreign_address_either(check, payload):
    (problem,) = check(payload(country_code="SGP", state_code="00"))

    assert problem.field == "state_code"


def test_a_malaysian_state_must_be_on_the_list_and_a_foreign_state_may_be_a_name():
    assert check_customer(customer(state_code="17")) == ()
    (problem,) = check_customer(customer(state_code="Texas"))
    assert problem.field == "state_code"

    assert check_customer(customer(country_code="USA", state_code="Texas", postal_zone="78701")) == ()


def test_an_invoice_number_may_be_50_characters_and_no_longer():
    assert check_line(line(invoice_number="A" * 50)) == ()
    (problem,) = check_line(line(invoice_number="A" * 51))

    assert problem.field == "invoice_number"
    assert "50" in problem.message


def test_a_line_may_omit_its_unit_code_and_an_item_may_not():
    assert check_line(line()) == ()
    (problem,) = check_line(line(unit_code="ZZZ"))
    assert problem.field == "unit_code"

    assert fields(check_item(item(unit_code=""))) == ["unit_code"]


def test_a_float_amount_is_refused_and_a_decimal_amount_is_accepted():
    (problem,) = check_line(line(unit_price=17.0))

    assert problem.field == "unit_price"
    assert "decimal" in problem.message.lower()
    assert check_line(line(unit_price=Decimal("17.00"), tax_amount=Decimal("0"), subtotal=Decimal("17"), total_excluding_tax=Decimal("17"))) == ()


def test_a_tax_exemption_needs_its_reason_and_the_exempted_amount():
    exempt = line(tax_type="E")

    assert fields(check_line(exempt)) == ["tax_exemption_reason", "amount_exempted"]
    assert check_line(line(
        tax_type="E",
        tax_exemption_reason="Goods acquired with SST exemption under Sales Tax Act 2018. Reference No: ABC.TAX(S)012-3/4/56(7)",
        amount_exempted=Decimal("100.00"),
    )) == ()


def test_a_city_may_be_50_characters_and_a_registration_number_keeps_its_maximum():
    assert check_einvoice_setting(setting(city="A" * 50, tin="C" + "1" * 13, brn="1" * 20)) == ()
    assert fields(check_einvoice_setting(setting(city="A" * 51))) == ["city"]
    assert fields(check_einvoice_setting(setting(tin="C" + "1" * 14))) == ["tin"]
    assert fields(check_einvoice_setting(setting(brn="1" * 21))) == ["brn"]
    assert check_customer(customer(id_type="NRIC", id_value="1" * 12)) == ()
    assert fields(check_customer(customer(id_type="NRIC", id_value="1" * 13))) == ["id_value"]
    assert fields(check_customer(customer(id_type="PASSPORT", id_value="A" * 13))) == ["id_value"]
    assert fields(check_customer(customer(id_type="ARMY", id_value="1" * 13))) == ["id_value"]


def test_a_tin_or_registration_number_is_never_repeated_in_the_error():
    (problem,) = check_einvoice_setting(setting(tin="C" + "9" * 20))

    assert problem.field == "tin"
    assert "C" + "9" * 20 not in problem.message


def test_the_check_does_not_read_another_companys_data(world):
    assert world["acme"].name != world["beta"].name

    with CaptureQueriesContext(connection) as captured:
        problems = (
            check_einvoice_setting(setting())
            + check_customer(customer())
            + check_item(item())
            + check_line(line())
        )

    assert problems == ()
    assert captured.captured_queries == []


def test_a_missing_field_is_named_in_chinese():
    with translation.override("zh-hans"):
        (problem,) = check_item(item(name=""))

    assert problem.field == "name"
    assert "项目名称" in problem.message
