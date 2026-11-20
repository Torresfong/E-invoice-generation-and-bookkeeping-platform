"""Mapping an approved invoice to LHDN's UBL 2.1 JSON document (ticket #123, D14, D59, D60).

#123 maps ringgit invoices only: no per-line SST (#52) and no foreign currency (#53) exist yet, so every line's tax
type is LHDN's "Not Applicable" (06) with a zero tax amount, and the document currency is always MYR. #125 adds real
per-line tax and foreign currency once #52 and #53 exist.

The mapper reads the invoice, the company's e-invoice setting and each line's live item (classification code and unit
code; #46's handoff note records why reading the item live, not a frozen copy, is accepted for this ticket). It never
reaches the customer master: the buyer is the locked snapshot already copied onto the invoice at approval (#46).
"""

import json
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

import pytest
from django.db import connection
from django.utils import timezone

from core import customers, einvoicing, items, invoices, numbering
from core.jobs import PermanentError
from core.models import CompanyEInvoiceSetting, Invoice
from myinvois import document
from myinvois.document import InvalidDocument, StaleIssueTime, map_invoice

from core.tests.test_customers import BRN_VALUE, CONTACT, TIN, switch_einvoicing_on

OTHERS, ONE, NOT_APPLICABLE = "022", "C62", "06"


@pytest.fixture
def books(world):
    owner, acme = world["acme_owner"], world["acme"]
    numbering.configure_sequence(acme, numbering.INVOICE, prefix="INV-", start_number=1)
    switch_einvoicing_on(owner, acme)
    return {
        "customer": customers.create_customer(
            owner, acme, legal_name="Buyer Sdn Bhd", tin=TIN, id_type="BRN", id_value=BRN_VALUE, **CONTACT
        ),
        "item": items.create_item(
            owner, acme, name="Widget", classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
        ),
    }


