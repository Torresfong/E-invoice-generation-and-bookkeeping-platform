"""A firm applies to join the platform and the operator decides (D67 (1), D49 (3)).

Applying runs in a SECURITY DEFINER function (migration 0039) because the applicant is not signed in and has no account;
it stores an application and creates no firm and no user. Deciding is an operator command, which runs as the table owner:
approval creates the firm and its first firm administrator, who is emailed a link to choose a password, in one
transaction with the email, so an administrator who cannot be reached leaves no firm behind. A refusal creates nothing.
"""

from django.db import transaction
from django.utils import timezone

from . import audit, services, tokens
from .db import scalar
from .models import AuditEntry, FirmApplication


class ApplicationNotFound(Exception):
    pass


class AlreadyDecided(Exception):
    """The application was approved or refused already; a decision is made once."""


def submit(firm_name, ssm_number, contact_name, email, people):
    """Store an application. False when nothing was stored (the address or the name is taken); the caller answers the same."""
    with transaction.atomic():
        return scalar("SELECT submit_firm_application(%s, %s, %s, %s, %s)", [firm_name, ssm_number, contact_name, email, people])


def pending():
    return FirmApplication.objects.filter(status=FirmApplication.PENDING).order_by("created_at", "pk")


def _lock_pending(pk):
    try:
        application = FirmApplication.objects.select_for_update().get(pk=pk)
    except FirmApplication.DoesNotExist as error:
        raise ApplicationNotFound from error
    if application.status != FirmApplication.PENDING:
        raise AlreadyDecided
    return application


def _stamp_decision(application, status):
    application.status = status
    application.decided_by = audit.OPERATOR
    application.decided_at = timezone.now()


@transaction.atomic
def approve(pk):
    """Create the firm and its first firm administrator and email them a password link. Returns the administrator.

    Raises `services.EmailInUse` if the address got an account since it applied, and an IntegrityError if a firm of that
    name now exists; either leaves the application pending and creates nothing.
    """
    application = _lock_pending(pk)
    administrator = services.create_firm_with_administrator(application.firm_name, application.contact_email)
    tokens.issue_setup_link(administrator)
    _stamp_decision(application, FirmApplication.APPROVED)
    application.firm = administrator.firm
    application.save()
    # The applicant had no firm until now, so the decision is recorded in the new firm's trail and on the application.
    audit.record_firm_event(
        AuditEntry.UPDATE, administrator.firm, target_type="firm_application", target_id=application.pk, changed_fields=["status"]
    )
    return administrator


@transaction.atomic
def refuse(pk, note=""):
    """Refuse an application. Creates nothing; who decided, when and why stay on the application."""
    application = _lock_pending(pk)
    _stamp_decision(application, FirmApplication.REFUSED)
    application.refusal_note = note.strip()
    application.save()
    return application
