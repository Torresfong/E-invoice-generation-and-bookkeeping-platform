from importlib import import_module

from django.db import migrations, models

from core.rls import CLAIMING_SETTING, COMPANY_FUNCTION, WORKER_ROLE, definer_function_sql

# Job execution (ticket #27, ADR 0001, D36): a job is claimed by marking it running under a lease, so the handler
# runs outside any row lock; a crashed or hung worker's job is returned to pending when its lease expires; a failed
# attempt is retried after a backoff (`run_after`) until the cap.
#
# The worker may now also write `run_after` and `lease_expires_at` on its own company's job (the outcome columns
# of 0017 still hold: never a job's company, name or payload). Claiming moves from `claim_job()` to
# `claim_next_job(now, lease)`, which also marks the job running. The functions below answer only while the runner
# is claiming (`app.claiming`), as `claim_job()` did.

CLAIM_NEXT_JOB = f"""
CREATE FUNCTION claim_next_job(p_now timestamptz, p_lease_seconds integer)
RETURNS TABLE (id bigint, company_id bigint, name text, payload jsonb, attempts smallint)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
    UPDATE core_job j
    SET status = 'running', attempts = j.attempts + 1, run_after = NULL,
        lease_expires_at = p_now + make_interval(secs => p_lease_seconds)
    WHERE j.id = (
        SELECT c.id FROM core_job c
        WHERE c.status = 'pending' AND (c.run_after IS NULL OR c.run_after <= p_now)
          AND current_setting('{CLAIMING_SETTING}', true) = 'on'
        ORDER BY c.id LIMIT 1 FOR UPDATE SKIP LOCKED
    )
    RETURNING j.id, j.company_id, j.name, j.payload, j.attempts
$$
"""

# A running job whose lease has expired belongs to a crashed or hung worker. It goes back to pending, with the lost
# attempt counted and a backoff of base * 2^(attempts - 1) seconds before the retry, or to failed once the cap is
# reached. Only the claim that holds the job may record an outcome (its `attempts` is the fence), so a worker that
# was only slow cannot overwrite what happens next.
RELEASE_EXPIRED_JOBS = f"""
CREATE FUNCTION release_expired_jobs(p_now timestamptz, p_base_seconds integer, p_max_attempts integer)
RETURNS TABLE (id bigint, name text, status text)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
    UPDATE core_job j
    SET status = CASE WHEN j.attempts >= p_max_attempts THEN 'failed' ELSE 'pending' END,
        error = 'LeaseExpired',
        finished_at = CASE WHEN j.attempts >= p_max_attempts THEN p_now END,
        run_after = CASE WHEN j.attempts >= p_max_attempts THEN NULL
                         ELSE p_now + make_interval(secs => p_base_seconds * power(2, j.attempts - 1)) END,
        lease_expires_at = NULL
    WHERE j.status = 'running' AND j.lease_expires_at < p_now
      AND current_setting('{CLAIMING_SETTING}', true) = 'on'
    RETURNING j.id, j.name, j.status
$$
"""

# For the alert on failed jobs: a count across companies and nothing else about them.
FAILED_JOB_COUNT = """
CREATE FUNCTION failed_job_count() RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$ SELECT count(*) FROM core_job WHERE status = 'failed' $$
"""

# Purging is the one place the worker deletes jobs, and only finished ones. It does nothing while a company context
# is set, so the purge (a system job) can run it and a company's job cannot, by accident. The context is a session
# setting a hostile handler could clear: this is a guard, not a barrier (docs/architecture/tenancy.md, "Limits").
PURGE_FINISHED_JOBS = f"""
CREATE FUNCTION purge_finished_jobs(p_before timestamptz) RETURNS bigint
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
    WITH gone AS (
        DELETE FROM core_job
        WHERE status IN ('done', 'failed') AND finished_at < p_before AND {COMPANY_FUNCTION}() IS NULL
        RETURNING 1
    )
    SELECT count(*) FROM gone
$$
"""

FUNCTIONS = {
    "claim_next_job(timestamptz, integer)": CLAIM_NEXT_JOB,
    "release_expired_jobs(timestamptz, integer, integer)": RELEASE_EXPIRED_JOBS,
    "failed_job_count()": FAILED_JOB_COUNT,
    "purge_finished_jobs(timestamptz)": PURGE_FINISHED_JOBS,
}
FUNCTIONS_FORWARD, FUNCTIONS_REVERSE = definer_function_sql(FUNCTIONS, role=WORKER_ROLE)

FORWARD = [
    f"GRANT UPDATE (run_after, lease_expires_at) ON core_job TO {WORKER_ROLE}",
    "DROP FUNCTION IF EXISTS claim_job()",
    *FUNCTIONS_FORWARD,
]


# The old runner claims only pending jobs and knows nothing of leases, so a job left running would never be taken
# again. Reversing is refused until the workers are stopped and their running jobs have finished or been swept.
NO_RUNNING_JOBS = """
DO $$
BEGIN
    IF EXISTS (SELECT FROM core_job WHERE status = 'running') THEN
        RAISE EXCEPTION 'Jobs are running; stop the workers and let them finish or expire before reversing the job execution migration';
    END IF;
END
$$
"""


def reverse():
    # What 0017 created, put back by its own statements.
    before = import_module("core.migrations.0017_job_tenancy")
    return [
        NO_RUNNING_JOBS,
        *FUNCTIONS_REVERSE,
        *before.CLAIM_FORWARD,
        f"REVOKE UPDATE (run_after, lease_expires_at) ON core_job FROM {WORKER_ROLE}",
    ]


REVERSE = reverse()


class Migration(migrations.Migration):
    dependencies = [("core", "0032_sign_in_audit_proof")]
    operations = [
        migrations.AddField(model_name="job", name="lease_expires_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="job", name="run_after", field=models.DateTimeField(blank=True, null=True)),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
