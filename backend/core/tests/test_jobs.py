import uuid
from decimal import Decimal

import pytest

from core import jobs
from core.models import Job


@pytest.mark.django_db
def test_queued_job_runs_in_process_with_its_payload(registry):
    seen = []
    jobs.register("record")(lambda **payload: seen.append(payload))

    jobs.enqueue("record", value=7)
    assert seen == []  # nothing runs until the runner is called

    assert jobs.run_pending() == 1
    assert seen == [{"value": 7}]
    assert Job.objects.get().status == Job.DONE


@pytest.mark.django_db
def test_a_failing_job_is_recorded_and_does_not_block_the_next_one(registry):
    seen = []

    def boom(**payload):
        raise ValueError("bad input")

    jobs.register("boom")(boom)
    jobs.register("ok")(lambda **payload: seen.append("ok"))

    jobs.enqueue("boom")
    jobs.enqueue("ok")
    jobs.run_pending()

    failed = Job.objects.get(name="boom")
    assert failed.status == Job.PENDING  # an unclassified error is retried later (ticket #27)
    assert failed.error == "ValueError"
    assert seen == ["ok"]


@pytest.mark.django_db
def test_a_failed_job_never_stores_or_logs_the_content_that_made_it_fail(registry, caplog):
    secret = "Supplier ACME Sdn Bhd, TIN C2345678901, total RM 18,450.00"

    def echo_content(document_id):
        raise ValueError(f"cannot parse total in: {secret}")

    jobs.register("extract")(echo_content)
    jobs.enqueue("extract", document_id=7)

    with caplog.at_level("DEBUG"):
        jobs.run_pending()

    failed = Job.objects.get()
    assert failed.status == Job.PENDING  # an unclassified error is retried later (ticket #27)
    assert "ACME" not in failed.error and "C2345678901" not in failed.error
    assert "ACME" not in caplog.text and "C2345678901" not in caplog.text
    assert "ValueError" in caplog.text  # still enough to debug from: which job, which error class


@pytest.mark.django_db
@pytest.mark.parametrize(
    "value",
    [
        7,
        True,
        None,
        uuid.uuid4(),
        "INV-2026-000123",
        "x" * jobs.MAX_STRING_LENGTH,
    ],
)
def test_identifiers_are_accepted_in_a_payload(registry, value):
    jobs.enqueue("record", ref=value)

    assert Job.objects.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize(
    "value",
    [
        "Supplier ACME Sdn Bhd, TIN C2345678901, total RM 18,450.00",
        "x" * (jobs.MAX_STRING_LENGTH + 1),
        18450.0,
        Decimal("18450.00"),
        {"tin": "C2345678901"},
        ["C2345678901"],
    ],
)
def test_content_is_refused_in_a_payload_and_never_echoed_back(registry, value):
    with pytest.raises(jobs.JobPayloadError) as excinfo:
        jobs.enqueue("record", document_text=value)

    assert Job.objects.count() == 0
    assert "C2345678901" not in str(excinfo.value) and "ACME" not in str(excinfo.value)
    assert "document_text" in str(excinfo.value)  # names the offending key, never the value
