"""Submitting one document to MyInvois when LHDN has no idempotency key (D58 (5), (6)).

Our job key stops our own worker running the effect twice, but LHDN answers a repeat with an error, not with the first
answer. So before calling LHDN the caller records the invoice number and document hash; after an ambiguous failure
(timeout, lost response) the document is looked up by its internal number and its uuid taken if LHDN already has it.
A 422 or DV302 means "already submitted": the uuid is recovered, never retried blindly.

A document issued more than 72 hours before submission is not re-issued or re-signed, unless LHDN already holds it. The job stops, the draft is kept
and the owner is told; the owner approves again, so approval locking (D17, D19) is never bypassed.

Nothing here holds document content: errors and the owner's notice name the invoice number and an LHDN code only.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from django.utils.translation import gettext as _

from core import ports
from core.jobs import PermanentError, TransientError
from core.ports.interfaces import ALREADY_SUBMITTED, DocumentUuid, DuplicateSubmission

STALE_AFTER = timedelta(hours=72)


class StaleDocument(PermanentError):
    """The document was issued too long ago for LHDN to accept it; the owner has been told to approve it again."""


class DocumentRejected(PermanentError):
    """LHDN refused the document. Only its error code is kept."""


@dataclass(frozen=True)
class SubmittedDocument:
    uuid: DocumentUuid
    recovered: bool  # True when LHDN already had the document and its uuid was looked up


def submit_invoice(*, gateway, session, document, code_number, issued_at, now, owner_email, record_attempt, key):
    """Submit `document` (bytes) and return its uuid.

    `record_attempt(code_number, document_hash)` stores both before LHDN is called and returns True when an earlier
    attempt for that number was already recorded, so this one may follow an ambiguous failure. `key(step)` is
    `jobs.idempotency_key`.
    """
    if now - issued_at > STALE_AFTER:
        # The document may already be at LHDN, accepted before an answer was lost: then there is nothing to re-issue.
        found = gateway.find_document(session, code_number)
        if found is not None:
            return SubmittedDocument(found.uuid, recovered=True)
        ports.get().email.send(
            owner_email,
            _("Invoice %(number)s must be approved again") % {"number": code_number},
            _(
                "Invoice %(number)s was not sent to LHDN within 72 hours of its issue time, and LHDN no longer accepts it. "
                "Your draft is kept. Please review it and approve it again."
            )
            % {"number": code_number},
            idempotency_key=key("stale-notice"),
        )
        raise StaleDocument(f"{code_number} is older than 72 hours")

    followed_a_failure = record_attempt(code_number, hashlib.sha256(document).hexdigest())
    if followed_a_failure:
        found = gateway.find_document(session, code_number)
        if found is not None:
            return SubmittedDocument(found.uuid, recovered=True)

    signed = gateway.sign(session, document, code_number)
    try:
        submission = gateway.submit(session, [signed], idempotency_key=key("submit"))
    except DuplicateSubmission:
        return _recover(gateway, session, code_number)
    if submission.accepted:
        return SubmittedDocument(submission.accepted[0].uuid, recovered=False)
    if not submission.rejected:
        raise TransientError("LHDN answered the submission with no result for the document")
    code = submission.rejected[0].error_code
    if code == ALREADY_SUBMITTED:
        return _recover(gateway, session, code_number)
    raise DocumentRejected(f"LHDN rejected the document with {code}")


def _recover(gateway, session, code_number):
    found = gateway.find_document(session, code_number)
    if found is None:
        raise TransientError("LHDN reports a duplicate but does not list the document yet")
    return SubmittedDocument(found.uuid, recovered=True)
