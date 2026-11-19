"""Approve & submit: one owner action approves an invoice and queues its submission through the gateway (#47).

Tested at the Jobs seam (`core.jobs.run_pending()`) with the fake MyInvois port, as the acceptance criteria ask;
the HTTP seam (one owner action, staff refused) is in core/tests/test_invoice_screens.py.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from core import customers, items, invoices, jobs, numbering
from core.models import Invoice, Job
from core.tests.test_customers import BRN_VALUE, CONTACT, TIN, switch_einvoicing_on
from myinvois.jobs import SUBMIT_INVOICE_JOB
from myinvois.models import SubmittedInvoice

OTHERS, ONE, NOT_APPLICABLE = "022", "C62", "06"

pytestmark = pytest.mark.django_db


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


def new_draft(world, books):
    return invoices.create_draft(
        world["acme_owner"], world["acme"], customer_id=books["customer"].pk,
        lines=[{"item": books["item"].pk, "quantity": "2", "unit_price": "5.50"}],
    )


def approve_as_einvoice(world, books):
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    return invoices.approve_invoice(world["acme_owner"], new_draft(world, books))


# --- the engine side: one owner action enqueues the job, identifiers only -------------------------------------------


def test_approving_an_e_invoice_queues_the_submission_job_for_the_right_company(world, books):
    invoice = approve_as_einvoice(world, books)

    job = Job.objects.get(name=SUBMIT_INVOICE_JOB)
    assert job.company_id == world["acme"].pk and job.payload == {"invoice_id": invoice.pk}


def test_approving_a_normal_invoice_queues_no_submission_job(world, books):
    invoices.approve_invoice(world["acme_owner"], new_draft(world, books))  # e-invoicing stays off

    assert not Job.objects.filter(name=SUBMIT_INVOICE_JOB).exists()


def test_re_approving_after_a_stale_failure_queues_a_replacement_job_that_submits(world, books, fake_ports):
    """D60 (2): the stale job stops and tells the owner, who approves again; that one action resumes submission
    rather than stranding the e-invoice (a Torres review finding on PR #194)."""
    invoice = approve_as_einvoice(world, books)
    Invoice.objects.filter(pk=invoice.pk).update(approved_at=timezone.now() - timedelta(hours=73))

    jobs.run_pending()  # StaleDocument: the first job fails permanently, nothing reaches LHDN
    assert Job.objects.get(name=SUBMIT_INVOICE_JOB).status == Job.FAILED
    assert fake_ports.gateway.submissions == []

    invoices.approve_invoice(world["acme_owner"], Invoice.objects.get(pk=invoice.pk))
    assert Job.objects.filter(name=SUBMIT_INVOICE_JOB).count() == 2

    jobs.run_pending()

    assert len(fake_ports.gateway.submissions) == 1
    row = SubmittedInvoice.objects.get(invoice_id=invoice.pk)
    assert row.uuid and row.recovered is False


def test_a_second_job_for_an_already_submitted_invoice_does_nothing(world, books, fake_ports):
    """The myinvois-side guard, independent of why a second job got enqueued (a Torres review finding on PR #194):
    a fresh job naming an invoice that already has a recorded uuid must not resubmit it."""
    invoice = approve_as_einvoice(world, books)
    jobs.run_pending()
    assert len(fake_ports.gateway.submissions) == 1

    jobs.enqueue(SUBMIT_INVOICE_JOB, company=world["acme"], invoice_id=invoice.pk)
    jobs.run_pending()

    assert len(fake_ports.gateway.submissions) == 1


# --- the job itself: submits through the gateway using the locked buyer details ------------------------------------


def test_running_the_job_submits_through_the_gateway_and_records_the_uuid(world, books, fake_ports):
    invoice = approve_as_einvoice(world, books)

    jobs.run_pending()

    row = SubmittedInvoice.objects.get(invoice_id=invoice.pk)
    assert row.uuid and row.recovered is False and len(fake_ports.gateway.submissions) == 1


def test_a_retried_job_never_submits_the_same_invoice_twice(world, books, fake_ports):
    invoice = approve_as_einvoice(world, books)
    fake_ports.gateway.lose_next_response = True  # LHDN accepts it; the answer back to us is lost

    jobs.run_pending()  # TransientError: retried later
    Job.objects.filter(name=SUBMIT_INVOICE_JOB).update(run_after=None)  # don't wait out the backoff in the test
    jobs.run_pending()  # the retry must recover the first attempt's uuid, not submit a second time

    assert len(fake_ports.gateway.submissions) == 1
    row = SubmittedInvoice.objects.get(invoice_id=invoice.pk)
    assert row.recovered is True


def test_a_retry_outside_lhdns_own_duplicate_detection_window_still_recovers_not_resubmits(world, books, fake_ports, monkeypatch):
    """Codex finding on PR #194: the attempt row this job writes before calling LHDN is rolled back with the rest
    of a failed attempt, so a naive retry would look like a first attempt again and resubmit for real once LHDN's
    own short-lived duplicate detection (its 10-minute payload window, its 2-hour number window) has passed."""
    invoice = approve_as_einvoice(world, books)
    moment = {"now": timezone.now()}
    monkeypatch.setattr(jobs, "clock", lambda: moment["now"])
    fake_ports.gateway.now = lambda: moment["now"]
    fake_ports.gateway.lose_next_response = True

    jobs.run_pending()  # LHDN accepts it; the answer back to us is lost -> TransientError, retried later
    moment["now"] += timedelta(hours=3)  # past both of the fake's own duplicate-detection windows
    Job.objects.filter(name=SUBMIT_INVOICE_JOB).update(run_after=None)
    jobs.run_pending()

    assert len(fake_ports.gateway.submissions) == 1
    row = SubmittedInvoice.objects.get(invoice_id=invoice.pk)
    assert row.recovered is True


def test_a_job_naming_another_companys_invoice_touches_nothing(world, books, registry):
    """Row-level security scopes `Invoice.objects` to the job's own company; a wrong id finds nothing (D36)."""
    beta_invoice = Invoice.objects.create(company=world["beta"], customer=customers.create_customer(world["beta_owner"], world["beta"], legal_name="X"))
    seen = {}

    def probe(**payload):
        try:
            Invoice.objects.get(pk=payload["invoice_id"])
        except Invoice.DoesNotExist:
            seen["found"] = False
        else:
            seen["found"] = True

    jobs.register("probe-cross-company-invoice")(probe)
    jobs.enqueue("probe-cross-company-invoice", company=world["acme"], invoice_id=beta_invoice.pk)

    jobs.run_pending()

    assert seen == {"found": False}
