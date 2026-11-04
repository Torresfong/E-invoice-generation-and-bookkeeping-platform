"""In-memory fakes for the four ports. They record what callers send so tests can assert on it."""

from .interfaces import CANCELLED, SUBMITTED, DocumentStatus, TaxpayerSession, WrongCompany
from .signing import Signer


class FakeKeyProvider:
    signature = b"fake-signature"

    def __init__(self):
        self.signings = []

    def sign(self, certificate_reference, digest):
        self.signings.append((certificate_reference, digest))
        return self.signature


class FakeGateway:
    """A test decides how LHDN answers by changing `statuses`; a document starts as submitted.

    A session submits only documents signed for its own company and reaches only its own taxpayer's submitted documents,
    so a caller that mixes up clients fails against the fake.
    """

    def __init__(self):
        self.key_provider = FakeKeyProvider()
        self.tins_validated = []
        self.submissions = []
        self.statuses = {}
        self.cancellations = []
        self.rejections = []

    def login_as_taxpayer(self, company_id, tin):
        return TaxpayerSession(company_id=company_id, tin=tin, token=f"fake-token-{tin}")

    def validate_tin(self, tin):
        self.tins_validated.append(tin)
        return True

    def sign(self, session, document):
        return Signer(self.key_provider).sign(session, document)

    def submit(self, session, signed):
        if signed.company_id != session.company_id:
            raise WrongCompany(f"signed for company {signed.company_id}, submitted for company {session.company_id}")
        uid = f"fake-uid-{len(self.submissions) + 1}"
        self.submissions.append({"uid": uid, "tin": session.tin, "signed": signed})
        self.statuses[uid] = DocumentStatus(state=SUBMITTED)
        return uid

    def _own_status(self, session, uid):
        if not any(s["uid"] == uid and s["tin"] == session.tin for s in self.submissions):
            raise KeyError(uid)
        return self.statuses[uid]

    def poll_status(self, session, uid):
        return self._own_status(session, uid)

    def cancel(self, session, uid, reason):
        self._own_status(session, uid).state = CANCELLED
        self.cancellations.append({"uid": uid, "reason": reason})

    def reject(self, session, uid, reason):
        self._own_status(session, uid).rejection_requested = True
        self.rejections.append({"uid": uid, "reason": reason})


class FakeAI:
    def __init__(self):
        self.extractions = []

    def extract(self, document, schema):
        self.extractions.append((document, schema))
        return {}


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})


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