def approved_invoice(world, books, *, lines=None, user="acme_owner"):
    owner, acme = world[user], world["acme"]
    draft = invoices.create_draft(
        owner, acme, customer_id=books["customer"].pk,
        lines=lines or [{"item": books["item"].pk, "quantity": "2", "unit_price": "5.50", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )
    return invoices.approve_invoice(owner, draft)


def decode(document_bytes):
    return json.loads(document_bytes.decode("utf-8"))


def decode_exact(document_bytes):
    """Like `decode`, but every number comes back as a `Decimal`, not a `float`.

    Codex's review of PR #191 found that plain `decode()` cannot tell a correctly rounded large amount from a
    wrongly rounded one: both round to the *same* float once a number needs more significant digits than a float
    holds (about 15-17), so a float-based assertion passes whether or not the precision bug it was meant to catch
    is still there. Use this, and a `Decimal(...)` literal to compare against, for any assertion on an amount
    that is not a small, ordinary invoice total.
    """
    return json.loads(document_bytes.decode("utf-8"), parse_float=Decimal)


def force_approved_invoice(world, books, *, buyer_overrides=None, invoice_overrides=None, lines=None):
    """An approved e-invoice built by a raw update, not `approve_invoice` (which always refuses an incomplete buyer).

    Lets a test put a gap in the frozen buyer snapshot that the engine's own approval check would never allow
    through, the same technique `test_invoice_approval.py` uses for the engine's own locked-row tests.
    """
    owner, acme = world["acme_owner"], world["acme"]
    draft = invoices.create_draft(
        owner, acme, customer_id=books["customer"].pk,
        lines=lines or [{"item": books["item"].pk, "quantity": "1", "unit_price": "10.00", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )
    for line in draft.lines.select_related("item"):
        line.item_name = line.item.name
        line.save(update_fields=["item_name"])
    buyer = {
        **dict(
            buyer_legal_name="Buyer Sdn Bhd", buyer_tin=TIN, buyer_id_type="BRN", buyer_id_value=BRN_VALUE,
            buyer_phone=CONTACT["phone"], buyer_sst_number=CONTACT["sst_number"],
            buyer_address_line1=CONTACT["address_line1"], buyer_address_line2=CONTACT["address_line2"],
            buyer_city=CONTACT["city"], buyer_postal_zone=CONTACT["postal_zone"],
            buyer_state_code=CONTACT["state_code"], buyer_country_code=CONTACT["country_code"],
        ),
        **(buyer_overrides or {}),
    }
    Invoice.objects.filter(pk=draft.pk).update(
        **{
            "status": "approved", "number": f"INV-FORCED-{draft.pk}", "approved_at": timezone.now(),
            "approved_by": owner, "is_einvoice": True, **buyer, **(invoice_overrides or {}),
        }
    )
    draft.refresh_from_db()
    return draft


def test_a_simple_approved_invoice_maps_to_the_lhdn_envelope_and_header(world, books):
    invoice = approved_invoice(world, books)

    document = decode(map_invoice(invoice, document_version="1.0", now=invoice.approved_at))

    assert document["_D"] == "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    assert document["_A"] == "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    assert document["_B"] == "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
    (inv,) = document["Invoice"]
    assert inv["ID"] == [{"_": invoice.number}]
    assert inv["InvoiceTypeCode"] == [{"_": "01", "listVersionID": "1.0"}]
    assert inv["DocumentCurrencyCode"] == [{"_": "MYR"}]


def test_the_issue_date_and_time_are_utc_from_the_approval_time(world, books):
    invoice = approved_invoice(world, books)
    noon_kl = invoice.approved_at.astimezone(dt_timezone.utc).replace(
        year=2026, month=11, day=17, hour=20, minute=5, second=30, microsecond=0,
    )
    invoice.approved_at = noon_kl
    invoice.save(update_fields=["approved_at"])

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=noon_kl))["Invoice"]

    assert inv["IssueDate"] == [{"_": "2026-11-17"}]
    assert inv["IssueTime"] == [{"_": "20:05:30Z"}]


def test_the_document_version_is_whatever_the_caller_passes(world, books):
    invoice = approved_invoice(world, books)

    for version in ("1.0", "1.1"):
        (inv,) = decode(map_invoice(invoice, document_version=version, now=invoice.approved_at))["Invoice"]
        assert inv["InvoiceTypeCode"] == [{"_": "01", "listVersionID": version}]


def test_the_supplier_comes_from_the_companys_einvoice_setting(world, books):
    invoice = approved_invoice(world, books)

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]
    (party,) = inv["AccountingSupplierParty"][0]["Party"]

    assert party["PartyLegalEntity"] == [{"RegistrationName": [{"_": "Acme Registered Name Sdn Bhd"}]}]
    assert party["IndustryClassificationCode"] == [
        {"_": "82193", "name": "Secretarial support services"}
    ]
    assert {"ID": [{"_": "C9999999999", "schemeID": "TIN"}]} in party["PartyIdentification"]
    assert {"ID": [{"_": "200001000001", "schemeID": "BRN"}]} in party["PartyIdentification"]
    # Core stores a blank SST/tourism-tax number to mean "none"; the mapper writes LHDN's own sentinel "NA" (#172).
    assert {"ID": [{"_": "NA", "schemeID": "SST"}]} in party["PartyIdentification"]
    assert {"ID": [{"_": "NA", "schemeID": "TTX"}]} in party["PartyIdentification"]
    assert party["Contact"] == [{"Telephone": [{"_": "+60312345678"}]}]
    (address,) = party["PostalAddress"]
    assert address["CityName"] == [{"_": "Kuala Lumpur"}]
    assert address["CountrySubentityCode"] == [{"_": "14"}]
    assert address["Country"] == [{"IdentificationCode": [{"_": "MYS", "listID": "ISO3166-1", "listAgencyID": "6"}]}]
    assert "PostalZone" not in address or address["PostalZone"] == [{"_": "50000"}]
    assert address["AddressLine"] == [{"Line": [{"_": "1 Test Street"}]}]  # address_line2 is blank: omitted


def test_the_buyer_is_the_locked_snapshot_never_the_live_customer(world, books):
    invoice = approved_invoice(world, books)
    customers.update_customer(world["acme_owner"], books["customer"], legal_name="Renamed After Approval")

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]
    (party,) = inv["AccountingCustomerParty"][0]["Party"]

    assert party["PartyLegalEntity"] == [{"RegistrationName": [{"_": "Buyer Sdn Bhd"}]}]
    assert "Renamed" not in json.dumps(party)
    assert {"ID": [{"_": TIN, "schemeID": "TIN"}]} in party["PartyIdentification"]
    assert {"ID": [{"_": BRN_VALUE, "schemeID": "BRN"}]} in party["PartyIdentification"]
    assert {"ID": [{"_": CONTACT["sst_number"], "schemeID": "SST"}]} in party["PartyIdentification"]
    assert party["Contact"] == [{"Telephone": [{"_": CONTACT["phone"]}]}]


