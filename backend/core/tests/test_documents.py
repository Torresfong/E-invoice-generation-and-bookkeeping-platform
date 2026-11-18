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


@pytest.fixture(autouse=True)
def staging_in_tmp(settings, tmp_path):
    """Stage into pytest's own tmp_path, not the real var/pending_documents: a test that never runs the storage
    job (most of them) would otherwise leave its staged file behind in the working tree."""
    settings.DOCUMENT_STAGING_ROOT = tmp_path


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
def test_an_upload_over_the_byte_limit_is_bounced_without_reaching_pillow(settings, monkeypatch):
    from core.models import CompanyAccess

    settings.MAX_UPLOAD_BYTES = 10
    company = make_company("Acme")
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    monkeypatch.setattr(quality, "check", lambda *a, **k: pytest.fail("must not reach Pillow"))

    document = upload(owner, company, data=b"x" * 11)

    assert document.status == Document.RETURNED_FOR_REUPLOAD and document.bounce_reason == quality.TOO_LARGE


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


# --- the D41 go-live-date check (ticket #14, extended by Torres's decision to build it now rather than in #15) ---


def _answer(document_type, invoice_date):
    return lambda document, schema, idempotency_key=None: {"document_type": document_type, "invoice_date": invoice_date}


@pytest.mark.django_db
def test_a_sales_invoice_dated_on_the_go_live_date_is_returned(fake_ports):
    from datetime import date

    from core.models import CompanyAccess

    company = make_company("Acme")
    company.go_live_date = date(2026, 6, 1)
    company.save(update_fields=["go_live_date"])
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    fake_ports.ai.extract = _answer(documents.SALES_INVOICE, "2026-06-01")

    document = upload(owner, company)
    jobs.run_pending()
    document.refresh_from_db()

    assert document.status == Document.RETURNED_FOR_REUPLOAD
    assert document.bounce_reason == quality.SALES_INVOICE_AFTER_GO_LIVE
    assert document.storage_key  # still stored: the go-live check does not stop the storage write


@pytest.mark.django_db
def test_a_sales_invoice_dated_before_go_live_stays_quality_checked(fake_ports):
    from datetime import date

    from core.models import CompanyAccess

    company = make_company("Acme")
    company.go_live_date = date(2026, 6, 1)
    company.save(update_fields=["go_live_date"])
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    fake_ports.ai.extract = _answer(documents.SALES_INVOICE, "2026-05-31")

    document = upload(owner, company)
    jobs.run_pending()
    document.refresh_from_db()

    assert document.status == Document.QUALITY_CHECKED and document.bounce_reason == ""


@pytest.mark.django_db
def test_a_purchase_invoice_is_unaffected_by_the_go_live_date(fake_ports):
    from datetime import date

    from core.models import CompanyAccess

    company = make_company("Acme")
    company.go_live_date = date(2026, 1, 1)
    company.save(update_fields=["go_live_date"])
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    fake_ports.ai.extract = _answer("purchase_invoice", "2026-06-01")

    document = upload(owner, company)
    jobs.run_pending()
    document.refresh_from_db()

    assert document.status == Document.QUALITY_CHECKED


@pytest.mark.django_db
def test_no_go_live_date_set_skips_the_check_entirely(fake_ports):
    from core.models import CompanyAccess

    company = make_company("Acme")  # go_live_date left null
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    fake_ports.ai.extract = lambda *a, **k: pytest.fail("must not call the AI provider with no go-live date set")

    document = upload(owner, company)
    jobs.run_pending()
    document.refresh_from_db()

    assert document.status == Document.QUALITY_CHECKED


@pytest.mark.django_db
def test_a_malformed_extraction_answer_fails_open(fake_ports):
    from datetime import date

    from core.models import CompanyAccess

    company = make_company("Acme")
    company.go_live_date = date(2026, 1, 1)
    company.save(update_fields=["go_live_date"])
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    fake_ports.ai.extract = _answer(documents.SALES_INVOICE, "not-a-date")

    document = upload(owner, company)
    jobs.run_pending()
    document.refresh_from_db()

    assert document.status == Document.QUALITY_CHECKED


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
def test_the_database_refuses_an_accountants_update_even_when_the_service_is_skipped(world, as_user):
    with as_user(world["acme_owner"]):
        document = Document.objects.create(
            company=world["acme"], uploaded_by=world["acme_owner"], original_filename="invoice.png",
            content_type="image/png",
        )

    with as_user(world["accountant"]):
        attempt(lambda: Document.objects.filter(pk=document.pk).update(original_filename="hijacked.png"))

    document.refresh_from_db()
    assert document.original_filename == "invoice.png"


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


@pytest.mark.django_db(transaction=True)
def test_the_staged_file_is_removed_once_the_job_stores_it():
    """`transaction=True`: the cleanup runs on the job's transaction commit (`transaction.on_commit`), which never
    fires under the ordinary wrapping rollback (see the other `document_guard` tests above, which do not need it)."""
    from core.models import CompanyAccess

    company = make_company("Acme")
    owner = make_member("owner@acme.example", company, CompanyAccess.OWNER)
    document = upload(owner, company)
    staged = documents._staging_path(document)
    assert staged.exists()

    jobs.run_pending()

    assert not staged.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("who", ["acme_owner", "acme_staff", "accountant"])
def test_a_document_cannot_be_deleted_by_anyone(world, as_user, who):
    with as_user(world["acme_owner"]):
        document = Document.objects.create(
            company=world["acme"], uploaded_by=world["acme_owner"], original_filename="invoice.png",
            content_type="image/png",
        )

    with as_user(world[who]):
        attempt(lambda: Document.objects.filter(pk=document.pk).delete())
    # nor by the table owner, who is not stopped by privileges: the trigger refuses
    attempt(lambda: Document.objects.filter(pk=document.pk).delete())

    assert Document.objects.filter(pk=document.pk).exists()


@pytest.mark.django_db
def test_the_worker_can_update_only_storage_key_status_and_bounce_reason(world):
    document = upload(world["acme_owner"], world["acme"])

    with jobs.as_worker_in(world["acme"]):
        Document.objects.filter(pk=document.pk).update(
            storage_key="documents/1/2026-11/1.png", status=Document.RETURNED_FOR_REUPLOAD,
            bounce_reason=quality.SALES_INVOICE_AFTER_GO_LIVE,
        )
        attempt(lambda: Document.objects.filter(pk=document.pk).update(original_filename="hijacked.png"))

    document.refresh_from_db()
    assert document.storage_key == "documents/1/2026-11/1.png"
    assert document.status == Document.RETURNED_FOR_REUPLOAD
    assert document.bounce_reason == quality.SALES_INVOICE_AFTER_GO_LIVE
    assert document.original_filename == "invoice.png"


@pytest.mark.django_db
def test_a_job_naming_another_companys_document_touches_nothing(world, registry):
    beta_document = upload(world["beta_owner"], world["beta"])
    seen = {}

    def probe(**payload):
        try:
            Document.objects.get(pk=payload["document_id"])
        except Document.DoesNotExist:
            seen["found"] = False
        else:
            seen["found"] = True

    jobs.register("probe-cross-company")(probe)
    jobs.enqueue("probe-cross-company", company=world["acme"], document_id=beta_document.pk)

    jobs.run_pending()

    beta_document.refresh_from_db()
    assert beta_document.original_filename == "invoice.png"
    assert seen == {"found": False}
