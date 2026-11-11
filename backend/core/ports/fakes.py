"""In-memory fakes for the four ports. They record what callers send so tests can assert on it."""

import re
from datetime import timedelta

from django.utils import timezone

from .interfaces import (
    ALREADY_SUBMITTED,
    CANCELLED,
    ID_TYPES,
    INVALID,
    SESSION_MINUTES,
    SUBMITTED,
    VALIDATED,
    AcceptedDocument,
    DocumentStatus,
    DocumentUuid,
    DuplicateSubmission,
    IdempotencyKeyMissing,
    RejectedDocument,
    SessionExpired,
    Submission,
    SubmissionUid,
    TaxpayerSession,
    TinCheck,
    WrongCompany,
)
from .signing import Signer


def _require_key_in_a_job(idempotency_key):
    """A job's external effect must carry the job's key, or a retry after a crash could repeat it (ticket #27)."""
    from core import jobs  # not at import: the jobs module does not depend on the ports package

    if idempotency_key is None and jobs.in_job():
        raise IdempotencyKeyMissing("a job made an external call without its idempotency key")


class FakeKeyProvider:
    signature = b"fake-signature"

    def __init__(self):
        self.signings = []

    def sign(self, certificate_reference, digest):
        self.signings.append((certificate_reference, digest))
        return self.signature


