"""Drafting rules (ticket #44, 07a; D17, D44, D59 (1), (10)), tested through `core.invoices`.

The pages are tested in test_invoice_screens.py and the database guards in test_invoice_database.py.
"""

from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import translation

from core import customers, invoices, items
from core.models import Invoice, InvoiceLine

from .test_customers import BRN_VALUE, TIN, switch_einvoicing_on

OTHERS, ONE, NOT_APPLICABLE = "022", "C62", "06"


@pytest.fixture(autouse=True)
def english():
    """These tests read the messages in English. A request test elsewhere can leave another language active on the thread."""
    with translation.override("en"):
        yield


@pytest.fixture
def stock(world):
    owner, acme = world["acme_owner"], world["acme"]
    return {
        "customer": customers.create_customer(owner, acme, legal_name="Buyer Sdn Bhd"),
        "item": items.create_item(
            owner, acme, name="Widget", classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
        ),
    }


def line(stock, quantity="1", unit_price="5", **overrides):
    return {"item": stock["item"].pk, "quantity": quantity, "unit_price": unit_price, **overrides}


def draft(world, stock, *lines, user="acme_owner"):
    return invoices.create_draft(world[user], world["acme"], customer_id=stock["customer"].pk, lines=list(lines) or [line(stock)])


def test_a_draft_keeps_amounts_exactly_as_typed_and_has_no_number(world, stock):
    saved = draft(world, stock, line(stock, "3", "0.1"), line(stock, "0.0001", "99999999999999.9999"))

    saved.refresh_from_db()
    assert saved.number is None and saved.status == Invoice.DRAFT
    stored = [(row.quantity, row.unit_price) for row in InvoiceLine.objects.filter(invoice=saved)]
    assert stored == [(Decimal("3"), Decimal("0.1")), (Decimal("0.0001"), Decimal("99999999999999.9999"))]
    assert all(isinstance(value, Decimal) for pair in stored for value in pair)
    # The sum is exact, which a float could not promise: 3 x 0.1.
    assert sum(q * p for q, p in stored[:1]) == Decimal("0.3")


def test_a_float_is_refused_rather_than_rounded(world, stock):
    with pytest.raises(TypeError):
        draft(world, stock, line(stock, 2.5, "1"))
    assert not Invoice.objects.exists()


@pytest.mark.parametrize("bad", ["0", "0.0000", "-1", "abc", "", "1e3", "1.23456", "NaN", "Infinity", "1" * 15, "1_0", " ", "١٢"])
def test_a_quantity_must_be_above_zero_and_fit_the_column(world, stock, bad):
    with pytest.raises(invoices.InvalidInvoice) as refused:
        draft(world, stock, line(stock), line(stock, quantity=bad))

    assert "quantity" in str(refused.value) and "line 2" in str(refused.value)
    assert not Invoice.objects.exists()


@pytest.mark.parametrize("bad", ["-1", "-0.01", "abc", "", "1e3", "1.23456", "NaN", "Infinity", "1" * 15])
def test_a_unit_price_may_be_zero_but_not_negative_or_malformed(world, stock, bad):
    with pytest.raises(invoices.InvalidInvoice) as refused:
        draft(world, stock, line(stock, unit_price=bad))

    assert "unit price" in str(refused.value) and "line 1" in str(refused.value)
    assert not Invoice.objects.exists()


def test_a_free_line_is_a_valid_line(world, stock):
    assert draft(world, stock, line(stock, unit_price="0")).lines.get().unit_price == Decimal("0")


def test_an_error_never_repeats_what_was_typed(world, stock):
    with pytest.raises(invoices.InvalidInvoice) as refused:
        draft(world, stock, line(stock, unit_price="-98765.4321"))

    assert "98765" not in str(refused.value)


def test_a_draft_needs_a_customer_and_at_least_one_line(world, stock):
    no_lines = {"customer_id": stock["customer"].pk, "lines": []}
    no_customer = {"customer_id": "", "lines": [line(stock)]}
    unknown_customer = {"customer_id": "999999", "lines": [line(stock)]}
    for form in (no_lines, no_customer, unknown_customer):
        with pytest.raises(invoices.InvalidInvoice):
            invoices.create_draft(world["acme_owner"], world["acme"], **form)

    assert not Invoice.objects.exists()


def test_a_deactivated_customer_or_item_cannot_start_a_draft(world, stock):
    customers.deactivate_customer(world["acme_owner"], stock["customer"])
    with pytest.raises(invoices.InvalidInvoice):
        draft(world, stock)
    customers.reactivate_customer(world["acme_owner"], stock["customer"])
    items.deactivate_item(world["acme_owner"], stock["item"])
    with pytest.raises(invoices.InvalidInvoice):
        draft(world, stock)

    assert not Invoice.objects.exists()


