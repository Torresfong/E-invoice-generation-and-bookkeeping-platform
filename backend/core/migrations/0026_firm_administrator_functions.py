from importlib import import_module

from django.db import migrations

from core.rls import definer_function_sql

# The firm administrator (D49) manages the accountants of their own firm from the web. The web role cannot write
# users or access rows, so each action is a SECURITY DEFINER function (D40 (2)) that checks, inside the database,
# that the caller is an active firm administrator and acts only on the caller's own firm. A function never takes
# the firm from its arguments. Each change writes its firm-trail entry (D50) in the same transaction, so it cannot
# be forgotten or forged.

# The caller's firm if they are an active firm administrator, else an error. The firm is locked first (one lock order
# everywhere, services.py) so changes to its people take turns, and the caller is checked again once the lock is held:
# an administrator deactivated while waiting for it must not go on to act.
CALLER_FIRM = """
    SELECT firm_id INTO v_firm FROM core_user WHERE id = app_user_id() AND is_firm_admin AND is_active;
    IF v_firm IS NULL THEN
        RAISE EXCEPTION 'only a firm administrator may do this' USING ERRCODE = '42501';
    END IF;
    PERFORM 1 FROM core_firm WHERE id = v_firm FOR UPDATE;
    IF NOT EXISTS (SELECT 1 FROM core_user WHERE id = app_user_id() AND is_firm_admin AND is_active AND firm_id = v_firm) THEN
        RAISE EXCEPTION 'only a firm administrator may do this' USING ERRCODE = '42501';
    END IF;
"""

# Mirrors services.create_accountant / create_firm_administrator, which run as the table owner and so cannot be
# used from the web. It also stores the hash of the person's password-setup link (an ordinary reset link, used at
# /reset/<token>/), capped at 24 hours. Returns the new user's id, or NULL when the address already has an account
# (the caller is not told which addresses are registered), in which case no link is stored.
CREATE_FIRM_PERSON = f"""
CREATE FUNCTION create_firm_person(p_email text, p_kind text, p_token_hash text, p_expires timestamptz) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_firm bigint;
    v_user bigint;
    v_caller_email text;
BEGIN
    IF p_kind NOT IN ('accountant', 'firm_admin') THEN
        RAISE EXCEPTION 'not a firm role' USING ERRCODE = '22023';
    END IF;
{CALLER_FIRM}
    -- '!' is Django's marker for a password nobody can use; the person chooses one from the emailed link.
    INSERT INTO core_user (password, last_login, is_superuser, email, is_active, date_joined, is_accountant, is_firm_admin, firm_id)
    VALUES ('!', NULL, false, lower(p_email), true, now(), p_kind = 'accountant', p_kind = 'firm_admin', v_firm)
    ON CONFLICT (lower(email)) DO NOTHING
    RETURNING id INTO v_user;
    IF v_user IS NULL THEN
        RETURN NULL;
    END IF;
    INSERT INTO core_authtoken (purpose, token_hash, email, role, user_id, expires_at, created_at)
    VALUES ('reset', p_token_hash, lower(p_email), '', v_user, LEAST(p_expires, now() + interval '24 hours'), now());
    IF p_kind = 'accountant' THEN
        INSERT INTO core_companyaccess (user_id, company_id, role, created_at)
        SELECT v_user, c.id, 'accountant', now() FROM core_company c WHERE c.firm_id = v_firm;
    END IF;
    SELECT email INTO v_caller_email FROM core_user WHERE id = app_user_id();
    INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_firm, app_user_id(), v_caller_email, 'create', 'user', v_user::text, '["email", "firm"]'::jsonb, now());
    RETURN v_user;
END
$$
"""

