"""The four external boundaries. Later tickets widen these; callers depend only on these shapes."""

from dataclasses import dataclass, field
from typing import Protocol

# What MyInvois says about a submitted document. A buyer's rejection request is not one of these: it is a marker on a
# validated document, which stays valid until the owner cancels it.
SUBMITTED = "submitted"
VALIDATED = "validated"
INVALID = "invalid"
CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaxpayerSession:
    """A login as intermediary on behalf of one taxpayer (D1). The token is left out of repr so it cannot reach a log."""

    tin: str
    token: str = field(repr=False)


@dataclass(frozen=True)
class SignedDocument:
    document: bytes
    signature: bytes


@dataclass
class DocumentStatus:
    state: str
    rejection_requested: bool = False


class MyInvoisGateway(Protocol):
    """Every call to MyInvois (D14). Swapping the implementation, or LHDN changing, must not touch callers."""

    def login_as_taxpayer(self, tin: str) -> TaxpayerSession: ...

    def validate_tin(self, tin: str) -> bool: ...

    def sign(self, document: bytes, company_id: int) -> SignedDocument: ...

    def submit(self, session: TaxpayerSession, signed: SignedDocument) -> str: ...

    def poll_status(self, session: TaxpayerSession, uid: str) -> DocumentStatus: ...

    def cancel(self, session: TaxpayerSession, uid: str, reason: str) -> None: ...

    def reject(self, session: TaxpayerSession, uid: str, reason: str) -> None: ...


class KeyProvider(Protocol):
    """The key vault (D13). It signs with the key it holds, so a key that cannot leave the vault fits the same shape."""

    def sign(self, certificate_reference: str, digest: bytes) -> bytes: ...


class AIProvider(Protocol):
    def extract(self, document: bytes, schema: dict) -> dict: ...


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...
