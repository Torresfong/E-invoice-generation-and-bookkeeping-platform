"""Uploading a bookkeeping document and bouncing an unusable one within seconds (D20, story 28, ticket #14).

The owner and staff upload; the accountant reads (`permissions`, D45). The fast local check (`core.quality`) runs
in memory before anything is written: a document row is created `uploaded` and, in the same transaction, moved on
to `quality_checked` or `returned_for_re_upload` with a reason (see `models.Document`). A bounced upload is never
staged or stored: there is nothing worth keeping, and the client is told to retake it at once.

An accepted upload is staged to local disk (`settings.DOCUMENT_STAGING_ROOT`) and a job is queued to write it
through the real object storage port, because that port call cannot be undone and so must happen outside the
request's own transaction (D53 (3)); the job's payload holds the document id only (ADR 0001 rule 4). The storage
key groups by company and upload month, matching the SQL Account export's own per-month batching (D42).

The same job also runs the D41 go-live-date check, since an AI call is likewise a port call that must happen in a
job, not the request (D53 (3)), and the job has already read the staged bytes for the storage write. It asks the
AI provider only whether the document is a sales invoice and, if so, its date (`EXTRACTION_SCHEMA`); nothing else
from that answer is kept, because classifying and extracting a document in full is ticket #15/#18's job, not this
one's. A sales invoice dated on or after `company.go_live_date` is moved from `quality_checked` on to
`returned_for_re_upload` with the reason "issue it in the platform" (D18); an older one, or one the AI does not
call a sales invoice, or a company with no go-live date set yet, is left `quality_checked`. Because this needs an
AI call, it is not part of the "bounced within seconds" promise that the fast local check keeps (D20); the issue's
own acceptance criterion for it names no such deadline. An extraction answer this module cannot parse (no go-live
date set, an unrecognised document type, a malformed date) fails open: the document stays `quality_checked` rather
than risk wrongly bouncing a document nobody has reviewed.

Known limitation, accepted for this ticket: `_stage` writes to disk inside the request's own transaction, so a
later rollback of that same transaction (a deliberate 5xx, D53 (1)) leaves the staged bytes on disk with no
`Document` row pointing at them. Nothing sweeps an orphan like this; it is a disk-hygiene cost, not a correctness
or tenancy one, since no row and no job ever names it.
"""

from datetime import date
from pathlib import Path

from django.conf import settings
from django.db import transaction

from . import jobs, permissions, ports, quality
from .models import Document

STORE_DOCUMENT_JOB = "store_uploaded_document"

# What the AI provider is asked for and nothing more (D41): whether this is a sales invoice, and if so, its date.
# A future real adapter must answer in this shape; `document_type` values other than "sales_invoice" (purchase
# invoice, receipt, anything else) and a null or missing date are both treated as "no go-live check applies".
SALES_INVOICE = "sales_invoice"
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "invoice_date": {"type": ["string", "null"]},
    },
    "required": ["document_type", "invoice_date"],
}


def list_documents(company):
    """The company's documents, newest first. Row-level security already hides every other company's."""
    return Document.objects.filter(company=company)


def upload_document(user, company, *, filename, content_type, data):
    """Upload one document as `user`, who must be the owner or staff of `company`.

    `data` is the file's bytes, already read from the request. Raises `PermissionDenied` for anyone else. Returns
    the `Document` row whatever the local check decides: `quality_checked` when it passes, or
    `returned_for_re_upload` with `bounce_reason` holding one of `core.quality`'s reason codes when it does not;
    render it with `core.quality.reason_text` (or the `reason_text` template filter), never store the rendered text.

    A file over `settings.MAX_UPLOAD_BYTES` is bounced before it ever reaches Pillow: Django places no cap of its
    own on a file upload's size, and decoding an oversized image is exactly the costly, memory-hungry work this
    check exists to avoid. The view still reads the whole upload into memory first; avoiding that too would need
    a streaming read, which is not built here.
    """
    permissions.require(user, "upload_documents", company)
    reason = quality.TOO_LARGE if len(data) > settings.MAX_UPLOAD_BYTES else quality.check(data, content_type)
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
    """Write a staged upload through the object storage port, record its key, run the D41 go-live check, and clean
    up the staging file.

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
    data = path.read_bytes()
    key = _storage_key(document)
    ports.get().storage.put(key, data)
    document.storage_key = key
    fields = ["storage_key"]

    reason = _go_live_check(document, data)
    if reason:
        document.status, document.bounce_reason = Document.RETURNED_FOR_REUPLOAD, reason
        fields += ["status", "bounce_reason"]

    document.save(update_fields=fields)
    transaction.on_commit(lambda: path.unlink(missing_ok=True))


def _go_live_check(document, data):
    """None if the document stays `quality_checked`; otherwise the reason it is returned instead (D41).

    Fails open on anything this module cannot act on with confidence: no go-live date set yet, a document type
    other than a sales invoice, or an extraction answer with no date or one that does not parse.
    """
    if document.company.go_live_date is None:
        return None
    answer = ports.get().ai.extract(data, EXTRACTION_SCHEMA, idempotency_key=jobs.idempotency_key("extract"))
    if answer.get("document_type") != SALES_INVOICE:
        return None
    try:
        invoice_date = date.fromisoformat(answer.get("invoice_date") or "")
    except ValueError:
        return None
    if invoice_date >= document.company.go_live_date:
        return quality.SALES_INVOICE_AFTER_GO_LIVE
    return None
