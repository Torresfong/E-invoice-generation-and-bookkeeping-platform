"""Job execution is safe around slow, side-effecting handlers (ticket #27, ADR 0001, D36).

A worker claims a job in a short transaction, runs the handler outside the row lock under a lease, and records the
outcome. Time comes from `jobs.clock`, which these tests replace with a clock they move by hand.
"""

import inspect
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from importlib import import_module
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from core import jobs, ports
from core.models import Job
from core.ports.interfaces import DuplicateSubmission, IntermediaryCredentials, SignedDocument, WrongCompany
from core.ports.myinvois import RateLimited

from .test_tenancy import query


pytestmark = pytest.mark.django_db


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(jobs, "clock", fake)
    return fake


def test_a_job_is_running_under_a_lease_while_its_handler_runs_and_no_other_worker_takes_it(registry, clock):
    seen = {}

    def handler(**payload):
        job = Job.objects.get()
        seen["status"], seen["lease"], seen["attempts"] = job.status, job.lease_expires_at, job.attempts
        seen["second_worker"] = jobs.run_pending()

    jobs.register("slow")(handler)
    jobs.enqueue("slow")

    assert jobs.run_pending() == 1

    assert seen["status"] == Job.RUNNING
    assert seen["lease"] == clock.now + timedelta(seconds=jobs.LEASE_SECONDS)
    assert seen["attempts"] == 1
    assert seen["second_worker"] == 0
    job = Job.objects.get()
    assert job.status == Job.DONE and job.lease_expires_at is None and job.attempts == 1


class WorkerCrash(BaseException):
    """The process dies mid-handler: not an error the handler can answer, and nothing records an outcome."""


def test_a_crashed_worker_neither_loses_nor_double_runs_its_job(registry, clock):
    runs = []

    def handler(**payload):
        runs.append(clock.now)
        if len(runs) == 1:
            raise WorkerCrash

    jobs.register("slow")(handler)
    jobs.enqueue("slow")

    with pytest.raises(WorkerCrash):
        jobs.run_pending()
    assert Job.objects.get().status == Job.RUNNING

    # While the lease holds, the job is presumed alive: no other worker runs it.
    clock.advance(seconds=jobs.LEASE_SECONDS - 1)
    assert jobs.run_pending() == 0 and len(runs) == 1

    # Once it expires the sweep returns the job to pending, counts the lost attempt and backs off before the retry.
    clock.advance(seconds=2)
    assert jobs.run_pending() == 0
    job = Job.objects.get()
    assert (job.status, job.attempts, job.lease_expires_at) == (Job.PENDING, 1, None)
    assert job.run_after == clock.now + timedelta(seconds=30)

    clock.advance(seconds=30)
    assert jobs.run_pending() == 1
    job.refresh_from_db()
    assert (job.status, job.attempts, len(runs)) == (Job.DONE, 2, 2)

    clock.advance(days=1)
    assert jobs.run_pending() == 0 and len(runs) == 2


def test_a_transient_error_is_retried_with_exponential_backoff_until_the_attempts_run_out(registry, clock):
    def flaky(**payload):
        raise jobs.TransientError("the gateway timed out")

    jobs.register("flaky")(flaky)
    jobs.enqueue("flaky")

    for attempt, delay in enumerate([30, 60, 120, 240], start=1):
        assert jobs.run_pending() == 1
        job = Job.objects.get()
        assert (job.status, job.attempts, job.error) == (Job.PENDING, attempt, "TransientError")
        assert job.run_after == clock.now + timedelta(seconds=delay)

        clock.advance(seconds=delay - 1)
        assert jobs.run_pending() == 0  # not due yet
        clock.advance(seconds=1)

    assert jobs.run_pending() == 1
    job = Job.objects.get()
    assert (job.status, job.attempts, job.error) == (Job.FAILED, 5, "TransientError")
    assert job.finished_at == clock.now and job.run_after is None

    clock.advance(days=1)
    assert jobs.run_pending() == 0


def test_a_permanent_error_fails_the_job_at_once(registry, clock):
    def rejected(**payload):
        raise jobs.PermanentError("the document was refused")

    jobs.register("rejected")(rejected)
    jobs.enqueue("rejected")

    assert jobs.run_pending() == 1

    job = Job.objects.get()
    assert (job.status, job.attempts, job.error) == (Job.FAILED, 1, "PermanentError")
    assert job.finished_at == clock.now


def _fail_once_with(registry, error):
    def handler(**payload):
        raise error

    jobs.register("limited")(handler)
    jobs.enqueue("limited")
    assert jobs.run_pending() == 1
    return Job.objects.get()


