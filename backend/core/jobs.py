"""Postgres-backed job queue (D10): no broker. Tests call run_pending() in-process.

See docs/adr/0001-job-queue-and-tenancy.md. Payloads carry identifiers only, and enqueue() enforces it.
A job carries the company it was queued for, as a column under row-level security, never in the payload.
"""

import json
import logging
import traceback
import uuid
from contextvars import ContextVar
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from psycopg.pq import TransactionStatus

from .models import Company, Job
from .rls import CLAIMING_SETTING, COMPANY_SETTING, CORRELATION_SETTING, WORKER_ROLE

logger = logging.getLogger(__name__)

HANDLERS = {}

# The job whose handler is running in this thread, if any.
_running = ContextVar("running_job", default=None)

# How long a worker may hold a job before it is presumed crashed or hung and the job goes back to pending.
LEASE_SECONDS = 300

# A failed attempt is retried after BACKOFF_SECONDS * 2^(attempt - 1) seconds, up to MAX_ATTEMPTS attempts in all.
BACKOFF_SECONDS = 30
MAX_ATTEMPTS = 5
# An error the handler did not classify may be a bug that no retry will fix, so it gets fewer attempts.
UNKNOWN_ERROR_MAX_ATTEMPTS = 3

# Time as the job runner sees it. Tests replace it with a clock they move by hand.
clock = timezone.now

# Longest string accepted in a payload: enough for an id or reference number, too short for content.
MAX_STRING_LENGTH = 40


def register(name):
    def decorator(func):
        HANDLERS[name] = func
        return func

    return decorator


class JobPayloadError(ValueError):
    pass


class TransientError(Exception):
    """A handler raises this when a retry may succeed: a timeout, a 5xx, a service that is briefly down."""


class PermanentError(Exception):
    """A handler raises this when no retry can succeed: a refused document, a record that does not exist."""


class NoHandler(PermanentError):
    pass


class SwallowedDatabaseError(PermanentError):
    """A handler caught a database error and returned normally; the runner fails the job instead of finishing it."""


def in_job():
    """True while a job's handler is running in this thread."""
    return _running.get() is not None


def idempotency_key(step=""):
    """The key a handler passes to an external port so that running the job again does not repeat the effect.

    It is the same on every attempt of the job and differs between jobs. A job that makes several calls with an
    external effect names each one with its own `step`, or the second would be taken for a repeat of the first.
    It holds an identifier only (ADR 0001 rule 4).
    """
    job = _running.get()
    if job is None:
        raise RuntimeError("An idempotency key belongs to a running job")
    return f"job-{job.pk}:{step}" if step else f"job-{job.pk}"


def _identifier(key, value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and len(value) <= MAX_STRING_LENGTH:
        return value
    # Name the key only: echoing the value would put the content we are refusing into an error.
    raise JobPayloadError(f"Job payload '{key}' must be an identifier (int, UUID, bool, None or a short string)")


def enqueue(name, company=None, run_after=None, **payload):
    """Queue a job. `company` is the company the work is for; leave it out only for a system job.

    `run_after` is the earliest time the job may be claimed (null: now).

    Queued from a request, the database refuses a company the signed-in user cannot reach. The payload holds
    identifiers only and cannot name the company.
    """
    if company is not None and not isinstance(company, Company):
        raise TypeError("company must be a Company: it is the source of the job's tenant context")
    if "company_id" in payload:
        # The company is the `company` argument and then the column. Other key names are not interpreted: a
        # handler must never treat a payload value as a company.
        raise JobPayloadError("Job payload 'company_id' is reserved: the company is set from the queueing session")
    clean = {key: _identifier(key, value) for key, value in payload.items()}
    return Job.objects.create(name=name, payload=clean, company=company, run_after=run_after)


def _set_company(company_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [COMPANY_SETTING, "" if company_id is None else str(company_id)])


def _set_correlation(value):
    """One id per job for the audit entries it writes (D50 (6)); empty clears it."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [CORRELATION_SETTING, value])


def _become_worker():
    """Switch this transaction to the worker role and return the roles to put back afterwards."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, session_user")
        before = cursor.fetchone()
        cursor.execute(f"SET LOCAL ROLE {WORKER_ROLE}")
    _set_company(None)
    return before


