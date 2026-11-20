"""The submitted-invoice table: one row per invoice LHDN has accepted, isolated like every tenant table (#47, D61 (4)).

Written by the submission job (as the worker, in the job's own company context) and read back for display; never
touched directly by a signed-in user's own request, so there is no screen here to drive it through. The table is
closed to the web role entirely (a Torres review finding on PR #194): unlike most tenant tables, no `web` policy
exists at all, so SQL run as `web` is refused outright rather than scoped by company.
"""

import pytest
from django.db import DatabaseError, transaction

from core import jobs
from myinvois.models import SubmittedInvoice

pytestmark = pytest.mark.django_db


def attempt(statement_block):
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            statement_block()


def test_the_worker_can_write_a_row_for_its_own_company_only(world):
    with jobs.as_worker_in(world["acme"]):
        SubmittedInvoice.objects.create(company=world["acme"], invoice_id=1, document_hash="h1")
        attempt(lambda: SubmittedInvoice.objects.create(company=world["beta"], invoice_id=2, document_hash="h2"))

    assert SubmittedInvoice.objects.filter(company=world["acme"]).count() == 1


def test_a_worker_in_one_companys_context_cannot_read_another_companys_row(world):
    with jobs.as_worker_in(world["beta"]):
        SubmittedInvoice.objects.create(company=world["beta"], invoice_id=1, document_hash="h1", uuid="uuid-beta")

    with jobs.as_worker_in(world["acme"]):
        assert list(SubmittedInvoice.objects.all()) == []


def test_a_row_with_no_uuid_yet_means_queued_not_submitted(world):
    with jobs.as_worker_in(world["acme"]):
        row = SubmittedInvoice.objects.create(company=world["acme"], invoice_id=1, document_hash="h1")

    assert row.uuid == "" and row.recovered is False


# --- worker positive control: every operation `update_or_create` actually performs still works --------------------


def test_the_worker_can_update_a_row_it_already_wrote(world):
    with jobs.as_worker_in(world["acme"]):
        SubmittedInvoice.objects.create(company=world["acme"], invoice_id=1, document_hash="h1")
        SubmittedInvoice.objects.filter(invoice_id=1).update(uuid="uuid-1", recovered=True)

    row = SubmittedInvoice.objects.get(invoice_id=1)
    assert row.uuid == "uuid-1" and row.recovered is True


# --- the table is closed to web (and to a worker operation the job never performs) ---------------------------------


def test_the_worker_cannot_delete_a_row(world):
    with jobs.as_worker_in(world["acme"]):
        SubmittedInvoice.objects.create(company=world["acme"], invoice_id=1, document_hash="h1")
        attempt(lambda: SubmittedInvoice.objects.filter(invoice_id=1).delete())


def test_the_web_role_cannot_read_the_ledger_at_all(as_user, world):
    with jobs.as_worker_in(world["acme"]):
        SubmittedInvoice.objects.create(company=world["acme"], invoice_id=1, document_hash="h1")

    with as_user(world["acme_owner"]):
        attempt(lambda: list(SubmittedInvoice.objects.all()))


def test_the_web_role_cannot_write_the_ledger_at_all(as_user, world):
    with as_user(world["acme_owner"]):
        attempt(lambda: SubmittedInvoice.objects.create(company=world["acme"], invoice_id=1, document_hash="h1"))