# A person that is not an accountant or administrator of the caller's firm is "not found", whichever firm they are
# in, so the caller learns nothing about other firms or about clients.
DEACTIVATE_FIRM_PERSON = f"""
CREATE FUNCTION deactivate_firm_person(p_user bigint) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_firm bigint;
    v_target core_user%ROWTYPE;
    v_caller_email text;
BEGIN
{CALLER_FIRM}
    SELECT * INTO v_target FROM core_user
    WHERE id = p_user AND firm_id = v_firm AND (is_accountant OR is_firm_admin) AND is_active FOR UPDATE;
    IF v_target.id IS NULL THEN
        RAISE EXCEPTION 'no such person' USING ERRCODE = 'P0002';
    END IF;
    IF v_target.is_firm_admin AND NOT EXISTS (
        SELECT 1 FROM core_user WHERE firm_id = v_firm AND is_firm_admin AND is_active AND id <> p_user
    ) THEN
        RAISE EXCEPTION 'the last active administrator of a firm cannot be deactivated' USING ERRCODE = '55000';
    END IF;
    UPDATE core_user SET is_active = false WHERE id = p_user;
    SELECT email INTO v_caller_email FROM core_user WHERE id = app_user_id();
    INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_firm, app_user_id(), v_caller_email, 'update', 'user', p_user::text, '["is_active"]'::jsonb, now());
END
$$
"""

# The caller's firm's accountants and administrators. Never the password hash, and never a client.
FIRM_PEOPLE = f"""
CREATE FUNCTION firm_people() RETURNS TABLE (person_id bigint, person_email text, person_is_admin boolean, person_is_active boolean)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_firm bigint;
BEGIN
    SELECT firm_id INTO v_firm FROM core_user WHERE id = app_user_id() AND is_firm_admin AND is_active;
    IF v_firm IS NULL THEN
        RAISE EXCEPTION 'only a firm administrator may do this' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT u.id, u.email::text, u.is_firm_admin, u.is_active FROM core_user u
        WHERE u.firm_id = v_firm AND (u.is_accountant OR u.is_firm_admin) ORDER BY u.is_firm_admin DESC, u.email;
END
$$
"""

FUNCTION_FORWARD, FUNCTION_REVERSE = definer_function_sql(
    {
        "create_firm_person(text, text, text, timestamptz)": CREATE_FIRM_PERSON,
        "deactivate_firm_person(bigint)": DEACTIVATE_FIRM_PERSON,
        "firm_people()": FIRM_PEOPLE,
    }
)

# Administrators sign in to the firm trail like accountants (D50 (3)). This is 0023's function with one change: the
# firm is also taken for a firm administrator.
RECORD_AUTH_EVENT = """
CREATE OR REPLACE FUNCTION record_auth_event(p_action text, p_email text DEFAULT NULL) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_user bigint;
    v_email text;
    v_firm bigint;
    v_caller bigint := app_user_id();
BEGIN
    IF p_action NOT IN ('login', 'login_failed', 'logout') THEN
        RAISE EXCEPTION 'not a sign-in event' USING ERRCODE = '22023';
    END IF;
    IF p_email IS NULL THEN
        SELECT id, email, CASE WHEN is_accountant OR is_firm_admin THEN firm_id END INTO v_user, v_email, v_firm
        FROM core_user WHERE id = app_user_id();
    ELSE
        SELECT id, email, CASE WHEN is_accountant OR is_firm_admin THEN firm_id END INTO v_user, v_email, v_firm
        FROM core_user WHERE lower(email) = lower(p_email);
        IF v_caller IS NOT NULL AND v_user IS DISTINCT FROM v_caller THEN
            RAISE EXCEPTION 'a signed-in user can record only their own sign-in events' USING ERRCODE = '42501';
        END IF;
    END IF;
    IF v_user IS NULL THEN
        RETURN;
    END IF;
    IF v_firm IS NOT NULL THEN
        INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
        VALUES (v_firm, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now());
        RETURN;
    END IF;
    INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    SELECT a.company_id, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now()
    FROM core_companyaccess a WHERE a.user_id = v_user;
END
$$
"""


def previous_record_auth_event():
    return import_module("core.migrations.0023_firm_audit_entry").RECORD_AUTH_EVENT


class Migration(migrations.Migration):
    dependencies = [("core", "0025_firm_administrator")]
    operations = [
        migrations.RunSQL(FUNCTION_FORWARD, FUNCTION_REVERSE),
        migrations.RunSQL(RECORD_AUTH_EVENT, previous_record_auth_event()),
    ]
