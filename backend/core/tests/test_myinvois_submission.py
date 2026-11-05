"""Submitting a document to MyInvois when LHDN has no idempotency key (D58 (5), (6); ticket #113).

The submit job records the invoice number and document hash first, looks the document up by its internal number after
an ambiguous failure, reads 422 and DV302 as "already submitted", and stops without re-signing a stale document.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from django.utils import translation

from core import jobs
from core.models import Job
from core.ports.interfaces import IntermediaryCredentials, Submission, SubmissionUid
from core.submission import DocumentRejected, StaleDocument, submit_invoice

DOCUMENT = b'{"invoice": "INV-0001"}'
CODE = "INV-0001"
HASH = hashlib.sha256(DOCUMENT).hexdigest()
NOW = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
CREDENTIALS = IntermediaryCredentials(client_id="erp-client-id", client_secret="erp-client-secret")

pytestmark = pytest.mark.django_db


class Attempts:
    """Stands in for the invoice record: remembers the number and hash written before LHDN is called."""

    def __init__(self, gateway):
        self.gateway = gateway
        self.records = []
        self.submissions_when_recorded = []

    def __call__(self, code_number, document_hash):
        earlier = any(r[0] == code_number for r in self.records)
        self.records.append((code_number, document_hash))
        self.submissions_when_recorded.append(len(self.gateway.submissions))
        return earlier


@pytest.fixture
def gateway(fake_ports):
    fake_ports.gateway.now = lambda: NOW
    return fake_ports.gateway


def run(gateway, attempts, issued_at=NOW - timedelta(hours=1), document=DOCUMENT, key=lambda step="": f"k:{step}"):
    session = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")
    return submit_invoice(
        gateway=gateway,
        session=session,
        document=document,
        code_number=CODE,
        issued_at=issued_at,
        now=NOW,
        owner_email="owner@acme.example",
        record_attempt=attempts,
        key=key,
    )


def test_a_document_is_recorded_by_number_and_hash_before_lhdn_is_called(gateway):
    attempts = Attempts(gateway)

    result = run(gateway, attempts)

    assert attempts.records == [(CODE, HASH)] and attempts.submissions_when_recorded == [0]
    assert result.uuid == gateway.find_document(gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890"), CODE).uuid
    assert result.recovered is False and len(gateway.submissions) == 1


def test_after_a_lost_response_the_document_is_looked_up_and_not_submitted_twice(gateway):
    attempts = Attempts(gateway)
    gateway.lose_next_response = True

    with pytest.raises(jobs.TransientError):
        run(gateway, attempts)
    result = run(gateway, attempts)

    assert result.recovered is True and len(gateway.submissions) == 1
    assert result.uuid in gateway.documents


def test_after_a_failure_that_never_reached_lhdn_the_document_is_submitted(gateway):
    attempts = Attempts(gateway)
    gateway.drop_next_request = True

    with pytest.raises(jobs.TransientError):
        run(gateway, attempts)
    result = run(gateway, attempts)

    assert result.recovered is False and len(gateway.submissions) == 1


def test_a_422_duplicate_is_read_as_already_submitted_and_the_uuid_is_recovered(gateway):
    first = run(gateway, Attempts(gateway))

    again = run(gateway, Attempts(gateway))  # a fresh record: this run does not know it submitted before

    assert again.uuid == first.uuid and again.recovered is True and len(gateway.submissions) == 1


def test_a_dv302_duplicate_is_read_as_already_submitted_and_the_uuid_is_recovered(gateway):
    first = run(gateway, Attempts(gateway))
    gateway.now = lambda: NOW + timedelta(minutes=30)  # past the 10 minutes of 422, inside the 2 hours of DV302

    again = run(gateway, Attempts(gateway), document=b'{"invoice": "INV-0001", "edited": true}')

    assert again.uuid == first.uuid and again.recovered is True and len(gateway.documents) == 1  # the repeat was refused, not accepted


def test_a_document_lhdn_refuses_for_another_reason_stops_the_job_with_its_code_only(gateway):
    gateway.reject_code_numbers[CODE] = "DV001"

    with pytest.raises(DocumentRejected) as refused:
        run(gateway, Attempts(gateway))

    assert isinstance(refused.value, jobs.PermanentError)
    assert "DV001" in str(refused.value) and "INV-0001" not in str(refused.value)


def test_a_document_issued_over_72_hours_ago_is_not_signed_nor_submitted_and_the_owner_is_told(gateway, fake_ports):
    attempts = Attempts(gateway)

    with pytest.raises(StaleDocument) as stale:
        run(gateway, attempts, issued_at=NOW - timedelta(hours=72, seconds=1))

    assert isinstance(stale.value, jobs.PermanentError)
    assert gateway.key_provider.signings == [] and gateway.submissions == [] and attempts.records == []
    (mail,) = fake_ports.email.sent
    assert mail["to"] == "owner@acme.example"
    assert CODE in mail["subject"] + mail["body"] and "approve" in mail["body"].lower()


def test_a_document_issued_exactly_72_hours_ago_is_still_submitted(gateway):
    result = run(gateway, Attempts(gateway), issued_at=NOW - timedelta(hours=72))

    assert result.uuid and len(gateway.submissions) == 1


def test_a_stale_document_in_a_job_fails_the_job_once_and_tells_the_owner_once(gateway, fake_ports, registry):
    @jobs.register("submit_stale")
    def handler(**payload):
        run(gateway, Attempts(gateway), issued_at=NOW - timedelta(days=4), key=jobs.idempotency_key)

    jobs.enqueue("submit_stale")

    jobs.run_pending()

    assert Job.objects.get().status == Job.FAILED
    assert len(fake_ports.email.sent) == 1 and gateway.key_provider.signings == []


def test_the_stale_notice_is_available_in_chinese(gateway, fake_ports):
    with translation.override("zh-hans"), pytest.raises(StaleDocument):
        run(gateway, Attempts(gateway), issued_at=NOW - timedelta(days=4))

    (mail,) = fake_ports.email.sent
    assert "重新批准" in mail["subject"] and CODE in mail["subject"]


def test_an_answer_with_no_result_for_the_document_is_retried_later(gateway):
    gateway.submit = lambda session, signed, idempotency_key=None: Submission(SubmissionUid("s"), [], [])

    with pytest.raises(jobs.TransientError):
        run(gateway, Attempts(gateway))


def test_a_document_lhdn_already_holds_is_recovered_even_when_the_retry_comes_after_72_hours(gateway, fake_ports):
    attempts = Attempts(gateway)
    gateway.lose_next_response = True
    with pytest.raises(jobs.TransientError):
        run(gateway, attempts)

    result = run(gateway, attempts, issued_at=NOW - timedelta(hours=80))

    assert result.recovered is True and len(gateway.submissions) == 1
    assert fake_ports.email.sent == []  # the owner is not asked to approve a document LHDN already has
