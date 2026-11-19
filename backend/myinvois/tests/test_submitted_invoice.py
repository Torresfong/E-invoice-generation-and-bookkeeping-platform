"""The submitted-invoice table: one row per invoice LHDN has accepted, isolated like every tenant table (#47, D61 (4)).

Written by the submission job (as the worker, in the job's own company context) and read back for display; never
touched directly by a signed-in user's own request, so there is no screen here to drive it through.
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