def test_a_single_line_maps_its_item_and_amounts(world, books):
    invoice = approved_invoice(
        world, books,
        lines=[{"item": books["item"].pk, "quantity": "2", "unit_price": "5.50", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]
    (line,) = inv["InvoiceLine"]

    assert line["ID"] == [{"_": "1"}]
    assert line["InvoicedQuantity"] == [{"_": 2, "unitCode": ONE}]
    assert line["Item"][0]["Description"] == [{"_": "Widget"}]
    assert line["Item"][0]["CommodityClassification"] == [{"ItemClassificationCode": [{"_": OTHERS, "listID": "CLASS"}]}]
    assert line["Price"] == [{"PriceAmount": [{"_": 5.50, "currencyID": "MYR"}]}]
    assert line["ItemPriceExtension"] == [{"Amount": [{"_": 11.00, "currencyID": "MYR"}]}]
    assert line["LineExtensionAmount"] == [{"_": 11.00, "currencyID": "MYR"}]
    (subtotal,) = line["TaxTotal"]
    assert subtotal["TaxAmount"] == [{"_": 0.00, "currencyID": "MYR"}]
    assert subtotal["TaxSubtotal"][0]["TaxCategory"][0]["ID"] == [{"_": NOT_APPLICABLE}]


def test_the_locked_item_name_is_used_but_the_codes_are_read_live(world, books):
    invoice = approved_invoice(world, books)
    items.update_item(world["acme_owner"], books["item"], name="Renamed widget", classification_code="023")

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]
    (line,) = inv["InvoiceLine"]

    # The locked snapshot (#46): the invoice shows the name as it was at approval.
    assert line["Item"][0]["Description"] == [{"_": "Widget"}]
    # Not frozen (deliberate, per the module's docstring and #46's handoff note): a code change after approval
    # still reaches the next mapping. A future ticket that chooses to freeze it changes this assertion.
    assert line["Item"][0]["CommodityClassification"] == [{"ItemClassificationCode": [{"_": "023", "listID": "CLASS"}]}]


def test_a_multi_line_invoice_sums_its_totals(world, books):
    second = items.create_item(
        world["acme_owner"], world["acme"], name="Gadget", classification_code=OTHERS, unit_code=ONE,
        default_tax_type=NOT_APPLICABLE,
    )
    invoice = approved_invoice(world, books, lines=[
        {"item": books["item"].pk, "quantity": "2", "unit_price": "5.50", "tax_type": NOT_APPLICABLE, "tax_rate": "0"},
        {"item": second.pk, "quantity": "3", "unit_price": "1.25", "tax_type": NOT_APPLICABLE, "tax_rate": "0"},
    ])

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]

    assert len(inv["InvoiceLine"]) == 2
    assert [line["ID"] for line in inv["InvoiceLine"]] == [[{"_": "1"}], [{"_": "2"}]]
    total = inv["LegalMonetaryTotal"][0]
    # 2*5.50 + 3*1.25 = 11.00 + 3.75 = 14.75; no tax is calculated yet (#52, #125), so inclusive == exclusive.
    assert total["TaxExclusiveAmount"] == [{"_": 14.75, "currencyID": "MYR"}]
    assert total["TaxInclusiveAmount"] == [{"_": 14.75, "currencyID": "MYR"}]
    assert total["PayableAmount"] == [{"_": 14.75, "currencyID": "MYR"}]
    assert inv["TaxTotal"][0]["TaxAmount"] == [{"_": 0.00, "currencyID": "MYR"}]