def test_a_rate_limit_that_asks_for_a_longer_wait_than_the_backoff_is_waited_out(registry, clock):
    job = _fail_once_with(registry, RateLimited("slow down", retry_after=120))  # the backoff alone would be 30

    assert (job.status, job.error) == (Job.PENDING, "RateLimited")
    assert job.run_after == clock.now + timedelta(seconds=120)


def test_a_rate_limit_never_shortens_the_backoff(registry, clock):
    job = _fail_once_with(registry, RateLimited("slow down", retry_after=5))

    assert job.run_after == clock.now + timedelta(seconds=30)


def test_a_rate_limit_without_a_retry_after_uses_the_backoff(registry, clock):
    job = _fail_once_with(registry, RateLimited("slow down"))

    assert job.run_after == clock.now + timedelta(seconds=30)


def test_an_absurd_retry_after_is_capped_so_a_job_is_never_parked_for_days(registry, clock):
    job = _fail_once_with(registry, RateLimited("slow down", retry_after=10**7))

    assert job.run_after == clock.now + timedelta(seconds=jobs.MAX_RETRY_AFTER_SECONDS)


def test_an_unknown_error_is_retried_but_fewer_times_than_a_transient_one(registry, clock):
    def surprise(**payload):
        raise ValueError("nobody said whether this passes")

    jobs.register("surprise")(surprise)
    jobs.enqueue("surprise")

    for attempt, delay in enumerate([30, 60], start=1):
        jobs.run_pending()
        job = Job.objects.get()
        assert (job.status, job.attempts, job.error) == (Job.PENDING, attempt, "ValueError")
        assert job.run_after == clock.now + timedelta(seconds=delay)
        clock.advance(seconds=delay)

    jobs.run_pending()
    job = Job.objects.get()
    assert (job.status, job.attempts, job.error) == (Job.FAILED, 3, "ValueError")


def test_a_job_without_a_handler_fails_at_once(registry, clock):
    jobs.enqueue("nobody-handles-this")

    jobs.run_pending()

    job = Job.objects.get()
    assert (job.status, job.attempts, job.error) == (Job.FAILED, 1, "NoHandler")


def test_a_job_that_crashes_every_worker_that_takes_it_ends_up_failed(registry, clock):
    def poison(**payload):
        raise WorkerCrash

    jobs.register("poison")(poison)
    jobs.enqueue("poison")

    for attempt in range(1, 6):
        clock.advance(days=1)  # past any backoff
        with pytest.raises(WorkerCrash):
            jobs.run_pending()
        assert Job.objects.get().attempts == attempt
        clock.advance(seconds=jobs.LEASE_SECONDS + 1)
        jobs.run_pending()  # the sweep

    job = Job.objects.get()
    assert (job.status, job.attempts, job.error) == (Job.FAILED, 5, "LeaseExpired")
    assert job.finished_at == clock.now
    clock.advance(days=1)
    assert jobs.run_pending() == 0


@pytest.mark.django_db(transaction=True)
def test_a_worker_that_outlives_its_lease_cannot_overwrite_the_retry_or_keep_its_writes(registry, clock):
    """Two real connections: the slow worker holds no lock on the job, so a second worker can sweep and finish it."""
    runs = []
    second_worker = {}

    def second_worker_run():
        try:
            clock.advance(seconds=jobs.LEASE_SECONDS + 1)
            jobs.run_pending()  # the sweep returns the job to pending, behind a backoff
            clock.advance(seconds=30)
            second_worker["ran"] = jobs.run_pending()
        except Exception as error:  # reported on the main thread
            second_worker["error"] = error
        finally:
            connection.close()

    def handler(**payload):
        runs.append(clock.now)
        if len(runs) > 1:
            return
        jobs.enqueue("side-effect-of-the-slow-run")
        thread = threading.Thread(target=second_worker_run)
        thread.start()
        thread.join(timeout=20)
        assert not thread.is_alive()

    jobs.register("slow")(handler)
    job = jobs.enqueue("slow")

    jobs.run_pending()

    assert second_worker == {"ran": 1}
    job.refresh_from_db()
    assert (job.status, job.attempts, len(runs)) == (Job.DONE, 2, 2)
    # The late worker's own database writes were rolled back with its discarded outcome.
    assert not Job.objects.filter(name="side-effect-of-the-slow-run").exists()