def _transaction_is_aborted():
    return connection.connection.info.transaction_status == TransactionStatus.INERROR


def _stop_being_worker(before):
    """Put back the role the runner found, and clear the company context.

    A savepoint release does not undo SET LOCAL, so it is done explicitly; in an aborted transaction no SQL can
    run, and the rollback that follows undoes it instead. A bare RESET ROLE would go to the session user, which
    is wrong when the runner was called under another role.
    """
    if _transaction_is_aborted():
        transaction.set_rollback(True)
        return
    _set_company(None)
    _set_correlation("")
    current, session = before
    with connection.cursor() as cursor:
        if current == session:
            cursor.execute("RESET ROLE")
        else:
            cursor.execute(f"SET LOCAL ROLE {connection.ops.quote_name(current)}")


def _claim():
    """Mark the oldest due pending job of any company running, under a lease, and return it, or None.

    This is a short transaction of its own: the handler runs later, in another one, so no row lock is held while
    it calls a slow service. Claiming goes through the `claim_next_job()` function so that the worker's own
    policies can stay narrow: it sees every company's pending jobs only for this one call, and no other
    company's jobs while a handler runs. The function answers only while `app.claiming` is on, which is for this
    call alone. The returned `attempts` counts this claim and is the job's fence: only the claim that holds the
    current count may record an outcome.
    """
    with transaction.atomic():
        before = _become_worker()
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config(%s, 'on', true)", [CLAIMING_SETTING])
            cursor.execute(
                "SELECT id, company_id, name, payload, attempts FROM claim_next_job(%s, %s)", [clock(), LEASE_SECONDS]
            )
            row = cursor.fetchone()
            cursor.execute("SELECT set_config(%s, 'off', true)", [CLAIMING_SETTING])
        _stop_being_worker(before)
    if row is None:
        return None
    # A raw cursor returns jsonb as text: Django only decodes it for model fields.
    return Job(id=row[0], company_id=row[1], name=row[2], payload=json.loads(row[3]), attempts=row[4], status=Job.RUNNING)


def _recover_expired():
    """Return the jobs of crashed or hung workers (lease expired) to pending, or to failed once out of attempts."""
    with transaction.atomic():
        before = _become_worker()
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config(%s, 'on', true)", [CLAIMING_SETTING])
            cursor.execute(
                "SELECT id, name, status FROM release_expired_jobs(%s, %s, %s)", [clock(), BACKOFF_SECONDS, MAX_ATTEMPTS]
            )
            released = cursor.fetchall()
            cursor.execute("SELECT set_config(%s, 'off', true)", [CLAIMING_SETTING])
        _stop_being_worker(before)
    for pk, name, status in released:
        logger.error("Job %s (%s) lease expired; the job is now %s", pk, name, status)


def _after_failure(job, exc):
    """The fields that record a failed attempt: retry after a backoff, or fail the job for good."""
    if isinstance(exc, PermanentError):
        cap = 1
    elif isinstance(exc, TransientError):
        cap = MAX_ATTEMPTS
    else:
        cap = UNKNOWN_ERROR_MAX_ATTEMPTS
    # Exception messages often echo tenant content, so only the class is kept or logged.
    outcome = {"error": type(exc).__name__}
    if job.attempts >= cap:
        return {**outcome, "status": Job.FAILED, "finished_at": clock()}
    delay = timedelta(seconds=BACKOFF_SECONDS * 2 ** (job.attempts - 1))
    return {**outcome, "status": Job.PENDING, "run_after": clock() + delay}


def _failing_frame(exc):
    """`file:line` of the innermost frame the exception passed through: where it was raised."""
    frames = traceback.extract_tb(exc.__traceback__)
    return f"{frames[-1].filename}:{frames[-1].lineno}" if frames else "unknown"


class LeaseLost(Exception):
    """The job no longer belongs to this claim: its lease expired and it was returned to pending or claimed again."""


