"""Owner approval, the buyer snapshot and the lock (ticket #46, 07c; D17, D19, D44, D59 (5), D60 (2), D62).

Approval is tested through `core.invoices.approve_invoice`; the screens are in test_invoice_screens.py. The database
rules (unique number, the locked row) write with the ORM straight to the tables, as in test_invoice_database.py.
"""

from datetime import date, timedelta

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.utils import timezone

from core import customers, einvoicing, invoices, items, numbering
from core.models import AuditEntry, CompanyEInvoiceSetting, Invoice

from .test_customers import BRN_VALUE, CONTACT, TIN, switch_einvoicing_on
from .test_invoice_screens import sign_in

OTHERS, ONE, NOT_APPLICABLE = "022", "C62", "06"


@pytest.fixture
def books(world):
    owner, acme = world["acme_owner"], world["acme"]
    numbering.configure_sequence(acme, numbering.INVOICE, prefix="INV-", start_number=1)
    return {
        "customer": customers.create_customer(
            owner, acme, legal_name="Buyer Sdn Bhd", tin=TIN, id_type="BRN", id_value=BRN_VALUE, **CONTACT
        ),
        "item": items.create_item(
            owner, acme, name="Widget", classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
        ),
    }


def new_draft(world, books, customer=None):
    return invoices.create_draft(
        world["acme_owner"],
        world["acme"],
        customer_id=(customer or books["customer"]).pk,
        lines=[{"item": books["item"].pk, "quantity": "2", "unit_price": "5"}],
    )


def approve(world, invoice, user="acme_owner", **kwargs):
    return invoices.approve_invoice(world[user], invoice, **kwargs)


def attempt(write):
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            write()


def next_number(company):
    return numbering.DocumentSequence.objects.get(company=company, document_type=numbering.INVOICE).next_number


# --- who may approve, and what approval records ----------------------------------------------------------------------


def test_the_owner_approves_and_the_invoice_takes_the_next_number(world, books):
    invoice = approve(world, new_draft(world, books))

    invoice.refresh_from_db()
    assert (invoice.status, invoice.number, invoice.document_type) == ("approved", "INV-1", "invoice")
    assert invoice.approved_by == world["acme_owner"] and invoice.approved_at is not None
    assert approve(world, new_draft(world, books)).number == "INV-2"


@pytest.mark.parametrize("who", ["acme_staff", "accountant", "beta_owner"])
def test_staff_an_accountant_and_another_companys_owner_cannot_approve(world, books, who):
    draft = new_draft(world, books)

    with pytest.raises(PermissionDenied):
        approve(world, draft, user=who)

    draft.refresh_from_db()
    assert (draft.status, draft.number) == ("draft", None)
    assert next_number(world["acme"]) == 1  # no number was used


def test_the_database_itself_refuses_staff_an_approval_written_straight_to_the_table(world, books, as_user):
    draft = new_draft(world, books)
    with as_user(world["acme_staff"]):
        attempt(
            lambda: Invoice.objects.filter(pk=draft.pk).update(
                status="approved", number="INV-1", buyer_legal_name="X", approved_at=timezone.now(), is_einvoice=False
            )
        )
    assert Invoice.objects.get(pk=draft.pk).status == "draft"


def test_approval_records_who_and_when_in_the_audit_trail(world, books):
    invoice = approve(world, new_draft(world, books))

    entry = AuditEntry.objects.get(company=world["acme"], action="approved")
    assert (entry.actor_id, entry.target_type, entry.target_id) == (world["acme_owner"].pk, "invoice", str(invoice.pk))
    assert entry.occurred_at is not None
    assert "INV-1" not in str(entry.changed_fields) and "Buyer" not in str(entry.changed_fields)


# --- the buyer snapshot and the lock ---------------------------------------------------------------------------------


def test_the_buyer_is_copied_at_approval_and_later_customer_edits_never_change_it(world, books):
    invoice = approve(world, new_draft(world, books))

    customers.update_customer(world["acme_owner"], books["customer"], legal_name="Renamed Sdn Bhd", city="Ipoh")

    invoice.refresh_from_db()
    assert (invoice.buyer_legal_name, invoice.buyer_tin, invoice.buyer_id_type, invoice.buyer_id_value) == (
        "Buyer Sdn Bhd", TIN, "BRN", BRN_VALUE
    )
    assert (invoice.buyer_phone, invoice.buyer_sst_number, invoice.buyer_city) == (
        CONTACT["phone"], CONTACT["sst_number"], "Petaling Jaya"
    )
    assert (invoice.buyer_address_line1, invoice.buyer_postal_zone, invoice.buyer_state_code, invoice.buyer_country_code) == (
        "2 Buyer Road", "47400", "10", "MYS"
    )


