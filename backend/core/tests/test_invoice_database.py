"""The database holds the draft rules whoever writes (ticket #44, D11, D17, D44; migration 0041).

These tests skip the application layer on purpose: they write with the ORM straight to the tables, as the web role, as
the worker role or as the table owner, and show that the database itself refuses what must never happen.
"""

from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction

from core import customers, invoices, items, numbering
from core.models import Invoice, InvoiceLine

OTHERS, ONE, NOT_APPLICABLE = "022", "C62", "06"


def attempt(write):
    """Run a write the database must refuse, inside a savepoint so the test can go on."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            write()


@pytest.fixture
def books(world):
    def buyer_and_item(owner, company, name):
        return (
            customers.create_customer(owner, company, legal_name=f"{name} buyer"),
            items.create_item(owner, company, name=f"{name} service", classification_code=OTHERS, unit_code=ONE,
                              default_tax_type=NOT_APPLICABLE),
        )

    acme_customer, acme_item = buyer_and_item(world["acme_owner"], world["acme"], "Acme")
    beta_customer, beta_item = buyer_and_item(world["beta_owner"], world["beta"], "Beta")
    acme_draft = invoices.create_draft(
        world["acme_owner"], world["acme"], customer_id=acme_customer.pk,
        lines=[{"item": acme_item.pk, "quantity": "1", "unit_price": "5", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )
    beta_draft = invoices.create_draft(
        world["beta_owner"], world["beta"], customer_id=beta_customer.pk,
        lines=[{"item": beta_item.pk, "quantity": "1", "unit_price": "5", "tax_type": NOT_APPLICABLE, "tax_rate": "0"}],
    )
    return {
        "acme_customer": acme_customer, "acme_item": acme_item, "acme_draft": acme_draft,
        "beta_customer": beta_customer, "beta_item": beta_item, "beta_draft": beta_draft,
    }


def query(sql, *params):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall()


def test_amounts_are_numeric_columns_and_a_draft_number_may_be_empty(db):
    columns = dict(
        query(
            "SELECT table_name || '.' || column_name, data_type || ':' || is_nullable FROM information_schema.columns "
            "WHERE table_name IN ('core_invoice', 'core_invoiceline')"
        )
    )

    assert columns["core_invoiceline.quantity"] == "numeric:NO"
    assert columns["core_invoiceline.unit_price"] == "numeric:NO"
    assert columns["core_invoiceline.tax_rate"] == "numeric:NO"
    assert columns["core_invoice.number"].endswith(":YES")


def test_a_tax_rate_outside_0_to_100_is_refused_by_the_database_itself(world, books):
    line = books["acme_draft"].lines.get()
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).update(tax_rate=Decimal("-0.01")))
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).update(tax_rate=Decimal("100.01")))


def test_the_database_refuses_a_currency_and_rate_that_do_not_match(world, books):
    # D24, ticket #53: MYR carries no rate and a foreign currency always has a positive one.
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(currency="USD"))  # no rate at all
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(currency="USD", exchange_rate=Decimal("0")))
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(exchange_rate=Decimal("4.47")))  # MYR, a rate
    assert Invoice.objects.get(pk=books["acme_draft"].pk).currency == "MYR"


def test_the_database_refuses_a_numbered_draft_an_unknown_status_and_bad_amounts(world, books):
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(number="INV-1"))
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(status="approved"))  # no number, buyer or time
    line = books["acme_draft"].lines.get()
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).update(quantity=Decimal("0")))
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).update(unit_price=Decimal("-0.01")))
    assert Invoice.objects.get(pk=books["acme_draft"].pk).number is None


def test_another_companys_invoices_are_invisible_and_unwritable_for_every_role(world, books, as_user):
    for who in ("beta_owner", "acme_owner"):
        mine, theirs = ("beta", "acme") if who == "beta_owner" else ("acme", "beta")
        with as_user(world[who]):
            assert [i.pk for i in Invoice.objects.all()] == [books[f"{mine}_draft"].pk]
            assert InvoiceLine.objects.count() == 1
            assert Invoice.objects.filter(pk=books[f"{theirs}_draft"].pk).update(status="draft") == 0
            assert Invoice.objects.filter(pk=books[f"{theirs}_draft"].pk).delete() == (0, {})
            attempt(lambda: Invoice.objects.create(company=world[theirs], customer=books[f"{theirs}_customer"]))
            attempt(lambda: InvoiceLine.objects.create(
                company=world[theirs], invoice=books[f"{theirs}_draft"], item=books[f"{theirs}_item"], quantity=1,
                unit_price=1, tax_type=NOT_APPLICABLE, tax_rate=0))
    assert Invoice.objects.count() == 2 and InvoiceLine.objects.count() == 2


def test_an_invoice_cannot_name_another_companys_customer_or_item_even_for_its_own_company(world, books):
    # No user is in play, as for an operator command: the guards hold for every writer.
    attempt(lambda: Invoice.objects.create(company=world["acme"], customer=books["beta_customer"]))
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(customer=books["beta_customer"]))
    attempt(lambda: InvoiceLine.objects.create(
        company=world["acme"], invoice=books["acme_draft"], item=books["beta_item"], quantity=1, unit_price=1,
        tax_type=NOT_APPLICABLE, tax_rate=0))
    attempt(lambda: InvoiceLine.objects.create(
        company=world["beta"], invoice=books["acme_draft"], item=books["beta_item"], quantity=1, unit_price=1,
        tax_type=NOT_APPLICABLE, tax_rate=0))
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(company=world["beta"]))
    attempt(lambda: InvoiceLine.objects.filter(invoice=books["acme_draft"]).update(invoice=books["beta_draft"]))
    assert books["acme_draft"].lines.count() == 1 and books["acme_draft"].customer == books["acme_customer"]


def test_an_accountant_and_a_stranger_cannot_write_drafts_but_staff_can(world, books, as_user):
    def new_invoice():
        return Invoice.objects.create(company=world["acme"], customer=books["acme_customer"])

    with as_user(world["accountant"]):
        assert Invoice.objects.filter(company=world["acme"]).count() == 1  # may read
        attempt(new_invoice)
        attempt(lambda: InvoiceLine.objects.filter(pk=books["acme_draft"].lines.get().pk).update(quantity=2))
        attempt(lambda: InvoiceLine.objects.create(
            company=world["acme"], invoice=books["acme_draft"], item=books["acme_item"], quantity=1, unit_price=1,
            tax_type=NOT_APPLICABLE, tax_rate=0))
    with as_user(world["acme_staff"]):
        new_invoice()
    assert Invoice.objects.filter(company=world["acme"]).count() == 2


def test_job_handlers_read_the_invoices_of_their_company_only_and_never_write(world, books, as_worker_connection):
    with as_worker_connection():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, false)", [str(world["acme"].pk)])
            cursor.execute("SELECT id FROM core_invoice")
            assert cursor.fetchall() == [(books["acme_draft"].pk,)]
        attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(status="draft"))
        attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).delete())
        attempt(lambda: InvoiceLine.objects.filter(invoice=books["acme_draft"]).update(quantity=3))


def test_an_invoice_that_has_left_the_draft_state_is_locked_with_its_lines(world, books):
    numbering.configure_sequence(world["acme"], numbering.INVOICE, prefix="INV-", start_number=1)
    invoice = invoices.approve_invoice(world["acme_owner"], books["acme_draft"])
    line = invoice.lines.get()

    other = customers.create_customer(world["acme_owner"], world["acme"], legal_name="Another buyer")
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(customer=other))
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).delete())
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).update(quantity=2))
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).delete())
    attempt(lambda: InvoiceLine.objects.create(
        company=world["acme"], invoice=invoice, item=books["acme_item"], quantity=1, unit_price=1,
        tax_type=NOT_APPLICABLE, tax_rate=0))
    # The service says so before the database has to (the page answers 403).
    with pytest.raises(invoices.InvoiceLocked):
        invoices.delete_draft(world["acme_owner"], invoice)
    with pytest.raises(invoices.InvoiceLocked):
        invoices.update_draft(world["acme_owner"], invoice, customer_id=books["acme_customer"].pk, lines=[])
    assert Invoice.objects.filter(pk=invoice.pk).exists() and InvoiceLine.objects.filter(pk=line.pk).exists()


def test_an_approved_invoices_currency_and_rate_are_frozen_with_everything_else(world, books):
    # No new trigger code for this (migration 0051): invoice_guard() already diffs the whole row once approved.
    numbering.configure_sequence(world["acme"], numbering.INVOICE, prefix="INV-", start_number=1)
    invoice = invoices.approve_invoice(world["acme_owner"], books["acme_draft"])

    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(currency="USD", exchange_rate=Decimal("4.47")))
    assert Invoice.objects.get(pk=invoice.pk).currency == "MYR"


def test_an_accountant_cannot_delete_a_draft_or_a_line_through_the_web_role_but_staff_can(world, books, as_user):
    # Row-level security lets any member of the company delete a row, so the guards must refuse the accountant.
    empty = Invoice.objects.create(company=world["acme"], customer=books["acme_customer"])
    line = books["acme_draft"].lines.get()

    def raw_delete(table, pk):
        with connection.cursor() as cursor:
            cursor.execute(f"DELETE FROM {table} WHERE id = %s", [pk])

    with as_user(world["accountant"]):
        attempt(lambda: raw_delete("core_invoice", empty.pk))
        attempt(lambda: raw_delete("core_invoiceline", line.pk))
        attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).delete())
    assert Invoice.objects.filter(pk=empty.pk).exists() and InvoiceLine.objects.filter(pk=line.pk).exists()

    with as_user(world["acme_staff"]):
        raw_delete("core_invoice", empty.pk)
        raw_delete("core_invoiceline", line.pk)
    assert not Invoice.objects.filter(pk=empty.pk).exists() and not InvoiceLine.objects.filter(pk=line.pk).exists()