def _record(job, **fields):
    """Write the outcome of this claim. Only the claim that still holds the job may do it (see LeaseLost)."""
    holds = Job.objects.filter(pk=job.pk, status=Job.RUNNING, attempts=job.attempts)
    if not holds.update(lease_expires_at=None, **fields):
        raise LeaseLost


def _execute(job):
    """Run the handler of a claimed job as the worker, in the job's company context, and record the outcome.

    The handler's database writes and the outcome are one transaction: they commit together or not at all. A
    handler that fails has its writes rolled back and the failure recorded. A handler that raises BaseException
    (the process is going down) records nothing, and the lease expiry returns the job to pending.
    """
    try:
        with transaction.atomic():
            before = _become_worker()
            _set_company(job.company_id)
            _set_correlation(str(uuid.uuid4()))
            try:
                handler = HANDLERS.get(job.name)
                if handler is None:
                    raise NoHandler(f"No handler registered for job '{job.name}'")
                token = _running.set(job)
                try:
                    with transaction.atomic():
                        handler(**job.payload)
                        if connection.needs_rollback or _transaction_is_aborted():
                            # The handler caught a database error and went on. Its writes since the error are
                            # gone and the rest may be half done, so this is a failure, not a success.
                            raise SwallowedDatabaseError
                finally:
                    _running.reset(token)
            except Exception as exc:
                outcome = _after_failure(job, exc)
                # Logged before the outcome is recorded: if the lease was lost, recording raises LeaseLost and this
                # would otherwise be the only trace of what went wrong. No message and no payload, either may hold
                # tenant content; the class and the frame that raised are enough to find the cause (ADR 0001 rule 5).
                logger.error(
                    "Job %s (%s) failed with %s at %s (attempt %s, outcome %s)",
                    job.pk, job.name, outcome["error"], _failing_frame(exc), job.attempts, outcome["status"],
                )
                _record(job, **outcome)
            else:
                _record(job, status=Job.DONE, error="", finished_at=clock())
            _stop_being_worker(before)
    except LeaseLost:
        logger.error("Job %s (%s) lost its lease and its outcome was discarded", job.pk, job.name)


def failed_count():
    """How many jobs are failed, across all companies, for an alert (ADR 0001: the stored error is deliberately thin)."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT failed_job_count()")
        return cursor.fetchone()[0]


def purge_finished():
    """Delete the done and failed jobs that finished more than JOB_RETENTION_DAYS ago; return how many.

    It deletes nothing from a job that runs for a company: only a system job, such as the purge job, may purge.
    """
    before = clock() - timedelta(days=settings.JOB_RETENTION_DAYS)
    with connection.cursor() as cursor:
        cursor.execute("SELECT purge_finished_jobs(%s)", [before])
        return cursor.fetchone()[0]


# The purge runs once a day. It queues its own next run, so one run to start it keeps it going (schedule_purge).
PURGE_JOB = "purge_finished_jobs"
PURGE_EVERY = timedelta(days=1)


@register(PURGE_JOB)
def _purge_finished_jobs():
    enqueue(PURGE_JOB, run_after=clock() + PURGE_EVERY)
    purge_finished()


def schedule_purge():
    """Queue the purge unless one is already waiting or running. Safe to call on every deploy."""
    if not Job.objects.filter(name=PURGE_JOB, status__in=[Job.PENDING, Job.RUNNING]).exists():
        enqueue(PURGE_JOB)


def run_pending(limit=100):
    """Run due pending jobs oldest first and return how many were attempted.

    Jobs run as the `worker` role (D36, ADR 0001), which is subject to row-level security. The company
    context for a handler is set from the job's own `company` column, never from the payload, so a handler can
    read only that company's rows (none, for a system job). A failing job is recorded as failed (with its
    error class only) and never blocks the ones behind it. A job's own database writes roll back if it fails.
    A job is claimed under a lease and the handler runs outside the row lock, so several workers can run side
    by side and a slow external call holds no lock.
    """
    _recover_expired()
    attempted = 0
    while attempted < limit:
        job = _claim()
        if job is None:
            break
        _execute(job)
        attempted += 1
    return attempted