def test_an_approved_invoice_can_no_longer_be_edited_or_deleted(world, books):
    invoice = approve(world, new_draft(world, books))

    with pytest.raises(invoices.InvoiceLocked):
        invoices.update_draft(world["acme_owner"], invoice, customer_id=books["customer"].pk, lines=[])
    with pytest.raises(invoices.InvoiceLocked):
        invoices.delete_draft(world["acme_owner"], invoice)
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(buyer_legal_name="Someone Else"))
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(number="INV-99"))
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(is_einvoice=True))
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).update(status="draft"))
    attempt(lambda: Invoice.objects.filter(pk=invoice.pk).delete())
    assert Invoice.objects.get(pk=invoice.pk).buyer_legal_name == "Buyer Sdn Bhd"


# --- numbering: one transaction, one unique number -------------------------------------------------------------------


def test_a_failed_approval_consumes_no_number(world, books, monkeypatch):
    draft = new_draft(world, books)

    def fail(*args, **kwargs):
        raise RuntimeError("the audit write failed after the number was taken")

    monkeypatch.setattr("core.audit.record", fail)
    with pytest.raises(RuntimeError):
        approve(world, draft)
    monkeypatch.undo()

    draft.refresh_from_db()
    assert (draft.status, draft.number) == ("draft", None) and next_number(world["acme"]) == 1
    assert approve(world, draft).number == "INV-1"


def approved_row(number, user):
    return dict(
        status="approved", number=number, buyer_legal_name="X", approved_at=timezone.now(), approved_by=user, is_einvoice=False
    )


def test_a_number_can_never_appear_twice_for_a_company_but_may_in_two_companies(world, books):
    first = approve(world, new_draft(world, books))
    second = new_draft(world, books)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Invoice.objects.filter(pk=second.pk).update(**approved_row(first.number, world["acme_owner"]))
    other_customer = customers.create_customer(world["beta_owner"], world["beta"], legal_name="Other")
    other = Invoice.objects.create(company=world["beta"], customer=other_customer)
    Invoice.objects.filter(pk=other.pk).update(**approved_row(first.number, world["beta_owner"]))  # a different company: allowed
    assert Invoice.objects.get(pk=other.pk).number == first.number


def test_the_unique_number_is_per_document_type(world, books):
    constraint = [c for c in Invoice._meta.constraints if getattr(c, "fields", None)]
    assert [tuple(c.fields) for c in constraint] == [("company", "document_type", "number")]


def test_approving_again_after_72_hours_keeps_the_number_and_never_takes_a_new_one(world, books, monkeypatch):
    invoice = approve(world, new_draft(world, books))
    Invoice.objects.filter(pk=invoice.pk).update(approved_at=timezone.now() - timedelta(hours=73))
    monkeypatch.setattr(numbering, "allocate_number", lambda *a, **k: pytest.fail("a new number was taken"))

    again = approve(world, Invoice.objects.get(pk=invoice.pk))

    again.refresh_from_db()
    assert again.number == "INV-1" and again.status == "approved"
    assert timezone.now() - again.approved_at < timedelta(minutes=1)  # the issue time is fresh
    assert next_number(world["acme"]) == 2
    assert AuditEntry.objects.filter(company=world["acme"], action="approved").count() == 2


def test_cancelling_keeps_the_number_and_never_calls_the_numbering_service(world, books, monkeypatch):
    invoice = approve(world, new_draft(world, books))
    monkeypatch.setattr(numbering, "allocate_number", lambda *a, **k: pytest.fail("numbering was called"))

    invoices.cancel_invoice(world["acme_owner"], invoice)

    invoice.refresh_from_db()
    assert (invoice.status, invoice.number) == ("cancelled", "INV-1")
    assert next_number(world["acme"]) == 2
    with pytest.raises(invoices.InvoiceLocked):
        approve(world, invoice)  # a cancelled invoice is not approved again
    with pytest.raises(PermissionDenied):
        invoices.cancel_invoice(world["acme_staff"], Invoice.objects.get(pk=invoice.pk))


