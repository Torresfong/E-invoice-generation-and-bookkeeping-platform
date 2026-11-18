"""Uploading a bookkeeping document and bouncing an unusable one within seconds (D20, story 28, ticket #14).

The owner and staff upload; the accountant reads (`permissions`, D45). The fast local check (`core.quality`) runs
in memory before anything is written: a document row is created `uploaded` and, in the same transaction, moved on
to `quality_checked` or `returned_for_re_upload` with a reason (see `models.Document`). A bounced upload is never
staged or stored: there is nothing worth keeping, and the client is told to retake it at once.

An accepted upload is staged to local disk (`settings.DOCUMENT_STAGING_ROOT`) and a job is queued to write it
through the real object storage port, because that port call cannot be undone and so must happen outside the
request's own transaction (D53 (3)); the job's payload holds the document id only (ADR 0001 rule 4). The storage
key groups by company and upload month, matching the SQL Account export's own per-month batching (D42).
"""

from pathlib import Path

from django.conf import settings
from django.db import transaction

from . import jobs, permissions, ports, quality
from .models import Document

STORE_DOCUMENT_JOB = "store_uploaded_document"


def list_documents(company):
    """The company's documents, newest first. Row-level security already hides every other company's."""
    return Document.objects.filter(company=company)


def upload_document(user, company, *, filename, content_type, data):
    """Upload one document as `user`, who must be the owner or staff of `company`.

    `data` is the file's bytes, already read from the request. Raises `PermissionDenied` for anyone else. Returns
    the `Document` row whatever the local check decides: `quality_checked` when it passes, or
    `returned_for_re_upload` with `bounce_reason` holding one of `core.quality`'s reason codes when it does not;
    render it with `core.quality.reason_text` (or the `reason_text` template filter), never store the rendered text.
    """
    permissions.require(user, "upload_documents", company)
    reason = quality.check(data, content_type)
    with transaction.atomic():
        document = Document.objects.create(company=company, uploaded_by=user, original_filename=filename, content_type=content_type)
        if reason:
            document.status, document.bounce_reason = Document.RETURNED_FOR_REUPLOAD, reason
            document.save(update_fields=["status", "bounce_reason"])
            return document
        document.status = Document.QUALITY_CHECKED
        document.save(update_fields=["status"])
        _stage(document, data)
        jobs.enqueue(STORE_DOCUMENT_JOB, company=company, document_id=document.pk)
    return document


def _staging_path(document):
    return Path(settings.DOCUMENT_STAGING_ROOT) / str(document.company_id) / str(document.pk)


def _stage(document, data):
    path = _staging_path(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _storage_key(document):
    """A key bounded well under `Document.storage_key`'s 255 characters, whatever the original filename's length.

    Only the extension travels from the original filename (bounded to 10 characters); the full name stays in
    `original_filename` for display, so nothing here needs to carry it too.
    """
    month = document.created_at.strftime("%Y-%m")
    suffix = Path(document.original_filename).suffix[:10]
    return f"documents/{document.company_id}/{month}/{document.pk}{suffix}"


@jobs.register(STORE_DOCUMENT_JOB)
def _store_uploaded_document(*, document_id):
    """Write a staged upload through the object storage port and record its key; clean up the staging file.

    Row-level security already confines this to the job's own company (migration 0046, `worker=True`): a payload
    naming another company's document id finds nothing here, consistent with the job queue's identifier-only rule.

    The staging file is deleted only once the surrounding transaction actually commits (`transaction.on_commit`),
    never right after `storage.put`: a delete is not transactional, so deleting it earlier and then losing the
    commit (a crash, a lost lease) would leave the job unable to retry — `storage_key` would be blank again, but
    the bytes it needs to re-stage would already be gone. `missing_ok` covers a retry that reaches this point
    after an earlier attempt's callback already ran.
    """
    document = Document.objects.get(pk=document_id)
    path = _staging_path(document)
    key = _storage_key(document)
    ports.get().storage.put(key, path.read_bytes())
    document.storage_key = key
    document.save(update_fields=["storage_key"])
    transaction.on_commit(lambda: path.unlink(missing_ok=True))
