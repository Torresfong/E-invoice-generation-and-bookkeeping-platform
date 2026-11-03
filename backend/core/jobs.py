"""Postgres-backed job queue (D10): no broker. Tests call run_pending() in-process.

See docs/adr/0001-job-queue-and-tenancy.md. Payloads carry identifiers only, and enqueue() enforces it.
A job carries the company it was queued for, as a column under row-level security, never in the payload.
"""

import json
import logging
import uuid

from django.db import connection, transaction
from django.utils import timezone
from psycopg.pq import TransactionStatus

from .models import Company, Job
from .rls import CLAIMING_SETTING, COMPANY_SETTING, CORRELATION_SETTING, WORKER_ROLE

logger = logging.getLogger(__name__)

HANDLERS = {}

# Longest string accepted in a payload: enough for an id or reference number, too short for content.
MAX_STRING_LENGTH = 40


def register(name):
    def decorator(func):
        HANDLERS[name] = func
        return func

    return decorator


class JobPayloadError(ValueError):
    pass


def _identifier(key, value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, str) and len(value) <= MAX_STRING_LENGTH:
        return value
    # Name the key only: echoing the value would put the content we are refusing into an error.
    raise JobPayloadError(f"Job payload '{key}' must be an identifier (int, UUID, bool, None or a short string)")


def enqueue(name, company=None, **payload):
    """Queue a job. `company` is the company the work is for; leave it out only for a system job.

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
    return Job.objects.create(name=name, payload=clean, company=company)


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


def _stop_being_worker(before):
    """Put back the role the runner found, and clear the company context.

    A savepoint release does not undo SET LOCAL, so it is done explicitly; in an aborted transaction no SQL can
    run, and the rollback that follows undoes it instead. A bare RESET ROLE would go to the session user, which
    is wrong when the runner was called under another role.
    """
    if connection.connection.info.transaction_status == TransactionStatus.INERROR:
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
    """The oldest pending job of any company, locked for this transaction (SKIP LOCKED), or None.

    Claiming goes through the `claim_job()` function so that the worker's own policies can stay narrow: it sees
    every company's pending jobs only for this one call, and no other company's jobs while a handler runs. The
    function answers only while `app.claiming` is on, which is for this call alone.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, 'on', true)", [CLAIMING_SETTING])
        cursor.execute("SELECT id, company_id, name, payload, attempts FROM claim_job()")
        row = cursor.fetchone()
        cursor.execute("SELECT set_config(%s, 'off', true)", [CLAIMING_SETTING])
    if row is None:
        return None
    # A raw cursor returns jsonb as text: Django only decodes it for model fields.
    return Job(id=row[0], company_id=row[1], name=row[2], payload=json.loads(row[3]), attempts=row[4], status=Job.PENDING)


def run_pending(limit=100):
    """Run pending jobs oldest first and return how many were attempted.

    Jobs run as the `worker` role (D36, ADR 0001), which is subject to row-level security. The company
    context for a handler is set from the job's own `company` column, never from the payload, so a handler can
    read only that company's rows (none, for a system job). A failing job is recorded as failed (with its
    error class only) and never blocks the ones behind it. A job's own database writes roll back if it fails.
    Rows are locked with SKIP LOCKED so several workers can run side by side.
    """
    attempted = 0
    while attempted < limit:
        with transaction.atomic():
            before = _become_worker()
            job = _claim()
            if job is None:
                _stop_being_worker(before)
                break
            _set_company(job.company_id)
            _set_correlation(str(uuid.uuid4()))
            try:
                handler = HANDLERS.get(job.name)
                if handler is None:
                    raise LookupError(f"No handler registered for job '{job.name}'")
                with transaction.atomic():
                    handler(**job.payload)
            except Exception as exc:
                # Exception messages often echo tenant content, so only the class is kept or logged.
                job.status = Job.FAILED
                job.error = type(exc).__name__
                logger.warning("Job %s (%s) failed: %s", job.pk, job.name, job.error)
            else:
                job.status = Job.DONE
            job.attempts += 1
            job.finished_at = timezone.now()
            # Only the columns that record the outcome: the worker may not change a job's company or content.
            job.save(update_fields=["status", "error", "attempts", "finished_at"])
            _stop_being_worker(before)
        attempted += 1
    return attempted