def test_a_company_without_a_sequence_cannot_approve(world):
    customer = customers.create_customer(world["acme_owner"], world["acme"], legal_name="Buyer")
    item = items.create_item(
        world["acme_owner"], world["acme"], name="W", classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
    )
    draft = invoices.create_draft(
        world["acme_owner"], world["acme"], customer_id=customer.pk, lines=[{"item": item.pk, "quantity": "1", "unit_price": "1"}]
    )

    with pytest.raises(numbering.SequenceNotConfigured):
        approve(world, draft)
    assert Invoice.objects.get(pk=draft.pk).status == "draft"


# --- the route: e-invoice or normal invoice (D59 (3), (5), (7)) ------------------------------------------------------


def test_with_einvoicing_off_the_invoice_is_normal_and_the_buyer_needs_only_a_name(world, books):
    bare = customers.create_customer(world["acme_owner"], world["acme"], legal_name="Walk-in Buyer")

    invoice = approve(world, new_draft(world, books, bare))

    invoice.refresh_from_db()
    assert invoice.is_einvoice is False and invoice.buyer_legal_name == "Walk-in Buyer" and invoice.buyer_tin == ""
    assert invoice.acknowledged_at is None


def test_with_einvoicing_on_the_invoice_is_an_einvoice_with_a_complete_buyer_snapshot(world, books):
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    invoice = approve(world, new_draft(world, books))

    invoice.refresh_from_db()
    assert invoice.is_einvoice is True and invoice.buyer_tin == TIN


def test_the_route_is_locked_when_the_setting_changes_later(world, books):
    invoice = approve(world, new_draft(world, books))
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    assert Invoice.objects.get(pk=invoice.pk).is_einvoice is False


def test_an_incomplete_buyer_is_refused_rather_than_snapshotted_blank(world, books):
    bare = customers.create_customer(world["acme_owner"], world["acme"], legal_name="Walk-in Buyer")
    draft = new_draft(world, books, bare)  # drafted while e-invoicing was off
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    with pytest.raises(invoices.InvalidInvoice) as refused:
        approve(world, draft)

    assert "TIN" in str(refused.value) and "Walk-in" not in str(refused.value)
    draft.refresh_from_db()
    assert (draft.status, draft.number) == ("draft", None) and next_number(world["acme"]) == 1


def test_an_incomplete_supplier_is_refused_rather_than_snapshotted_blank(world, books):
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    draft = new_draft(world, books)
    # A setting saved before #172 can hold a blank name and phone; the service would not save one now.
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # run the deferred checks, or ALTER TABLE is refused
        cursor.execute("ALTER TABLE core_companyeinvoicesetting DISABLE TRIGGER USER")
        CompanyEInvoiceSetting.objects.filter(company=world["acme"]).update(phone="", legal_name="")
        cursor.execute("ALTER TABLE core_companyeinvoicesetting ENABLE TRIGGER USER")

    with pytest.raises(invoices.InvalidInvoice) as refused:
        approve(world, draft)

    assert "phone number" in str(refused.value) and "registered name" in str(refused.value)
    assert "+60312345678" not in str(refused.value)
    assert Invoice.objects.get(pk=draft.pk).status == "draft" and next_number(world["acme"]) == 1


def test_a_normal_invoice_for_a_mandatory_company_needs_an_acknowledgement_that_is_recorded(world, books):
    einvoicing.update_setting(
        world["acme_owner"], world["acme"], einvoicing_enabled=False, status="mandatory", effective_from=date(2020, 1, 1)
    )
    draft = new_draft(world, books)

    with pytest.raises(einvoicing.AcknowledgementRequired):
        approve(world, draft)
    assert Invoice.objects.get(pk=draft.pk).status == "draft" and next_number(world["acme"]) == 1

    invoice = approve(world, draft, acknowledged=True)

    invoice.refresh_from_db()
    assert invoice.is_einvoice is False and invoice.acknowledged_by == world["acme_owner"]
    assert invoice.acknowledged_at is not None and invoice.acknowledged_version == einvoicing.ACKNOWLEDGEMENT_VERSION


def test_a_voluntary_company_needs_no_acknowledgement_for_a_normal_invoice(world, books):
    einvoicing.update_setting(
        world["acme_owner"], world["acme"], einvoicing_enabled=False, status="voluntary", effective_from=date(2020, 1, 1)
    )

    assert approve(world, new_draft(world, books)).acknowledged_at is None


# --- the screens -----------------------------------------------------------------------------------------------------