# --- refusals --------------------------------------------------------------------------------------------------


def test_a_missing_supplier_field_is_named_and_nothing_is_returned(world, books):
    invoice = approved_invoice(world, books)
    # The setting's own history trigger refuses a write that skips `update_setting` (and that service itself refuses
    # a blank required field); disabling the trigger is how `test_invoice_approval.py` simulates data saved before
    # #172 added these checks, which the mapper must still refuse rather than map blank.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE core_companyeinvoicesetting DISABLE TRIGGER USER")
        CompanyEInvoiceSetting.objects.filter(company=world["acme"]).update(phone="")
        cursor.execute("ALTER TABLE core_companyeinvoicesetting ENABLE TRIGGER USER")

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=invoice.approved_at)

    assert "phone" in str(refused.value)


def test_a_missing_buyer_field_is_named(world, books):
    invoice = force_approved_invoice(world, books, buyer_overrides={"buyer_city": ""})

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=invoice.approved_at)

    # Not just "city": both parties have one, and the message, not the field key, is what distinguishes them
    # (field_rules.py's own `_customer_labels`/`_company_labels`). This pins that it is the buyer's that is named.
    assert "customer's city" in str(refused.value)


def test_a_missing_supplier_field_names_the_supplier_not_the_buyer(world, books):
    invoice = approved_invoice(world, books)
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("ALTER TABLE core_companyeinvoicesetting DISABLE TRIGGER USER")
        CompanyEInvoiceSetting.objects.filter(company=world["acme"]).update(city="")
        cursor.execute("ALTER TABLE core_companyeinvoicesetting ENABLE TRIGGER USER")

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=invoice.approved_at)

    assert "company's city" in str(refused.value) and "customer's city" not in str(refused.value)


def test_an_issue_time_in_the_future_is_refused(world, books):
    invoice = approved_invoice(world, books)
    future = invoice.approved_at + timedelta(hours=1)

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=future - timedelta(hours=2))

    assert "future" in str(refused.value)
    assert not isinstance(refused.value, StaleIssueTime)  # a future time can never recover by lookup (D58 (5))


def test_an_issue_time_more_than_72_hours_old_is_refused_as_a_distinguishable_stale_subclass(world, books):
    invoice = approved_invoice(world, books)
    too_late = invoice.approved_at + timedelta(hours=73)

    with pytest.raises(StaleIssueTime) as refused:
        map_invoice(invoice, document_version="1.1", now=too_late)

    assert "72" in str(refused.value)
    assert isinstance(refused.value, InvalidDocument)  # #47 may still catch the general case


def test_an_issue_time_within_72_hours_is_accepted(world, books):
    invoice = approved_invoice(world, books)
    just_in_time = invoice.approved_at + timedelta(hours=71, minutes=59)

    map_invoice(invoice, document_version="1.1", now=just_in_time)  # does not raise


def test_invalid_document_is_a_permanent_error_so_a_job_never_retries_it_blindly(world, books):
    # core.jobs._after_failure gives a PermanentError exactly one attempt; a missing field or a bad document
    # version will not fix itself on retry, unlike StaleIssueTime's recovery path (handled by #47, see its docstring).
    assert issubclass(InvalidDocument, PermanentError)
    assert issubclass(StaleIssueTime, InvalidDocument)


def test_an_unsupported_document_version_is_refused(world, books):
    invoice = approved_invoice(world, books)

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="9.9", now=invoice.approved_at)

    assert "document_version" in str(refused.value)


