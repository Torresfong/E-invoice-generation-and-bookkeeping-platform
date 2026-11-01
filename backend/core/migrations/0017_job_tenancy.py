from django.db import migrations

from core.rls import (
    CLAIMING_SETTING,
    COMPANY_FUNCTION,
    COMPANY_SETTING,
    IDENTITY_FUNCTION,
    JOB_COMPANY_CHECK,
    USER_COMPANY_CHECK,
    WEB_ROLE,
    WORKER_ROLE,
    definer_function_sql,
    owner_policy_sql,
    secure_role_sql,
)

# The job table is tenant-scoped (D36, ADR 0001), and background work runs as a separate `worker` role.
#
# Web role: reads and queues jobs for the companies the signed-in user belongs to, and nothing else. No update,
# no delete, no job for another company, no job without a company (system jobs are queued by the owner role or
# the worker).
#
# Worker role: neither owner nor superuser, no login (the production login role is a member of it). A job is
# *claimed* through the `claim_job()` definer function, which sees every company's pending jobs and locks one
# with SKIP LOCKED. The worker's own policies cover only the job's company, so while a handler runs it cannot
# see or change another company's jobs. It may update only the columns that record the outcome, never a job's
# company, name or payload, and cannot delete jobs. The company context for a handler comes from the job's own
# column, set by the runner as `app.company_id`; tenant tables opened to the worker (core.rls.tenant_policy_sql,
# `worker=True`) show it only that company's rows, and with no company set it sees none. It reads no account, credential or
# membership table. It may write the audit trail as the system for its company, only through the audit
# trigger, and read its own company's row; it cannot read the trail. Nobody may create temporary tables: a
# temporary trigger function would run at trigger depth and so get past that audit guard.

SECURE_WORKER, VERIFY_WORKER = secure_role_sql(WORKER_ROLE)

# One login role holding both web and worker could switch between them and so reach any company. Membership
# counts through any chain of group roles. A superuser is a member of everything and is not what this guards.
NO_SHARED_LOGIN = f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM pg_roles r WHERE NOT r.rolsuper
        AND pg_has_role(r.oid, '{WEB_ROLE}', 'MEMBER') AND pg_has_role(r.oid, '{WORKER_ROLE}', 'MEMBER')
    ) THEN
        RAISE EXCEPTION 'A role is a member of both {WEB_ROLE} and {WORKER_ROLE}; the web and worker processes need separate login roles';
    END IF;
END
$$
"""

COMPANY_CONTEXT = f"""
CREATE FUNCTION {COMPANY_FUNCTION}() RETURNS bigint LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('{COMPANY_SETTING}', true), '')::bigint $$
"""

# It answers only while the runner is claiming (`app.claiming`), so a handler that calls it by accident or through
# injected SQL gets nothing. A handler can set the flag itself, like `app.company_id`: it is a guard, not a barrier.
CLAIM_JOB = f"""
CREATE FUNCTION claim_job() RETURNS TABLE (id bigint, company_id bigint, name text, payload jsonb, attempts smallint)
LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
    SELECT j.id, j.company_id, j.name, j.payload, j.attempts FROM core_job j
    WHERE j.status = 'pending' AND current_setting('{CLAIMING_SETTING}', true) = 'on'
    ORDER BY j.id LIMIT 1 FOR UPDATE SKIP LOCKED
$$
"""
CLAIM_FORWARD, CLAIM_REVERSE = definer_function_sql({"claim_job()": CLAIM_JOB}, role=WORKER_ROLE)

# The audit trigger function of 0015, with one change: a worker has no signed-in user, so the lookup of the user's
# address is skipped when there is none (the worker cannot read accounts, and the entry is the system's). It is
# restated in full, as a frozen copy, so this migration does not depend on how 0015 is written.
AUDIT_ROW_CHANGE = f"""
CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_row jsonb;
    v_fields jsonb := '[]'::jsonb;
    v_email text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        v_row := to_jsonb(OLD);
    ELSE
        v_row := to_jsonb(NEW);
    END IF;
    IF TG_OP = 'UPDATE' THEN
        SELECT coalesce(jsonb_agg(n.key ORDER BY n.key), '[]'::jsonb) INTO v_fields
        FROM jsonb_each(to_jsonb(NEW)) AS n WHERE n.value IS DISTINCT FROM (to_jsonb(OLD) -> n.key);
        IF v_fields = '[]'::jsonb THEN
            RETURN NULL;
        END IF;
    END IF;
    IF {IDENTITY_FUNCTION}() IS NOT NULL THEN
        SELECT email INTO v_email FROM core_user WHERE id = {IDENTITY_FUNCTION}();
    END IF;
    INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (
        (v_row ->> 'company_id')::bigint,
        {IDENTITY_FUNCTION}(),
        coalesce(v_email, ''),
        CASE TG_OP WHEN 'INSERT' THEN 'create' WHEN 'UPDATE' THEN 'update' ELSE 'delete' END,
        TG_TABLE_NAME,
        v_row ->> 'id',
        v_fields,
        now()
    );
    -- A row moved to another company leaves a trace in the company it left as well.
    IF TG_OP = 'UPDATE' AND (to_jsonb(OLD) ->> 'company_id') IS DISTINCT FROM (v_row ->> 'company_id') THEN
        INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
        VALUES ((to_jsonb(OLD) ->> 'company_id')::bigint, {IDENTITY_FUNCTION}(), coalesce(v_email, ''), 'update',
                TG_TABLE_NAME, v_row ->> 'id', v_fields, now());
    END IF;
    RETURN NULL;