def test_a_draft_may_keep_what_was_deactivated_after_it_was_started_but_not_add_such_things(world, stock):
    saved = draft(world, stock)
    other = items.create_item(
        world["acme_owner"], world["acme"], name="Other", classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
    )
    customers.deactivate_customer(world["acme_owner"], stock["customer"])
    items.deactivate_item(world["acme_owner"], stock["item"])
    items.deactivate_item(world["acme_owner"], other)

    invoices.update_draft(world["acme_owner"], saved, customer_id=stock["customer"].pk, lines=[line(stock, "4", "6")])
    assert saved.lines.get().quantity == Decimal("4") and saved.customer == stock["customer"]

    with pytest.raises(invoices.InvalidInvoice):
        invoices.update_draft(
            world["acme_owner"], saved, customer_id=stock["customer"].pk, lines=[line(stock), line(stock, item=other.pk)]
        )
    assert saved.lines.count() == 1


def test_a_draft_cannot_use_another_companys_customer_or_item(world, stock):
    beta_customer = customers.create_customer(world["beta_owner"], world["beta"], legal_name="Beta Buyer")
    beta_item = items.create_item(
        world["beta_owner"], world["beta"], name="Beta thing", classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
    )

    with pytest.raises(invoices.InvalidInvoice):
        invoices.create_draft(world["acme_owner"], world["acme"], customer_id=beta_customer.pk, lines=[line(stock)])
    with pytest.raises(invoices.InvalidInvoice):
        draft(world, stock, line(stock, item=beta_item.pk))

    assert not Invoice.objects.exists()


def test_a_failed_edit_leaves_the_draft_as_it_was(world, stock):
    saved = draft(world, stock, line(stock, "2", "3"))

    with pytest.raises(invoices.InvalidInvoice):
        invoices.update_draft(world["acme_owner"], saved, customer_id=stock["customer"].pk, lines=[line(stock), line(stock, "0")])

    assert [(r.quantity, r.unit_price) for r in saved.lines.all()] == [(Decimal("2"), Decimal("3"))]


def test_deleting_a_draft_removes_its_lines_and_takes_no_number(world, stock):
    saved = draft(world, stock, line(stock), line(stock, "2"))

    invoices.delete_draft(world["acme_staff"], saved)

    assert not Invoice.objects.exists() and not InvoiceLine.objects.exists()


def test_only_the_owner_and_staff_of_the_company_may_draft_edit_or_delete(world, stock):
    saved = draft(world, stock)
    for who in ("accountant", "beta_owner"):
        with pytest.raises(PermissionDenied):
            draft(world, stock, user=who)
        with pytest.raises(PermissionDenied):
            invoices.update_draft(world[who], saved, customer_id=stock["customer"].pk, lines=[line(stock, "9")])
        with pytest.raises(PermissionDenied):
            invoices.delete_draft(world[who], saved)

    assert Invoice.objects.count() == 1 and saved.lines.get().quantity == Decimal("1")
    draft(world, stock, user="acme_staff")
    assert Invoice.objects.count() == 2


def test_a_customer_saved_without_an_identity_cannot_be_drafted_once_einvoicing_is_on(world, stock):
    # The customer master checks the identity when a customer is saved, so this one was saved while e-invoicing was off.
    started = draft(world, stock)
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    with pytest.raises(invoices.InvalidInvoice) as new:
        draft(world, stock)
    with pytest.raises(invoices.InvalidInvoice) as edit:
        invoices.update_draft(world["acme_owner"], started, customer_id=stock["customer"].pk, lines=[line(stock, "4")])

    for refused in (new, edit):
        assert "TIN" in str(refused.value) and "e-invoicing" in str(refused.value)
    assert Invoice.objects.count() == 1 and started.lines.get().quantity == Decimal("1")


def test_a_customer_with_a_full_identity_can_be_drafted_while_einvoicing_is_on(world, stock):
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    identified = customers.create_customer(
        world["acme_owner"], world["acme"], legal_name="Identified Sdn Bhd", tin=TIN, id_type="BRN", id_value=BRN_VALUE
    )

    saved = invoices.create_draft(world["acme_owner"], world["acme"], customer_id=identified.pk, lines=[line(stock)])

    assert saved.customer == identified


def test_one_retained_line_of_a_deactivated_item_cannot_become_two(world, stock):
    saved = draft(world, stock)
    items.deactivate_item(world["acme_owner"], stock["item"])

    with pytest.raises(invoices.InvalidInvoice) as refused:
        invoices.update_draft(
            world["acme_owner"], saved, customer_id=stock["customer"].pk, lines=[line(stock), line(stock, "2")]
        )

    assert "line 2" in str(refused.value)
    assert saved.lines.count() == 1