@pytest.mark.parametrize("version", ["1.0", "1.1"])
def test_each_supported_document_version_is_accepted(world, books, version):
    invoice = approved_invoice(world, books)

    map_invoice(invoice, document_version=version, now=invoice.approved_at)  # does not raise


def test_a_line_amount_rounds_half_a_sen_up(world, books):
    # The first amount calculation in the codebase (InvoiceLine stores none of its own); pinned at the boundary
    # that distinguishes ROUND_HALF_UP from ROUND_HALF_EVEN or truncation, raised for Torres on PR #191 as a
    # choice no D-number records yet (see `_line_details`'s docstring).
    invoice = approved_invoice(
        world, books,
        lines=[{"item": books["item"].pk, "quantity": "1", "unit_price": "0.005", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]

    (line,) = inv["InvoiceLine"]
    assert line["ItemPriceExtension"] == [{"Amount": [{"_": 0.01, "currencyID": "MYR"}]}]


def test_a_large_line_amount_rounds_correctly_despite_pythons_default_decimal_precision(world, books):
    # Codex's first finding on PR #191: quantity and unit_price are each NUMERIC(18,4) (core/models.py), so their
    # exact product can need up to 36 significant digits - more than Python's default 28-digit Decimal context.
    # Both values below are inside what core.invoices._amount already accepts (at most 14 integer, 4 fractional
    # digits each), so this is reachable, not a theoretical maximum. Under the ambient (default) context the
    # multiplication itself would already round to 28 significant digits, under ROUND_HALF_EVEN, before
    # `.quantize(..., ROUND_HALF_UP)` ever saw the result - giving ...438.56 instead of the correctly rounded
    # ...438.55 confirmed by hand below.
    #
    # Codex's second finding, on the first fix: `decode()` (plain `json.loads`) turns every number into a float,
    # and a float cannot hold this many significant digits either, so the wrong ...438.56 and the right ...438.55
    # become the *same* float and the assertion could not actually tell them apart. `decode_exact()` keeps every
    # number as a `Decimal`, so the comparison below is exact.
    invoice = approved_invoice(world, books, lines=[
        {
            "item": books["item"].pk, "quantity": "70442201917.6046", "unit_price": "55353191799.5913",
            "tax_type": NOT_APPLICABLE, "tax_rate": "0",
        },
    ])

    (inv,) = decode_exact(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]

    (line,) = inv["InvoiceLine"]
    assert line["ItemPriceExtension"] == [
        {"Amount": [{"_": Decimal("3899200713530705492438.55"), "currencyID": "MYR"}]}
    ]


def test_a_large_document_total_keeps_its_precision_past_the_line_amount(world, books):
    # Codex's second finding on PR #191: the first fix only widened the precision of the per-line multiplication
    # in `_line_details`, so a large line amount was correct on its own but the summation in `_totals` and the
    # `total_excluding_tax + total_tax` addition in `_build` still ran in the default 28-digit context, silently
    # dropping digits a second time on the way from a (now correct) line amount to the document's own totals.
    invoice = approved_invoice(world, books, lines=[
        {
            "item": books["item"].pk, "quantity": "99999999999999.1234", "unit_price": "99999999999999.5678",
            "tax_type": NOT_APPLICABLE, "tax_rate": "0",
        },
    ])
    expected = Decimal("9999999999999869120000000000.38")

    decoded = decode_exact(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))

    (inv,) = decoded["Invoice"]
    (line,) = inv["InvoiceLine"]
    total = inv["LegalMonetaryTotal"][0]
    assert line["ItemPriceExtension"] == [{"Amount": [{"_": expected, "currencyID": "MYR"}]}]
    assert total["TaxExclusiveAmount"] == [{"_": expected, "currencyID": "MYR"}]
    assert total["TaxInclusiveAmount"] == [{"_": expected, "currencyID": "MYR"}]
    assert total["PayableAmount"] == [{"_": expected, "currencyID": "MYR"}]


def test_a_draft_invoice_is_refused(world, books):
    owner, acme = world["acme_owner"], world["acme"]
    draft = invoices.create_draft(
        owner, acme, customer_id=books["customer"].pk,
        lines=[{"item": books["item"].pk, "quantity": "1", "unit_price": "10.00", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(draft, document_version="1.1", now=timezone.now())

    assert "approved" in str(refused.value)


def test_a_normal_invoice_is_refused(world, books):
    owner, acme = world["acme_owner"], world["acme"]
    # e-invoicing off, so approval needs no complete buyer and the invoice is normal (D59 (3)): never submitted.
    einvoicing.update_setting(owner, acme, einvoicing_enabled=False, status="voluntary", effective_from=date(2020, 1, 1))
    bare = customers.create_customer(owner, acme, legal_name="Walk-in Buyer")
    draft = invoices.create_draft(
        owner, acme, customer_id=bare.pk,
        lines=[{"item": books["item"].pk, "quantity": "1", "unit_price": "10.00", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )
    invoice = invoices.approve_invoice(owner, draft)
    assert invoice.is_einvoice is False

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=invoice.approved_at)

    assert "is_einvoice" in str(refused.value)


def test_a_credit_note_is_refused(world, books):
    # Credit, debit and refund notes are #124: this mapper only ever maps `document_type="invoice"`.
    invoice = force_approved_invoice(world, books, invoice_overrides={"document_type": "credit_note"})

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=invoice.approved_at)

    assert "invoice" in str(refused.value)


def test_the_mapper_never_calculates_tax_even_when_the_item_has_a_real_tax_type(world, books):
    items.update_item(world["acme_owner"], books["item"], default_tax_type="01")  # Sales Tax: still never used (#52, #125)
    invoice = approved_invoice(world, books)

    (inv,) = decode(map_invoice(invoice, document_version="1.1", now=invoice.approved_at))["Invoice"]

    (line,) = inv["InvoiceLine"]
    assert line["TaxTotal"][0]["TaxSubtotal"][0]["TaxCategory"][0]["ID"] == [{"_": NOT_APPLICABLE}]
    assert line["TaxTotal"][0]["TaxAmount"] == [{"_": 0.00, "currencyID": "MYR"}]


def test_a_cross_company_mapping_never_leaks_the_other_companys_supplier_or_buyer(world, books):
    beta_owner, beta = world["beta_owner"], world["beta"]
    einvoicing.update_setting(
        beta_owner, beta, einvoicing_enabled=True, status="voluntary", effective_from=date(2020, 1, 1),
        legal_name="Beta Books Registered Name", phone="+60399999999", tin="C1111111111", brn="200009999999",
        msic_code="82193", address_line1="9 Beta Lane", city="Johor Bahru", postal_zone="80000",
        state_code="01", country_code="MYS",
    )
    numbering.configure_sequence(beta, numbering.INVOICE, prefix="BETA-", start_number=1)
    beta_item = items.create_item(
        beta_owner, beta, name="Beta Widget", classification_code=OTHERS, unit_code=ONE,
        default_tax_type=NOT_APPLICABLE,
    )
    beta_customer = customers.create_customer(
        beta_owner, beta, legal_name="Beta's Own Buyer", tin="C2222222222", id_type="BRN", id_value="200008888888",
        **CONTACT,
    )
    beta_draft = invoices.create_draft(
        beta_owner, beta, customer_id=beta_customer.pk,
        lines=[{"item": beta_item.pk, "quantity": "1", "unit_price": "1.00", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )
    beta_invoice = invoices.approve_invoice(beta_owner, beta_draft)
    acme_invoice = approved_invoice(world, books)

    acme_document = json.dumps(decode(map_invoice(acme_invoice, document_version="1.1", now=acme_invoice.approved_at)))
    beta_document = json.dumps(decode(map_invoice(beta_invoice, document_version="1.1", now=beta_invoice.approved_at)))

    assert "Beta" not in acme_document and "Acme" not in beta_document
    assert "Buyer Sdn Bhd" not in beta_document and "Beta's Own Buyer" not in acme_document


# --- matching LHDN's own published sample structure --------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_a_simple_invoice_matches_lhdns_published_sample_structure(world, books):
    """`invoice_basic.json` is #123's mandatory-field subset of LHDN's own "Invoice 1.1 Sample JSON"

    (https://sdk.myinvois.hasil.gov.my/files/sdksamples/1.1-Invoice-Sample.json, fetched 2026-11-17 from the page
    linked at https://sdk.myinvois.hasil.gov.my/documents/invoice-v1-1/): the same envelope, key names and nesting,
    with the optional elements #123 does not emit (InvoicePeriod, BillingReference, Delivery, PaymentMeans,
    AllowanceCharge, and the Signature/UBLExtensions block #43 adds) left out, and this test's own supplier, buyer
    and line values in place of the sample's.
    """
    invoice = approved_invoice(world, books)
    frozen_issue_time = datetime(2026, 1, 15, 8, 0, 0, tzinfo=dt_timezone.utc)
    Invoice.objects.filter(pk=invoice.pk).update(approved_at=frozen_issue_time)
    invoice.refresh_from_db()
    expected = json.loads((FIXTURES / "invoice_basic.json").read_text(encoding="utf-8"))

    document = decode(map_invoice(invoice, document_version="1.1", now=frozen_issue_time))

    assert document == expected


def test_a_multi_line_invoice_matches_lhdns_published_sample_structure(world, books):
    """`invoice_multiline.json`: the same derivation as the simple-invoice fixture, with a second line."""
    second = items.create_item(
        world["acme_owner"], world["acme"], name="Gadget", classification_code=OTHERS, unit_code=ONE,
        default_tax_type=NOT_APPLICABLE,
    )
    invoice = approved_invoice(world, books, lines=[
        {"item": books["item"].pk, "quantity": "2", "unit_price": "5.50", "tax_type": NOT_APPLICABLE, "tax_rate": "0"},
        {"item": second.pk, "quantity": "3", "unit_price": "1.25", "tax_type": NOT_APPLICABLE, "tax_rate": "0"},
    ])
    frozen_issue_time = datetime(2026, 1, 15, 8, 0, 0, tzinfo=dt_timezone.utc)
    Invoice.objects.filter(pk=invoice.pk).update(approved_at=frozen_issue_time)
    invoice.refresh_from_db()
    expected = json.loads((FIXTURES / "invoice_multiline.json").read_text(encoding="utf-8"))

    document = decode(map_invoice(invoice, document_version="1.1", now=frozen_issue_time))

    assert document == expected


def test_a_document_over_300kb_is_refused(world, books, monkeypatch):
    invoice = approved_invoice(world, books)
    monkeypatch.setattr("myinvois.document.MAX_DOCUMENT_BYTES", 10)

    with pytest.raises(InvalidDocument) as refused:
        map_invoice(invoice, document_version="1.1", now=invoice.approved_at)

    assert "size" in str(refused.value)


def test_the_300kb_limit_is_the_stricter_decimal_reading_not_a_binary_one(world, books):
    # Torres's review (NB-c on PR #191): nothing else pins this value, so reverting it to the looser 300 * 1024
    # left every other test green. LHDN's own wording ("300 KB maximum per e-Invoice") wins over the binary
    # reading (document.py's own comment on this constant).
    assert document.MAX_DOCUMENT_BYTES == 300_000


# No test here reaches LHDN's 50-character invoice-number limit (the other half of this ticket's size/length
# acceptance criterion): `Invoice.number` is `CharField(max_length=20)` (D62), so a number that long can never
# exist to map. The rule is still enforced and already covered, at the field_rules layer where it is checked
# (`test_document_rules.py::test_an_invoice_number_may_be_50_characters_and_no_longer`), and this mapper's own
# tests confirm `invoice_number` reaches that check (every passing test above calls it without error).