END
$$
"""
# What 0015 created, for the reverse.
AUDIT_ROW_CHANGE_BEFORE = AUDIT_ROW_CHANGE.replace(
    f"    IF {IDENTITY_FUNCTION}() IS NOT NULL THEN\n"
    f"        SELECT email INTO v_email FROM core_user WHERE id = {IDENTITY_FUNCTION}();\n"
    "    END IF;",
    f"    SELECT email INTO v_email FROM core_user WHERE id = {IDENTITY_FUNCTION}();",
)
assert AUDIT_ROW_CHANGE_BEFORE != AUDIT_ROW_CHANGE, "the text of 0015 has drifted: the reverse would not restore it"

# A role that can create temporary tables can reach `pg_temp`, so none may.
NO_TEMP_TABLES = """
DO $$ BEGIN EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database()); END $$
"""
# The reverse always opens it again, even if an operator had closed it before this migration.
ALLOW_TEMP_TABLES = """
DO $$ BEGIN EXECUTE format('GRANT TEMPORARY ON DATABASE %I TO PUBLIC', current_database()); END $$
"""

FORWARD = [
    SECURE_WORKER,
    VERIFY_WORKER,
    NO_SHARED_LOGIN,
    NO_TEMP_TABLES,
    f"GRANT USAGE ON SCHEMA public TO {WORKER_ROLE}",
    COMPANY_CONTEXT,
    # --- web: the signed-in user's companies
    f"GRANT SELECT, INSERT ON core_job TO {WEB_ROLE}",
    "ALTER TABLE core_job ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_job FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY company_jobs ON core_job FOR SELECT TO {WEB_ROLE} USING ({USER_COMPANY_CHECK})",
    f"CREATE POLICY company_queueing ON core_job FOR INSERT TO {WEB_ROLE} WITH CHECK ({USER_COMPANY_CHECK})",
    *owner_policy_sql("core_job"),
    # --- worker: only the job's own company (and system jobs when it has none), outcome columns only
    f"GRANT SELECT, INSERT ON core_job TO {WORKER_ROLE}",
    f"GRANT UPDATE (status, error, attempts, finished_at) ON core_job TO {WORKER_ROLE}",
    f"CREATE POLICY worker_own_jobs ON core_job FOR SELECT TO {WORKER_ROLE} "
    f"USING (company_id IS NOT DISTINCT FROM {COMPANY_FUNCTION}())",
    f"CREATE POLICY worker_outcomes ON core_job FOR UPDATE TO {WORKER_ROLE} "
    f"USING (company_id IS NOT DISTINCT FROM {COMPANY_FUNCTION}()) "
    f"WITH CHECK (company_id IS NOT DISTINCT FROM {COMPANY_FUNCTION}())",
    f"CREATE POLICY worker_follow_ups ON core_job FOR INSERT TO {WORKER_ROLE} "
    f"WITH CHECK (company_id IS NOT DISTINCT FROM {COMPANY_FUNCTION}())",
    *CLAIM_FORWARD,
    # --- worker: its company's own row, and the audit trail as the system, only through the audit trigger
    f"GRANT SELECT ON core_company TO {WORKER_ROLE}",
    f"CREATE POLICY worker_own_company ON core_company FOR SELECT TO {WORKER_ROLE} USING (id = {COMPANY_FUNCTION}())",
    f"GRANT INSERT ON core_auditentry TO {WORKER_ROLE}",
    f"CREATE POLICY worker_entries ON core_auditentry FOR INSERT TO {WORKER_ROLE} "
    f"WITH CHECK ({JOB_COMPANY_CHECK} AND actor_id IS NULL AND actor_email = '' AND pg_trigger_depth() > 0)",
    AUDIT_ROW_CHANGE,
]

REVERSE = [
    ALLOW_TEMP_TABLES,
    AUDIT_ROW_CHANGE_BEFORE,
    "DROP POLICY IF EXISTS worker_entries ON core_auditentry",
    f"REVOKE ALL ON core_auditentry FROM {WORKER_ROLE}",
    "DROP POLICY IF EXISTS worker_own_company ON core_company",
    f"REVOKE ALL ON core_company FROM {WORKER_ROLE}",
    *CLAIM_REVERSE,
    "DROP POLICY IF EXISTS worker_follow_ups ON core_job",
    "DROP POLICY IF EXISTS worker_outcomes ON core_job",
    "DROP POLICY IF EXISTS worker_own_jobs ON core_job",
    f"REVOKE ALL ON core_job FROM {WORKER_ROLE}",
    "DROP POLICY IF EXISTS definer_functions ON core_job",
    "DROP POLICY IF EXISTS company_queueing ON core_job",
    "DROP POLICY IF EXISTS company_jobs ON core_job",
    "ALTER TABLE core_job NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core_job DISABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON core_job FROM {WEB_ROLE}",
    f"DROP FUNCTION IF EXISTS {COMPANY_FUNCTION}()",
    f"REVOKE USAGE ON SCHEMA public FROM {WORKER_ROLE}",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0016_job_company")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