def _send_everything(ports, key):
    """One job's external effects: an email, a MyInvois submission and an AI extraction, each under its own step."""
    session = ports.gateway.login_as_taxpayer(IntermediaryCredentials("id", "secret"), 1, "C1234567890")
    signed = SignedDocument(company_id=1, code_number=key(), document=key().encode(), signature=b"signature")  # one document per job
    ports.email.send("owner@acme.example", "Invoice ready", "Hello", idempotency_key=key("email"))
    try:
        ports.gateway.submit(session, [signed], idempotency_key=key("submit"))
    except DuplicateSubmission:
        pass  # LHDN has no idempotency key: a repeat is answered with a 422, and the document is already there
    ports.ai.extract(b"receipt", {"type": "object"}, idempotency_key=key("extract"))


def test_a_retry_after_a_crash_that_followed_the_external_calls_does_not_repeat_their_effects(
    registry, clock, fake_ports
):
    attempts = []

    def handler(**payload):
        attempts.append(jobs.idempotency_key())
        _send_everything(ports.get(), jobs.idempotency_key)
        if len(attempts) == 1:
            raise WorkerCrash  # after the effects happened, before the outcome was recorded

    jobs.register("notify")(handler)
    jobs.enqueue("notify")

    with pytest.raises(WorkerCrash):
        jobs.run_pending()
    clock.advance(seconds=jobs.LEASE_SECONDS + 1)
    jobs.run_pending()  # the sweep
    clock.advance(seconds=30)
    jobs.run_pending()

    assert Job.objects.get().status == Job.DONE
    assert len(attempts) == 2 and attempts[0] == attempts[1]  # the same key on every attempt
    assert len(fake_ports.email.sent) == 1
    assert len(fake_ports.gateway.submissions) == 1
    assert len(fake_ports.ai.extractions) == 1


def test_each_job_has_its_own_key_and_each_step_of_a_job_its_own(registry, fake_ports):
    keys = []

    def handler(**payload):
        keys.append((jobs.idempotency_key(), jobs.idempotency_key("a"), jobs.idempotency_key("b")))
        _send_everything(ports.get(), jobs.idempotency_key)

    jobs.register("notify")(handler)
    jobs.enqueue("notify")
    jobs.enqueue("notify")

    jobs.run_pending()

    assert len({key for triple in keys for key in triple}) == 6
    # Identical messages from two jobs are two effects; the steps of one job are three.
    assert len(fake_ports.email.sent) == 2
    assert len(fake_ports.gateway.submissions) == 2
    assert len(fake_ports.ai.extractions) == 2


def test_an_external_call_from_a_job_without_a_key_is_refused(registry, fake_ports):
    def handler(**payload):
        ports.get().email.send("owner@acme.example", "Invoice ready", "Hello")

    jobs.register("notify")(handler)
    jobs.enqueue("notify")

    jobs.run_pending()

    assert Job.objects.get().error == "IdempotencyKeyMissing"
    assert fake_ports.email.sent == []


def test_a_key_is_only_available_inside_a_job():
    with pytest.raises(RuntimeError):
        jobs.idempotency_key()


def _orm_write_that_fails():
    Job.objects.create(name=None)


def _raw_statement_that_fails():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 / 0")


@pytest.mark.parametrize("failing_statement", [_orm_write_that_fails, _raw_statement_that_fails])
def test_a_handler_that_swallows_a_database_error_and_returns_is_failed_not_done(registry, failing_statement):
    def handler(**payload):
        try:
            failing_statement()
        except DatabaseError:
            pass  # the transaction is now unusable, whatever the handler thinks

    jobs.register("careless")(handler)
    jobs.enqueue("careless")

    jobs.run_pending()

    job = Job.objects.get()
    assert (job.status, job.attempts, job.error) == (Job.FAILED, 1, "SwallowedDatabaseError")


def test_a_handler_that_recovers_from_a_database_error_in_its_own_savepoint_is_done(registry):
    def handler(**payload):
        try:
            with transaction.atomic():
                _raw_statement_that_fails()
        except DatabaseError:
            pass  # rolled back to the savepoint: the transaction is healthy

    jobs.register("careful")(handler)
    jobs.enqueue("careful")

    jobs.run_pending()

    assert Job.objects.get().status == Job.DONE


def test_a_failure_is_logged_at_error_level_with_where_it_failed_and_nothing_of_the_jobs_content(registry, caplog):
    secret = "Supplier ACME Sdn Bhd, TIN C2345678901, total RM 18,450.00"
    raised_on = []

    def extract(document_ref):
        raised_on.append(inspect.currentframe().f_lineno + 1)
        raise ValueError(f"cannot parse total in: {secret}")

    jobs.register("extract")(extract)
    job = jobs.enqueue("extract", document_ref="INV-2026-000123")

    with caplog.at_level("DEBUG"):
        jobs.run_pending()

    [record] = [r for r in caplog.records if r.name == "core.jobs"]
    assert record.levelname == "ERROR"
    for expected in [str(job.pk), "extract", "ValueError", f"test_job_execution.py:{raised_on[0]}"]:
        assert expected in record.getMessage()
    for content in ["ACME", "C2345678901", "18,450", "INV-2026-000123", "document_ref"]:
        assert content not in caplog.text
    assert record.exc_info is None  # a traceback would carry the exception message


