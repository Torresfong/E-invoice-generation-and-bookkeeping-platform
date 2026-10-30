from importlib import import_module

from django.db import migrations

from core.rls import (
    COMPANY_FUNCTION,
    COMPANY_SETTING,
    IDENTITY_FUNCTION,
    WEB_ROLE,
    WORKER_ROLE,
    owner_policy_sql,
    secure_role_sql,
)

# The job table is tenant-scoped (D36, ADR 0001), and background work runs as a separate `worker` role.
#
# Web role: reads and queues jobs for the companies the signed-in user belongs to, and nothing else. No update,
# no delete, no job for another company, no job without a company (system jobs are queued by the owner role or
# the worker).
#
# Worker role: neither owner nor superuser, no login (the production login role is a member of it). It claims
# jobs of every company and records the outcome, but may change only the columns that record it, never a job's
# company, name or payload. The company context for a handler comes from the job's own column, set by the runner
# as `app.company_id`; tenant tables show the worker only that company's rows (core.rls.tenant_policy_sql), and
# with no company set it sees none. It reads no account, credential or membership table. It may write the audit
# trail as the system for the job's company, and read its own company's row; it cannot read the trail.

ACCESSIBLE = f"company_id IN (SELECT company_id FROM core_companyaccess WHERE user_id = {IDENTITY_FUNCTION}())"
IN_JOB = f"company_id = {COMPANY_FUNCTION}()"

SECURE_WORKER, VERIFY_WORKER = secure_role_sql(WORKER_ROLE)

COMPANY_CONTEXT = f"""
CREATE FUNCTION {COMPANY_FUNCTION}() RETURNS bigint LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('{COMPANY_SETTING}', true), '')::bigint $$
"""

# A worker has no signed-in user, so the audit trigger must not look one up: the worker cannot read accounts, and
# the entry is the system's (no actor).
_audit_0015 = import_module("core.migrations.0015_audit_trigger")
_LOOKUP = f"    SELECT email INTO v_email FROM core_user WHERE id = {IDENTITY_FUNCTION}();"
assert _LOOKUP in _audit_0015.AUDIT_ROW_CHANGE
AUDIT_ROW_CHANGE_FOR_WORKER = _audit_0015.AUDIT_ROW_CHANGE.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION").replace(
    _LOOKUP,
    f"    IF {IDENTITY_FUNCTION}() IS NOT NULL THEN\n"
    f"        SELECT email INTO v_email FROM core_user WHERE id = {IDENTITY_FUNCTION}();\n"
    "    END IF;",
)
AUDIT_ROW_CHANGE_AS_BEFORE = _audit_0015.AUDIT_ROW_CHANGE.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")

FORWARD = [
    SECURE_WORKER,
    VERIFY_WORKER,
    f"GRANT USAGE ON SCHEMA public TO {WORKER_ROLE}",
    COMPANY_CONTEXT,
    # --- web: the signed-in user's companies
    f"GRANT SELECT, INSERT ON core_job TO {WEB_ROLE}",
    "ALTER TABLE core_job ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_job FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY company_jobs ON core_job FOR SELECT TO {WEB_ROLE} USING ({ACCESSIBLE})",
    f"CREATE POLICY company_queueing ON core_job FOR INSERT TO {WEB_ROLE} WITH CHECK ({ACCESSIBLE})",
    *owner_policy_sql("core_job"),
    # --- worker: claim any job, record the outcome, queue follow-ups for the job's own company only
    f"GRANT SELECT, INSERT ON core_job TO {WORKER_ROLE}",
    f"GRANT UPDATE (status, error, attempts, finished_at) ON core_job TO {WORKER_ROLE}",
    f"CREATE POLICY worker_claims ON core_job FOR SELECT TO {WORKER_ROLE} USING (true)",
    f"CREATE POLICY worker_outcomes ON core_job FOR UPDATE TO {WORKER_ROLE} USING (true) WITH CHECK (true)",
    f"CREATE POLICY worker_follow_ups ON core_job FOR INSERT TO {WORKER_ROLE} "
    f"WITH CHECK (company_id IS NOT DISTINCT FROM {COMPANY_FUNCTION}())",
    # --- worker: its company's own row, and the audit trail as the system
    f"GRANT SELECT ON core_company TO {WORKER_ROLE}",
    f"CREATE POLICY worker_company ON core_company FOR SELECT TO {WORKER_ROLE} USING (id = {COMPANY_FUNCTION}())",
    f"GRANT INSERT ON core_auditentry TO {WORKER_ROLE}",
    f"CREATE POLICY worker_entries ON core_auditentry FOR INSERT TO {WORKER_ROLE} "
    f"WITH CHECK ({IN_JOB} AND actor_id IS NULL AND actor_email = '')",
    AUDIT_ROW_CHANGE_FOR_WORKER,
]

REVERSE = [
    AUDIT_ROW_CHANGE_AS_BEFORE,
    "DROP POLICY IF EXISTS worker_entries ON core_auditentry",
    f"REVOKE ALL ON core_auditentry FROM {WORKER_ROLE}",
    "DROP POLICY IF EXISTS worker_company ON core_company",
    f"REVOKE ALL ON core_company FROM {WORKER_ROLE}",
    "DROP POLICY IF EXISTS worker_follow_ups ON core_job",
    "DROP POLICY IF EXISTS worker_outcomes ON core_job",
    "DROP POLICY IF EXISTS worker_claims ON core_job",
    f"REVOKE ALL ON core_job FROM {WORKER_ROLE}",
    "DROP POLICY IF EXISTS definer_functions ON core_job",
    "DROP POLICY IF EXISTS company_queueing ON core_job",
    "DROP POLICY IF EXISTS company_jobs ON core_job",
    "ALTER TABLE core_job DISABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON core_job FROM {WEB_ROLE}",
    f"DROP FUNCTION IF EXISTS {COMPANY_FUNCTION}()",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0016_job_company")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