def page_url(company, invoice, *rest):
    return "/".join([f"/companies/{company.pk}/invoices", str(invoice.pk), *rest, ""])


def test_the_owner_approves_over_http_and_the_page_then_shows_the_number_and_the_locked_buyer(
    client, world, books, as_production_connection
):
    draft = new_draft(world, books)
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        approved = client.post(page_url(world["acme"], draft, "approve"))
    customers.update_customer(world["acme_owner"], books["customer"], legal_name="Renamed Sdn Bhd")
    with as_production_connection():
        page = client.get(page_url(world["acme"], draft)).content.decode()
        listing = client.get(f"/companies/{world['acme'].pk}/invoices/").content.decode()
        edit = client.post(page_url(world["acme"], draft), {"customer": books["customer"].pk})
        delete = client.post(page_url(world["acme"], draft, "delete"))

    assert approved.status_code == 302 and Invoice.objects.get(pk=draft.pk).number == "INV-1"
    assert "INV-1" in page and "Buyer Sdn Bhd" in page and "Renamed" not in page
    assert "Save draft" not in page and "/approve/" not in page  # the lock: no edit form, nothing left to approve
    assert "INV-1" in listing and "Approved" in listing
    assert edit.status_code == 403 and delete.status_code == 403


@pytest.mark.parametrize("who", ["acme_staff", "accountant"])
def test_staff_and_an_accountant_see_no_approve_button_and_are_refused_over_http(
    client, world, books, as_production_connection, who
):
    draft = new_draft(world, books)
    with as_production_connection():
        sign_in(client, world[who])
        page = client.get(page_url(world["acme"], draft)).content.decode()
        refused = client.post(page_url(world["acme"], draft, "approve"))

    assert "/approve/" not in page and refused.status_code == 403
    assert Invoice.objects.get(pk=draft.pk).status == "draft"


def test_the_owner_sees_the_approve_button_on_a_draft(client, world, books, as_production_connection):
    draft = new_draft(world, books)
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        page = client.get(page_url(world["acme"], draft)).content.decode()

    assert page_url(world["acme"], draft, "approve") in page


def test_an_incomplete_buyer_shows_the_reason_and_approves_nothing(client, world, books, as_production_connection):
    bare = customers.create_customer(world["acme_owner"], world["acme"], legal_name="Walk-in Buyer")
    draft = new_draft(world, books, bare)
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        refused = client.post(page_url(world["acme"], draft, "approve"))

    assert refused.status_code == 200 and "TIN" in refused.content.decode()
    assert Invoice.objects.get(pk=draft.pk).status == "draft" and next_number(world["acme"]) == 1


def test_a_mandatory_company_must_tick_the_acknowledgement_to_issue_a_normal_invoice(
    client, world, books, as_production_connection
):
    einvoicing.update_setting(
        world["acme_owner"], world["acme"], einvoicing_enabled=False, status="mandatory", effective_from=date(2020, 1, 1)
    )
    draft = new_draft(world, books)
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        asked = client.post(page_url(world["acme"], draft, "approve"))
    assert Invoice.objects.get(pk=draft.pk).status == "draft"
    with as_production_connection():
        done = client.post(page_url(world["acme"], draft, "approve"), {"acknowledged": "1"})

    assert asked.status_code == 200 and str(einvoicing.ACKNOWLEDGEMENT_TEXT) in asked.content.decode()
    assert done.status_code == 302 and Invoice.objects.get(pk=draft.pk).acknowledged_at is not None


def test_another_companys_owner_cannot_approve_this_companys_draft_over_http(client, world, books, as_production_connection):
    draft = new_draft(world, books)
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        refused = client.post(page_url(world["acme"], draft, "approve"))

    assert refused.status_code in (403, 404) and Invoice.objects.get(pk=draft.pk).status == "draft"


def test_approving_needs_a_post(client, world, books, as_production_connection):
    draft = new_draft(world, books)
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        assert client.get(page_url(world["acme"], draft, "approve")).status_code == 405


def test_item_names_are_copied_at_approval_so_a_later_item_rename_never_changes_the_invoice(world, books, client, as_production_connection):
    invoice = approve(world, new_draft(world, books))

    items.update_item(world["acme_staff"], books["item"], name="Renamed widget")

    assert [line.item_name for line in invoice.lines.all()] == ["Widget"]
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        page = client.get(page_url(world["acme"], invoice)).content.decode()
    assert "Widget" in page and "Renamed widget" not in page
