"""Uploading a bookkeeping document and bouncing an unusable one within seconds (ticket #14, D20, story 28).

The fast local check itself is tested in test_quality.py; these tests cover who may upload, the resulting document
state, and that the real object-storage write only ever happens through the queued job, never for a bounced upload.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction

from core import documents, jobs, quality
from core.models import Document, Job

from .factories import make_company, make_member
from .test_quality import blank_page, sharp_page


def attempt(statement_block):
    """Run a write that the database must refuse, inside a savepoint so the test can go on (as test_items.py does)."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            statement_block()


def upload(user, company, *, filename="invoice.png", content_type="image/png", data=None):
    return documents.upload_document(user, company, filename=filename, content_type=content_type, data=data or sharp_page())


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["owner", "staff"])
def test_the_owner_and_staff_can_upload_a_good_file(role):
    from core.models import CompanyAccess

    company = make_company("Acme")
    user = make_member("person@acme.example", company, getattr(CompanyAccess, role.upper()))

    document = upload(user, company)

    assert document.status == Document.QUALITY_CHECKED
    assert document.bounce_reason == ""
    assert document.company_id == company.pk and document.uploaded_by_id == user.pk


@pytest.mark.django_db
def test_an_accountant_cannot_upload():
    from core.models import CompanyAccess

    company = make_company("Acme")
    accountant = make_member("acc@firm.example", company, CompanyAccess.ACCOUNTANT)

    with pytest.raises(PermissionDenied):
        upload(accountant, company)


@pytest.mark.django_db
def test_a_user_of_another_company_cannot_upload():
    from core.models import CompanyAccess

    acme, beta = make_company("Acme"), make_company("Beta")
    beta_owner = make_member("owner@beta.example", beta, CompanyAccess.OWNER)

    with pytest.raises(PermissionDenied):
        upload(beta_owner, acme)


@pytest.mark.django_db
def test_a_good_upload_queues_exactly_one_job_naming_only_the_document_id():
    from core.models import CompanyAccess

    company = make_company("Acme")
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)

    document = upload(owner, company)

    job = Job.objects.get()
    assert job.name == documents.STORE_DOCUMENT_JOB
    assert job.payload == {"document_id": document.pk}
    assert job.company_id == company.pk


@pytest.mark.django_db
def test_a_blurry_upload_is_returned_for_re_upload_with_a_reason_and_stores_nothing():
    from core.models import CompanyAccess

    company = make_company("Acme")
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)

    document = upload(owner, company, data=blank_page())

    assert document.status == Document.RETURNED_FOR_REUPLOAD
    assert document.bounce_reason == quality.TOO_BLURRY
    assert document.storage_key == ""
    assert Job.objects.count() == 0


@pytest.mark.django_db
def test_the_queued_job_writes_the_bytes_through_storage_by_company_and_month(fake_ports):
    from core.models import CompanyAccess

    company = make_company("Acme")
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    data = sharp_page()

    document = upload(owner, company, data=data)
    jobs.run_pending()
    document.refresh_from_db()

    assert document.storage_key.startswith(f"documents/{company.pk}/")
    assert fake_ports.storage.get(document.storage_key) == data


@pytest.mark.django_db
def test_list_documents_only_returns_the_given_companys_rows():
    from core.models import CompanyAccess

    acme, beta = make_company("Acme"), make_company("Beta")
    acme_owner = make_member("owner@acme.example", acme, CompanyAccess.OWNER)
    beta_owner = make_member("owner@beta.example", beta, CompanyAccess.OWNER)
    upload(acme_owner, acme)
    upload(beta_owner, beta)

    assert list(documents.list_documents(acme)) == list(Document.objects.filter(company=acme))
    assert documents.list_documents(acme).count() == 1


@pytest.mark.django_db
def test_the_database_refuses_an_accountants_insert_even_when_the_service_is_skipped(world, as_user):
    with as_user(world["accountant"]):
        attempt(lambda: Document.objects.create(
            company=world["acme"], uploaded_by=world["accountant"], original_filename="planted.png",
            content_type="image/png",
        ))

    assert not Document.objects.filter(company=world["acme"]).exists()


@pytest.mark.django_db
def test_a_document_cannot_be_moved_to_another_company(world, as_user):
    """The accountant has access to both companies (`world`), so row-level security alone would allow the move:
    this pins that the guard trigger itself refuses it, not merely each company's own isolation."""
    with as_user(world["acme_owner"]):
        document = Document.objects.create(
            company=world["acme"], uploaded_by=world["acme_owner"], original_filename="invoice.png",
            content_type="image/png",
        )

    with as_user(world["accountant"]):
        attempt(lambda: Document.objects.filter(pk=document.pk).update(company=world["beta"]))


@pytest.mark.django_db
def test_the_staged_file_is_removed_once_the_job_stores_it():
    from core.models import CompanyAccess

    company = make_company("Acme")
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    document = upload(owner, company)
    staged = documents._staging_path(document)
    assert staged.exists()

    jobs.run_pending()

    assert not staged.exists()