class FakeGateway:
    """Answers as LHDN's published contract does (D58 (7)); a test decides what LHDN says by calling the helpers below.

    A session submits only documents signed for its own company and reaches only its own taxpayer's documents, so a
    caller that mixes up clients fails against the fake. LHDN has no idempotency key, so a repeat is answered with an
    error and never with the first answer: the same payload within 10 minutes is a 422, the same internal number within
    2 hours is rejected DV302, and neither returns the original uuid.
    """

    PAYLOAD_REPEAT = timedelta(minutes=10)
    NUMBER_REPEAT = timedelta(hours=2)
    TIN_SHAPE = re.compile(r"^[A-Z]{1,2}\d{8,12}$")

    def __init__(self):
        self.key_provider = FakeKeyProvider()
        self.now = timezone.now  # a test replaces it to move time
        self.accepted_client_ids = None  # None: any client ID logs in; a set: only these (others get the 401 LHDN gives)
        self.authorised_tins = None  # None: any taxpayer has authorised us; a set: only these (others get LHDN's 403)
        self.tins_validated = []
        self.unknown_tins = set()  # well-formed TINs that LHDN answers "not found" for
        self.submissions = []
        self.documents = {}  # uuid -> DocumentStatus
        self.cancellations = []
        self.reject_code_numbers = {}  # internal number -> the error code LHDN rejects it with
        self.lose_next_response = False  # LHDN accepts the next submit, but the answer never arrives
        self.drop_next_request = False  # the next submit never reaches LHDN
        self._meta = {}  # uuid -> what LHDN knows about the document besides its status
        self._payloads = {}  # (represented party, payload fingerprint) -> when it was accepted

    # --- what a test does to LHDN's side

    def validate(self, uuid):
        status = self.documents[uuid]
        status.state, status.date_time_validated = VALIDATED, self.now()
        status.long_id = f"fake-long-id-{uuid}"

    def invalidate(self, uuid, reason):
        status = self.documents[uuid]
        status.state, status.reason, status.long_id = INVALID, reason, None

    def request_rejection(self, uuid):
        self.documents[uuid].rejection_requested = True

    # --- the port

    def login_as_taxpayer(self, credentials, company_id, tin, rob=None):
        from core.ports.myinvois import NOT_AUTHORISED, AuthenticationFailed, PermissionDenied  # not at import: they extend the jobs errors

        if self.accepted_client_ids is not None and credentials.client_id not in self.accepted_client_ids:
            raise AuthenticationFailed("MyInvois refused the credentials (401)")
        if tin and self.authorised_tins is not None and tin not in self.authorised_tins:
            raise PermissionDenied(NOT_AUTHORISED)
        return TaxpayerSession(
            company_id=company_id,
            tin=tin,
            token=f"fake-token-{tin}",
            expires_at=self.now() + timedelta(minutes=SESSION_MINUTES),
            rob=rob,
        )

    def validate_tin(self, tin, id_type, id_value):
        if id_type not in ID_TYPES:
            raise ValueError(f"id_type must be one of {', '.join(ID_TYPES)}")
        self.tins_validated.append((tin, id_type, id_value))
        if not id_value:  # the one input the preprod sandbox answers 400 for (#42)
            return TinCheck.BAD_FORMAT
        # The sandbox answers 404 for a malformed TIN, a wrong id value and a mismatched id type alike.
        if not self.TIN_SHAPE.match(tin) or tin in self.unknown_tins:
            return TinCheck.NOT_FOUND
        return TinCheck.VALID

    def sign(self, session, document, code_number):
        return Signer(self.key_provider).sign(session, document, code_number)

    def _check(self, session):
        if self.now() >= session.expires_at:
            raise SessionExpired("the login has expired")

    def submit(self, session, signed, idempotency_key=None):
        from core.jobs import TransientError  # not at import: the jobs module does not depend on the ports package

        _require_key_in_a_job(idempotency_key)
        self._check(session)
        for document in signed:
            if document.company_id != session.company_id:
                raise WrongCompany(f"signed for company {document.company_id}, submitted for company {session.company_id}")
        if self.drop_next_request:
            self.drop_next_request = False
            raise TransientError("the request timed out")
        now = self.now()
        fingerprint = frozenset((d.code_number, d.document_hash) for d in signed)
        sent = self._payloads.get((session.onbehalf_of, fingerprint))
        if sent is not None and now - sent < self.PAYLOAD_REPEAT:
            raise DuplicateSubmission("DuplicateSubmission: this payload was submitted within the last 10 minutes")
        self._payloads[(session.onbehalf_of, fingerprint)] = now
        submission_uid = SubmissionUid(f"fake-submission-{len(self.submissions) + 1}")
        accepted, rejected = [], []
        for document in signed:
            code = document.code_number
            if code in self.reject_code_numbers:
                rejected.append(RejectedDocument(code, self.reject_code_numbers[code]))
            elif any(m["onbehalf_of"] == session.onbehalf_of and m["code"] == code and now - m["at"] < self.NUMBER_REPEAT for m in self._meta.values()):
                rejected.append(RejectedDocument(code, ALREADY_SUBMITTED))
            else:
                uuid = DocumentUuid(f"fake-uuid-{len(self.documents) + 1}")
                self.documents[uuid] = DocumentStatus(uuid=uuid, state=SUBMITTED)
                self._meta[uuid] = {"onbehalf_of": session.onbehalf_of, "code": code, "at": now, "submission": submission_uid}
                accepted.append(AcceptedDocument(code, uuid))
        self.submissions.append({"submission_uid": submission_uid, "onbehalf_of": session.onbehalf_of, "signed": list(signed)})
        if self.lose_next_response:
            self.lose_next_response = False
            raise TransientError("the response was lost")
        return Submission(submission_uid, accepted, rejected)

    def _own_document(self, session, uuid):
        if not isinstance(uuid, DocumentUuid):
            raise TypeError("a document is identified by its uuid, not by a submission uid or a bare string")
        if self._meta.get(uuid, {}).get("onbehalf_of") != session.onbehalf_of:
            raise KeyError(uuid)
        return self.documents[uuid]

    def poll_status(self, session, submission_uid):
        self._check(session)
        if not isinstance(submission_uid, SubmissionUid):
            raise TypeError("a submission is polled by its submission uid, not by a document uuid or a bare string")
        owned = [s for s in self.submissions if s["submission_uid"] == submission_uid and s["onbehalf_of"] == session.onbehalf_of]
        if not owned:
            raise KeyError(submission_uid)
        return [self.documents[u] for u, m in self._meta.items() if m["submission"] == submission_uid]

    def find_document(self, session, code_number):
        self._check(session)
        found = [u for u, m in self._meta.items() if m["onbehalf_of"] == session.onbehalf_of and m["code"] == code_number]
        return self.documents[found[-1]] if found else None

    def cancel(self, session, uuid, reason, idempotency_key=None):
        _require_key_in_a_job(idempotency_key)
        self._check(session)
        status = self._own_document(session, uuid)
        status.state = CANCELLED
        self.cancellations.append({"uuid": uuid, "reason": reason})


class FakeAI:
    def __init__(self):
        self.extractions = []
        self._answers = {}

    def extract(self, document, schema, idempotency_key=None):
        _require_key_in_a_job(idempotency_key)
        if idempotency_key in self._answers:
            return self._answers[idempotency_key]
        self.extractions.append((document, schema))
        if idempotency_key is not None:
            self._answers[idempotency_key] = {}
        return {}


class FakeEmail:
    def __init__(self):
        self.sent = []
        self._keys = set()

    def send(self, to, subject, body, idempotency_key=None):
        _require_key_in_a_job(idempotency_key)
        if idempotency_key in self._keys:
            return
        self.sent.append({"to": to, "subject": subject, "body": body})
        if idempotency_key is not None:
            self._keys.add(idempotency_key)


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data):
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]


class FakePorts:
    def __init__(self):
        self.gateway = FakeGateway()
        self.ai = FakeAI()
        self.email = FakeEmail()
        self.storage = FakeStorage()
