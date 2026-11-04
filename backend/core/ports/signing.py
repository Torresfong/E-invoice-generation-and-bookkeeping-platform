"""Signing a document for MyInvois (D13). The one route to the signing key, and the one place a signing is logged.

The key stays behind a KeyProvider: the application holds a reference to a certificate in the vault, never the key.
The log line is written before the key is used, so a signing that fails in the vault is logged too. It says what was
signed by hash and never holds the document (ADR 0001 rule 5).
"""

import hashlib
import logging

from django.conf import settings

from .interfaces import KeyProvider, SignedDocument

logger = logging.getLogger(__name__)


class CertificateNotConfigured(RuntimeError):
    pass


class Signer:
    def __init__(self, key_provider: KeyProvider):
        self._key_provider = key_provider

    def sign(self, document: bytes, company_id: int) -> SignedDocument:
        environment = settings.MYINVOIS_ENVIRONMENT
        reference = settings.MYINVOIS_CERTIFICATES[environment]
        if not reference:
            raise CertificateNotConfigured(f"No MyInvois certificate is configured for {environment}")
        digest = hashlib.sha256(document).digest()
        logger.info(
            "signing document sha256=%s company=%s environment=%s certificate=%s",
            digest.hex(), company_id, environment, reference,
        )
        return SignedDocument(document=document, signature=self._key_provider.sign(reference, digest))
