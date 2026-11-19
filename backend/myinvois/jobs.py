"""The submission job: approve & submit, queued by the engine, run by the worker (#47, D36, D44).

Registered with `core.jobs.register` and imported from `MyinvoisConfig.ready()`, so the engine enqueues it by
name and never imports this module (D61 (4)). By the time this runs, the job's own company column has already
set the row-level security context (core.jobs): `Invoice.objects` and `core.einvoicing.current_setting` see only
this invoice's company, and a payload naming another company's invoice finds nothing.
"""

from django.conf import settings
from django.utils import timezone

from core import einvoicing, jobs
from core.models import Invoice
from core.ports import get as get_ports
from core.ports.myinvois import credentials_from_environment

from . import document
from .models import SubmittedInvoice
from .submission import check_not_stale, submit_invoice

# Must match the literal name `core.invoices` enqueues: the engine cannot import this module (D61 (4)), so the two
# sides share no constant, only this string.
SUBMIT_INVOICE_JOB = "myinvois_submit_invoice"


def _document_version():
    # D60 (1): production signs "1.1"; the sandbox may still be unsigned, so it stays on "1.0".
    return "1.1" if settings.MYINVOIS_ENVIRONMENT == "production" else "1.0"


def _record_attempt(invoice):
    """`record_attempt` for `submission.submit_invoice`: writes the row this job also records the outcome on.

    `followed_a_failure` reads `jobs.current_attempt()`, not this write's own effect: `core.jobs._execute()` rolls
    back everything a failed attempt wrote, this row included, so a crash between LHDN accepting the document and
    our commit would otherwise look like a first attempt again on retry and resubmit instead of recovering (a
    Codex finding on PR #194). The job's own attempt count survives that rollback, because the runner sets it
    before the handler ever runs.
    """

    def record(code_number, document_hash):
        SubmittedInvoice.objects.update_or_create(
            invoice_id=invoice.pk, defaults={"company": invoice.company, "document_hash": document_hash}
        )
        return jobs.current_attempt() > 1

    return record


@jobs.register(SUBMIT_INVOICE_JOB)
def _submit_invoice(*, invoice_id):
    invoice = Invoice.objects.get(pk=invoice_id)
    # A replacement job (D60 (2) re-approval) can name an invoice an earlier, separate job already finished: that
    # earlier job's own `jobs.current_attempt()` cannot be seen here, since this is a fresh job row starting again
    # at attempt 1 (a Torres review finding on PR #194). The persisted outcome is checked directly instead, so a
    # second job for an already-submitted invoice does nothing rather than submitting it again.
    if SubmittedInvoice.objects.filter(invoice_id=invoice.pk).exclude(uuid="").exists():
        return
    setting = einvoicing.current_setting(invoice.company)
    gateway = get_ports().gateway
    credentials = credentials_from_environment(settings.MYINVOIS_ENVIRONMENT)
    session = gateway.login_as_taxpayer(credentials, invoice.company_id, setting.tin, setting.rob or None)
    now = timezone.now()
    owner_email = einvoicing.owner_email(invoice.company)

    # Checked before the document is ever built (#123's handoff): a retry that lands here more than 72 hours after
    # approval must still recover an earlier attempt's uuid by lookup, not fail inside `document.map_invoice`.
    result = check_not_stale(
        gateway=gateway, session=session, code_number=invoice.number, issued_at=invoice.approved_at, now=now,
        owner_email=owner_email, key=jobs.idempotency_key,
    )
    if result is None:
        built = document.map_invoice(invoice, document_version=_document_version(), now=now)
        result = submit_invoice(
            gateway=gateway, session=session, document=built, code_number=invoice.number,
            issued_at=invoice.approved_at, now=now, owner_email=owner_email,
            record_attempt=_record_attempt(invoice), key=jobs.idempotency_key,
        )

    SubmittedInvoice.objects.update_or_create(
        invoice_id=invoice.pk, defaults={"company": invoice.company, "uuid": str(result.uuid), "recovered": result.recovered},
    )
