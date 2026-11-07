"""Gapless document numbers, one sequence per company and document type (D18, D44, D59 (6), D60 (2)).

A number is taken once, when the owner approves (D44). Drafts have no number, so deleting one cannot leave a gap.
`allocate_number` must run inside the transaction that approves: it locks the sequence row until that transaction
ends, so concurrent approvals take turns, and if the approval rolls back the number was never used. A cancelled
document keeps its number and an approval that is repeated after 72 hours keeps the number it already has (D60 (2)),
so neither calls this again.

The database enforces the rest (migration 0034): the counter moves forward by one and never back, and the prefix and
starting number cannot change once a number has been issued.
"""

from django.db import connection, transaction

from .models import DocumentSequence

INVOICE = "invoice"
CREDIT_NOTE = "credit_note"
DEBIT_NOTE = "debit_note"
REFUND_NOTE = "refund_note"
DOCUMENT_TYPES = (INVOICE, CREDIT_NOTE, DEBIT_NOTE, REFUND_NOTE)

# SQL Account's import template holds 20 characters of document number (D51 (3)).
MAX_NUMBER_LENGTH = 20
MAX_PREFIX_LENGTH = 10
MAX_DIGITS = 10
MAX_START_NUMBER = 10**MAX_DIGITS - 1


class SequenceNotConfigured(Exception):
    """The company has no sequence for this document type, or the signed-in user cannot see it."""


class SequenceInUse(Exception):
    """A number has already been issued, so the prefix and starting number can no longer change."""


class InvalidConfiguration(ValueError):
    pass


def format_number(prefix, number):
    return f"{prefix}{number}"


def configure_sequence(company, document_type, prefix, start_number):
    """Set the prefix and the number the first approval will take, continuing from the owner's existing books.

    Allowed only until the first number is issued; afterwards it raises `SequenceInUse`. The caller checks that the
    user is the company owner (`core.permissions`).
    """
    if document_type not in DOCUMENT_TYPES:
        raise InvalidConfiguration(f"unknown document type {document_type!r}")
    if not isinstance(start_number, int) or isinstance(start_number, bool) or not 1 <= start_number <= MAX_START_NUMBER:
        raise InvalidConfiguration("the starting number must be a whole number of at least 1 and at most 10 digits")
    if len(prefix) > MAX_PREFIX_LENGTH:
        raise InvalidConfiguration(f"the prefix may have at most {MAX_PREFIX_LENGTH} characters")
    if len(format_number(prefix, start_number)) > MAX_NUMBER_LENGTH:
        raise InvalidConfiguration(f"the prefix and number may have at most {MAX_NUMBER_LENGTH} characters together")

    with transaction.atomic():
        sequence, created = DocumentSequence.objects.select_for_update().get_or_create(
            company=company,
            document_type=document_type,
            defaults={"prefix": prefix, "start_number": start_number, "next_number": start_number},
        )
        if created:
            return sequence
        if sequence.next_number != sequence.start_number:
            raise SequenceInUse(f"{document_type} numbers have already been issued for this company")
        sequence.prefix, sequence.start_number, sequence.next_number = prefix, start_number, start_number
        sequence.save(update_fields=["prefix", "start_number", "next_number"])
        return sequence


def allocate_number(company, document_type):
    """Take the next number of the sequence and return it with its prefix, e.g. "INV-1043".

    Call inside the approving transaction. Raises `SequenceNotConfigured` if the company has no such sequence.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("a number must be taken inside the transaction that approves the document")
    sequence = DocumentSequence.objects.select_for_update().filter(company=company, document_type=document_type).first()
    if sequence is None:
        raise SequenceNotConfigured(f"no {document_type} sequence for company {company.pk}")
    number = format_number(sequence.prefix, sequence.next_number)
    if len(number) > MAX_NUMBER_LENGTH:
        raise InvalidConfiguration(f"the next number no longer fits in {MAX_NUMBER_LENGTH} characters")
    DocumentSequence.objects.filter(pk=sequence.pk).update(next_number=sequence.next_number + 1)
    return number
