"""Row-level security for tenant tables (D11, D40; see docs/architecture/tenancy.md).

Every business table carries a company id. A row is visible and writable only when that company is among
the signed-in user's `CompanyAccess` rows. The signed-in user reaches the database as a transaction-local
setting (`app.user_id`), and requests run as the `web` role, which is neither table owner nor superuser.

Background jobs run as the `worker` role (D36, ADR 0001), also neither owner nor superuser. The worker has no
user: before a handler runs, the runner sets `app.company_id` from the job's own company column, and a tenant
table opened to the worker shows it only the rows of that company. With no company set (a system job) it shows none.

To add a tenant table: subclass `core.models.TenantModel`, then in the migration that creates it add
`enable_tenant_rls("app_table")` after the CreateModel. Add `worker=True` only if job handlers need the table.
"""

from django.db import migrations

WEB_ROLE = "web"
WORKER_ROLE = "worker"
IDENTITY_SETTING = "app.user_id"
IDENTITY_FUNCTION = "app_user_id"
COMPANY_SETTING = "app.company_id"
COMPANY_FUNCTION = "app_company_id"
# Set by the runner for the one call that claims a job; `claim_job()` returns nothing without it.
CLAIMING_SETTING = "app.claiming"

# The two row rules behind every tenant policy. A user sees the rows of the companies they belong to; a job's
# worker sees the rows of the company the job is for.
USER_COMPANY_CHECK = (
    f"company_id IN (SELECT company_id FROM core_companyaccess WHERE user_id = {IDENTITY_FUNCTION}())"
)
JOB_COMPANY_CHECK = f"company_id = {COMPANY_FUNCTION}()"


def tenant_policy_sql(table, worker=False):
    """SQL that puts `table` under the standard isolation policy.

    Job handlers reach the table only with `worker=True`, which is a decision for each table: a table that holds
    secrets (credentials, signing metadata, 2FA) must stay closed to every handler.
    """
    roles = f"{WEB_ROLE}, {WORKER_ROLE}" if worker else WEB_ROLE
    statements = [
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {roles}",
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} TO {WEB_ROLE} "
        f"USING ({USER_COMPANY_CHECK}) WITH CHECK ({USER_COMPANY_CHECK})",
    ]
    if worker:
        statements.append(
            f"CREATE POLICY worker_company_context ON {table} TO {WORKER_ROLE} "
            f"USING ({JOB_COMPANY_CHECK}) WITH CHECK ({JOB_COMPANY_CHECK})"
        )
    return statements


def drop_tenant_policy_sql(table):
    return [
        f"DROP POLICY IF EXISTS worker_company_context ON {table}",
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
        f"REVOKE ALL ON {table} FROM {WEB_ROLE}, {WORKER_ROLE}",
    ]


def enable_tenant_rls(table, worker=False):
    """A migration operation that applies the standard isolation policy to `table`; `worker=True` opens it to jobs."""
    return migrations.RunSQL(tenant_policy_sql(table, worker), drop_tenant_policy_sql(table))


def owner_policy_sql(table):
    """SQL that lets the role running the migration (the table owner) through `FORCE ROW LEVEL SECURITY`.

    The SECURITY DEFINER functions of D40 (2) run as that role, and forced row-level security would
    otherwise hide every row from them. The policy names the owner explicitly, so it opens nothing to the
    web role or to a login role that is a member of it.
    """
    return [
        "DO $$ BEGIN EXECUTE format("
        f"'CREATE POLICY definer_functions ON {table} TO %I USING (true) WITH CHECK (true)', current_user); "
        "END $$"
    ]


def definer_function_sql(functions, role=WEB_ROLE):
    """Forward and reverse SQL for SECURITY DEFINER functions: create each, close it to everyone, open it to `role`.

    `functions` maps a signature such as "auth_lookup(text)" to its CREATE FUNCTION statement.
    """
    forward, reverse = [], []
    for signature, create in functions.items():
        forward += [
            create,
            f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC",
            f"GRANT EXECUTE ON FUNCTION {signature} TO {role}",
        ]
        reverse.insert(0, f"DROP FUNCTION IF EXISTS {signature}")
    return forward, reverse


def secure_role_sql(role):
    """SQL that creates `role` as a login-less group role that cannot bypass row-level security, or forces that
    onto an existing role, plus SQL that refuses to continue unless it holds and the role is a member of nothing.

    Changing the SUPERUSER attribute at all needs a superuser, so the role is altered only when it is unsafe.
    Migration 0003 predates this helper and holds the same logic for `web`; a change to one needs the other.
    """
    unsafe = "rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolcanlogin"
    secure = f"""
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}') THEN
        CREATE ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    ELSIF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}' AND ({unsafe})) THEN
        ALTER ROLE {role} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$
"""
    verify = f"""
DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = '{role}' AND ({unsafe})) THEN
        RAISE EXCEPTION 'Database role {role} has unsafe attributes; it must be NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB';
    END IF;
    IF EXISTS (
        SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member WHERE r.rolname = '{role}'
    ) THEN
        RAISE EXCEPTION 'Database role {role} is a member of other roles; it must not inherit their privileges';
    END IF;
END
$$
"""
    return secure, verify
