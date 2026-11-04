"""The four external boundaries. Later tickets widen these; callers depend only on these shapes."""

from dataclasses import dataclass, field
from typing import Protocol

# What MyInvois says about a submitted document. A buyer's rejection request is not one of these: it is a marker on a
# validated document, which stays valid until the owner cancels it.
SUBMITTED = "submitted"
VALIDATED = "validated"
INVALID = "invalid"
CANCELLED = "cancelled"


class WrongCompany(RuntimeError):
    """A document signed for one company was submitted through another company's session."""


@dataclass(frozen=True)
class TaxpayerSession:
    """A login as intermediary for one company's taxpayer (D1). The TIN and token are left out of repr so neither can reach a log."""

    company_id: int
    tin: str = field(repr=False)
    token: str = field(repr=False)


@dataclass(frozen=True)
class SignedDocument:
    company_id: int
    document: bytes = field(repr=False)
    signature: bytes = field(repr=False)


@dataclass
class DocumentStatus:
    state: str
    rejection_requested: bool = False


class MyInvoisGateway(Protocol):
    """Every call to MyInvois (D14). Swapping the implementation, or LHDN changing, must not touch callers."""

    def login_as_taxpayer(self, company_id: int, tin: str) -> TaxpayerSession: ...

    def validate_tin(self, tin: str) -> bool: ...

    def sign(self, session: TaxpayerSession, document: bytes) -> SignedDocument:
        """An implementation signs only through signing.Signer, which is what logs it (D13)."""
        ...

    def submit(self, session: TaxpayerSession, signed: SignedDocument) -> str:
        """Refuses with WrongCompany a document signed for another company than the session's."""
        ...

    def poll_status(self, session: TaxpayerSession, uid: str) -> DocumentStatus: ...

    def cancel(self, session: TaxpayerSession, uid: str, reason: str) -> None: ...

    def reject(self, session: TaxpayerSession, uid: str, reason: str) -> None: ...


class KeyProvider(Protocol):
    """The key vault (D13), reached only by signing.Signer behind the gateway: not a fifth port.

    It signs with the key it holds, so a key that cannot leave the vault fits the same shape.
    """

    def sign(self, certificate_reference: str, digest: bytes) -> bytes: ...


class AIProvider(Protocol):
    def extract(self, document: bytes, schema: dict) -> dict: ...


class EmailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ObjectStorage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def get(self, key: str) -> bytes: ...
