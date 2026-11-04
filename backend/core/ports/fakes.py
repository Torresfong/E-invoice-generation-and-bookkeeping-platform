"""In-memory fakes for the four ports. They record what callers send so tests can assert on it."""

from .interfaces import CANCELLED, SUBMITTED, DocumentStatus, TaxpayerSession
from .signing import Signer


class FakeKeyProvider:
    signature = b"fake-signature"

    def __init__(self):
        self.signings = []

    def sign(self, certificate_reference, digest):
        self.signings.append((certificate_reference, digest))
        return self.signature


class FakeGateway:
    """A test decides how LHDN answers by changing `statuses`; a document starts as submitted."""

    def __init__(self):
        self.key_provider = FakeKeyProvider()
        self.tins_validated = []
        self.submissions = []
        self.statuses = {}
        self.cancellations = []
        self.rejections = []

    def login_as_taxpayer(self, tin):
        return TaxpayerSession(tin=tin, token=f"fake-token-{tin}")

    def validate_tin(self, tin):
        self.tins_validated.append(tin)
        return True

    def sign(self, document, company_id):
        return Signer(self.key_provider).sign(document, company_id)

    def submit(self, session, signed):
        uid = f"fake-uid-{len(self.submissions) + 1}"
        self.submissions.append({"uid": uid, "tin": session.tin, "signed": signed})
        self.statuses[uid] = DocumentStatus(state=SUBMITTED)
        return uid

    def poll_status(self, session, uid):
        return self.statuses[uid]

    def cancel(self, session, uid, reason):
        self.statuses[uid].state = CANCELLED
        self.cancellations.append({"uid": uid, "reason": reason})

    def reject(self, session, uid, reason):
        self.statuses[uid].rejection_requested = True
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
