"""The database holds the draft rules whoever writes (ticket #44, D11, D17, D44; migration 0041).

These tests skip the application layer on purpose: they write with the ORM straight to the tables, as the web role, as
the worker role or as the table owner, and show that the database itself refuses what must never happen.
"""

from decimal import Decimal

import pytest
from django.db import DatabaseError, connection, transaction

from core import customers, invoices, items
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
        world["acme_owner"], world["acme"], customer_id=acme_customer.pk, lines=[{"item": acme_item.pk, "quantity": "1", "unit_price": "5"}]
    )
    beta_draft = invoices.create_draft(
        world["beta_owner"], world["beta"], customer_id=beta_customer.pk, lines=[{"item": beta_item.pk, "quantity": "1", "unit_price": "5"}]
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
    assert columns["core_invoice.number"].endswith(":YES")


def test_the_database_refuses_a_numbered_draft_an_unknown_status_and_bad_amounts(world, books):
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(number="INV-1"))
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(status="approved"))
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
                company=world[theirs], invoice=books[f"{theirs}_draft"], item=books[f"{theirs}_item"], quantity=1, unit_price=1))
    assert Invoice.objects.count() == 2 and InvoiceLine.objects.count() == 2


def test_an_invoice_cannot_name_another_companys_customer_or_item_even_for_its_own_company(world, books):
    # No user is in play, as for an operator command: the guards hold for every writer.
    attempt(lambda: Invoice.objects.create(company=world["acme"], customer=books["beta_customer"]))
    attempt(lambda: Invoice.objects.filter(pk=books["acme_draft"].pk).update(customer=books["beta_customer"]))
    attempt(lambda: InvoiceLine.objects.create(
        company=world["acme"], invoice=books["acme_draft"], item=books["beta_item"], quantity=1, unit_price=1))
    attempt(lambda: InvoiceLine.objects.create(
        company=world["beta"], invoice=books["acme_draft"], item=books["beta_item"], quantity=1, unit_price=1))
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
            company=world["acme"], invoice=books["acme_draft"], item=books["acme_item"], quantity=1, unit_price=1))
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
    # No code can make a non-draft invoice yet (approval is ticket #46). Lift the status constraint for this test only:
    # DDL is transactional in PostgreSQL, so the test's rollback restores it.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # run the deferred foreign-key checks, or ALTER TABLE is refused
        cursor.execute("ALTER TABLE core_invoice DROP CONSTRAINT invoice_status_known")
        cursor.execute("UPDATE core_invoice SET status = 'approved' WHERE id = %s", [books["acme_draft"].pk])
    invoice, line = books["acme_draft"], books["acme_draft"].lines.get()

    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(customer=books["acme_customer"]))
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).delete())
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).update(quantity=2))
    attempt(lambda: InvoiceLine.objects.filter(pk=line.pk).delete())
    attempt(lambda: InvoiceLine.objects.create(
        company=world["acme"], invoice=invoice, item=books["acme_item"], quantity=1, unit_price=1))
    # The service says so before the database has to (the page answers 403).
    with pytest.raises(invoices.InvoiceLocked):
        invoices.delete_draft(world["acme_owner"], invoice)
    with pytest.raises(invoices.InvoiceLocked):
        invoices.update_draft(world["acme_owner"], invoice, customer_id=books["acme_customer"].pk, lines=[])
    assert Invoice.objects.filter(pk=invoice.pk).exists() and InvoiceLine.objects.filter(pk=line.pk).exists()