def test_the_number_of_failed_jobs_of_every_company_is_exposed_for_an_alert(registry, world):
    def refuse(**payload):
        raise jobs.PermanentError("refused")

    jobs.register("refuse")(refuse)
    jobs.register("fine")(lambda **payload: None)
    for company in [world["acme"], world["beta"], None]:
        jobs.enqueue("refuse", company=company)
    jobs.enqueue("fine", company=world["acme"])
    assert jobs.failed_count() == 0

    jobs.run_pending()

    assert jobs.failed_count() == 3
    out = StringIO()
    call_command("failed_jobs", stdout=out)
    assert out.getvalue().strip() == "3"


def test_only_the_worker_may_count_failed_jobs_across_companies(db):
    assert query("SELECT has_function_privilege('worker', 'failed_job_count()', 'EXECUTE')") == [(True,)]
    assert query("SELECT has_function_privilege('web', 'failed_job_count()', 'EXECUTE')") == [(False,)]


# --- purging finished jobs ---------------------------------------------------------------------------------


@pytest.fixture
def old_and_new_jobs(clock, settings, world):
    """Jobs of every state, in two companies and as system jobs, around a 30 day retention."""
    settings.JOB_RETENTION_DAYS = 30
    day = timedelta(days=1)
    made = {}
    for label, status, age in [
        ("old_done", Job.DONE, 31 * day),
        ("old_failed", Job.FAILED, 31 * day),
        ("recent_done", Job.DONE, 29 * day),
        ("recent_failed", Job.FAILED, 1 * day),
    ]:
        for company_label, company in [("acme", world["acme"]), ("beta", world["beta"]), ("system", None)]:
            made[f"{label}_{company_label}"] = Job.objects.create(
                name="x", company=company, status=status, finished_at=clock.now - age
            )
    made["pending"] = Job.objects.create(name="x", status=Job.PENDING, run_after=clock.now + 2 * day)
    made["running"] = Job.objects.create(name="x", status=Job.RUNNING, lease_expires_at=clock.now + day)
    return made


def test_the_purge_job_removes_old_finished_jobs_and_keeps_everything_else(registry, clock, old_and_new_jobs):
    jobs.schedule_purge()

    assert jobs.run_pending() == 1

    kept = set(Job.objects.filter(name="x").values_list("pk", flat=True))
    expected = {
        job.pk
        for label, job in old_and_new_jobs.items()
        if label.startswith(("recent_", "pending", "running"))
    }
    assert kept == expected
    # Old failed jobs go too: the alert counts failures inside the retention window.
    assert not Job.objects.filter(pk__in=[j.pk for label, j in old_and_new_jobs.items() if label.startswith("old_")]).exists()


def test_the_purge_queues_its_own_next_run_and_schedule_purge_does_not_double_it(registry, clock):
    jobs.schedule_purge()
    jobs.schedule_purge()
    assert Job.objects.filter(name=jobs.PURGE_JOB).count() == 1

    assert jobs.run_pending() == 1

    later = Job.objects.get(name=jobs.PURGE_JOB, status=Job.PENDING)
    assert later.run_after == clock.now + timedelta(days=1)
    assert later.company_id is None
    jobs.schedule_purge()  # one is already waiting
    assert Job.objects.filter(name=jobs.PURGE_JOB, status=Job.PENDING).count() == 1

    assert jobs.run_pending() == 0  # not due
    clock.advance(days=1)
    assert jobs.run_pending() == 1


def test_a_job_of_a_company_cannot_purge(registry, clock, world, old_and_new_jobs):
    def handler(**payload):
        jobs.purge_finished()

    jobs.register("nosy")(handler)
    jobs.enqueue("nosy", company=world["acme"])

    jobs.run_pending()

    assert Job.objects.filter(pk=old_and_new_jobs["old_done_beta"].pk).exists()
    assert Job.objects.filter(pk=old_and_new_jobs["old_failed_acme"].pk).exists()


def test_the_schedule_command_queues_the_purge_once(registry):
    call_command("schedule_jobs")
    call_command("schedule_jobs")

    assert Job.objects.filter(name=jobs.PURGE_JOB, status=Job.PENDING).count() == 1


