"""The four external boundaries. Later tickets widen these; callers depend only on these shapes.

A call with an external effect that a job makes carries the job's idempotency key (`core.jobs.idempotency_key`). A
job is run again after a crash, so the call may arrive twice; an implementation that sees a key twice does the
effect once and answers the second call as it answered the first (ticket #27, ADR 0001). Calls made outside a job
leave the key out.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

# What MyInvois says about a submitted document. A buyer's rejection request is not one of these: it is a marker on a
# validated document, which stays valid until the owner cancels it.
SUBMITTED = "submitted"
VALIDATED = "validated"
INVALID = "invalid"
CANCELLED = "cancelled"


class IdempotencyKeyMissing(RuntimeError):
    """A job made a call with an external effect without its idempotency key, so a retry could repeat the effect."""


class WrongCompany(RuntimeError):
    """A document signed for one company was submitted through another company's session."""


class SessionExpired(RuntimeError):
    """The login lasts 60 minutes; a call after that needs a new login."""


class DuplicateSubmission(RuntimeError):
    """LHDN's 422 DuplicateSubmission: this payload was sent within the last 10 minutes. It does not return the uuid."""

    status_code = 422


class TinCheck(Enum):
    VALID = "valid"
    BAD_FORMAT = "bad_format"
    NOT_FOUND = "not_found"


ALREADY_SUBMITTED = "DV302"  # LHDN rejects the same internal number within 2 hours with this code
ID_TYPES = ("NRIC", "PASSPORT", "BRN", "ARMY")
SESSION_MINUTES = 60


class SubmissionUid(str):
    """What LHDN calls a whole submission. Polling takes this; cancelling does not."""


class DocumentUuid(str):
    """What LHDN calls one accepted document. Cancelling takes this; polling does not."""


@dataclass(frozen=True)
class IntermediaryCredentials:
    """Our ERP client ID and secret for one MyInvois environment (D58 (1)). Left out of repr so neither can reach a log."""

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class TaxpayerSession:
    """A login as intermediary for one company's taxpayer (D1). The TIN and token are left out of repr so neither can reach a log.

    `tin` and `rob` come from the company row, never from a payload (D36).
    """

    company_id: int
    tin: str = field(repr=False)
    token: str = field(repr=False)
    expires_at: datetime
    rob: str | None = field(default=None, repr=False)

    @property
    def onbehalf_of(self):
        """The `onbehalfof` header: the TIN, or `TIN:ROB`."""
        return f"{self.tin}:{self.rob}" if self.rob else self.tin


@dataclass(frozen=True)
class SignedDocument:
    company_id: int
    code_number: str = field(repr=False)  # our internal invoice number: LHDN's `codeNumber`
    document: bytes = field(repr=False)
    signature: bytes = field(repr=False)

    @property
    def document_hash(self):
        """SHA-256 hex of the document. Whether LHDN wants hex or base64 is open (D58)."""
        return hashlib.sha256(self.document).hexdigest()


@dataclass(frozen=True)
class AcceptedDocument:
    code_number: str = field(repr=False)
    uuid: DocumentUuid


@dataclass(frozen=True)
class RejectedDocument:
    code_number: str = field(repr=False)
    error_code: str


@dataclass(frozen=True)
class Submission:
    """LHDN's answer to a submit: one document can be accepted while another is rejected (D58 (3))."""

    submission_uid: SubmissionUid
    accepted: list[AcceptedDocument]
    rejected: list[RejectedDocument]


@dataclass
class DocumentStatus:
    uuid: DocumentUuid
    state: str
    long_id: str | None = None  # valid documents only
    date_time_validated: datetime | None = None
    reason: str | None = None  # LHDN's reason for an invalid document
    rejection_requested: bool = False  # `rejectRequestDateTime` is set


class MyInvoisGateway(Protocol):
    """Every call to MyInvois (D14), in the shapes LHDN publishes (D58). Swapping the implementation, or LHDN changing, must not touch callers."""

    def login_as_taxpayer(
        self, credentials: IntermediaryCredentials, company_id: int, tin: str, rob: str | None = None
    ) -> TaxpayerSession:
        """The session lasts 60 minutes. `tin` and `rob` are read from the company row by the caller.

        A login proves only that the taxpayer has authorised us as intermediary; it proves no submit, cancel or reject
        permission (D57 (3)). A 403 raises `PermissionDenied` and wrong client credentials `AuthenticationFailed`, both
        permanent and never retried; a 429, 5xx or network failure is transient (D57 (4)).
        """
        ...

    def validate_tin(self, tin: str, id_type: str, id_value: str) -> TinCheck:
        """`id_type` is one of NRIC, PASSPORT, BRN, ARMY."""
        ...

    def sign(self, session: TaxpayerSession, document: bytes, code_number: str) -> SignedDocument:
        """An implementation signs only through signing.Signer, which is what logs it (D13)."""
        ...

    def submit(
        self, session: TaxpayerSession, signed: list[SignedDocument], idempotency_key: str | None = None
    ) -> Submission:
        """Refuses with WrongCompany a document signed for another company than the session's.

        LHDN has no idempotency key: the key only guards our own worker. A repeat raises DuplicateSubmission (422), or
        comes back rejected with DV302, and neither returns the original uuid; find it with find_document.
        """
        ...

    def poll_status(self, session: TaxpayerSession, submission_uid: SubmissionUid) -> list[DocumentStatus]: ...

    def find_document(self, session: TaxpayerSession, code_number: str) -> DocumentStatus | None:
        """The taxpayer's recent document with this internal number, or None. How a caller recovers a uuid."""
        ...

    def cancel(
        self, session: TaxpayerSession, uuid: DocumentUuid, reason: str, idempotency_key: str | None = None
    ) -> None: ...


class KeyProvider(Protocol):
    """The key vault (D13), reached only by signing.Signer behind the gateway: not a fifth port.

    It signs with the key it holds, so a key that cannot leave the vault fits the same shape.
    """

    def sign(self, certificate_reference: str, digest: bytes) -> bytes: ...


class AIProvider(Protocol):
    def extract(self, document: bytes, schema: dict, idempotency_key: str | None = None) -> dict: ...


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str, idempotency_key: str | None = None) -> None: ...


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...