def test_the_job_execution_migration_reverses_and_applies_again(db):
    migration = import_module("core.migrations.0033_job_execution")
    ours = ["claim_next_job", "release_expired_jobs", "failed_job_count", "purge_finished_jobs"]
    count = "SELECT count(*) FROM pg_proc WHERE proname = ANY(%s)"
    with connection.cursor() as cursor:
        for statement in migration.REVERSE:
            cursor.execute(statement)
        assert query(count, [ours]) == [(0,)]
        assert query("SELECT has_function_privilege('worker', 'claim_job()', 'EXECUTE')") == [(True,)]
        for statement in migration.FORWARD:
            cursor.execute(statement)

    assert query(count, [ours]) == [(4,)]
    assert query("SELECT count(*) FROM pg_proc WHERE proname = 'claim_job'") == [(0,)]
    for signature in ["claim_next_job(timestamptz, integer)", "failed_job_count()"]:
        assert query("SELECT has_function_privilege('worker', %s, 'EXECUTE')", [signature]) == [(True,)]


def test_the_fake_gateway_refuses_a_replay_from_another_companys_session(fake_ports):
    gateway = ports.get().gateway
    credentials = IntermediaryCredentials("id", "secret")
    session = gateway.login_as_taxpayer(credentials, 1, "C1234567890")
    signed = SignedDocument(company_id=1, code_number="INV-1", document=b"x", signature=b"y")
    gateway.submit(session, [signed], idempotency_key="k")

    with pytest.raises(WrongCompany):
        gateway.submit(gateway.login_as_taxpayer(credentials, 2, "C9999999999"), [signed], idempotency_key="k")


# --- review follow-ups (PR #109) ---------------------------------------------------------------------------


def test_reversing_the_migration_is_refused_while_a_job_is_running(db, clock):
    migration = import_module("core.migrations.0033_job_execution")
    Job.objects.create(name="x", status=Job.RUNNING, lease_expires_at=clock.now + timedelta(minutes=5))

    with connection.cursor() as cursor, pytest.raises(DatabaseError, match="running"), transaction.atomic():
        for statement in migration.REVERSE:
            cursor.execute(statement)

    assert query("SELECT count(*) FROM pg_proc WHERE proname = 'claim_next_job'") == [(1,)]


@pytest.mark.django_db(transaction=True)
def test_a_failure_after_the_lease_was_lost_is_still_logged_with_its_class_and_frame(registry, clock, caplog):
    raised_on = []
    second_worker = {}

    def second_worker_run():
        try:
            clock.advance(seconds=jobs.LEASE_SECONDS + 1)
            jobs.run_pending()
            clock.advance(seconds=30)
            second_worker["ran"] = jobs.run_pending()
        except Exception as error:  # reported on the main thread
            second_worker["error"] = error
        finally:
            connection.close()

    runs = []

    def handler(**payload):
        runs.append(clock.now)
        if len(runs) > 1:
            return
        thread = threading.Thread(target=second_worker_run)
        thread.start()
        thread.join(timeout=20)
        raised_on.append(inspect.currentframe().f_lineno + 1)
        raise ValueError("cannot parse total in: Supplier ACME Sdn Bhd")

    jobs.register("slow")(handler)
    jobs.enqueue("slow")

    with caplog.at_level("DEBUG"):
        jobs.run_pending()

    assert second_worker == {"ran": 1}
    assert Job.objects.get().status == Job.DONE
    first_attempt = [r.getMessage() for r in caplog.records if "ValueError" in r.getMessage()]
    assert len(first_attempt) == 1 and f"test_job_execution.py:{raised_on[0]}" in first_attempt[0]
    assert "ACME" not in caplog.text


def _load_settings(**env):
    base = {k: v for k, v in os.environ.items() if k not in ("ENVIRONMENT", "SECRET_KEY", "JOB_RETENTION_DAYS")}
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=Path(__file__).resolve().parents[2], env={**base, **env}, capture_output=True, text=True,
    )


@pytest.mark.parametrize("days", ["0", "-1", "soon"])
def test_the_app_will_not_start_with_a_retention_that_would_purge_current_jobs(days):
    result = _load_settings(JOB_RETENTION_DAYS=days)

    assert result.returncode != 0 and "JOB_RETENTION_DAYS" in result.stderr


@pytest.mark.parametrize("days", ["1", "30"])
def test_the_app_starts_with_a_positive_retention(days):
    result = _load_settings(JOB_RETENTION_DAYS=days)

    assert result.returncode == 0, result.stderr
