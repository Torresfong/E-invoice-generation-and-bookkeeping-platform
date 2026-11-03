from importlib import import_module

from django.db import migrations

from core.rls import definer_function_sql

# The firm administrator (D49) manages the accountants of their own firm from the web. The web role cannot write
# users or access rows, so each action is a SECURITY DEFINER function (D40 (2)) that checks, inside the database,
# that the caller is an active firm administrator and acts only on the caller's own firm. A function never takes
# the firm from its arguments. Each change writes its firm-trail entry (D50) in the same transaction, so it cannot
# be forgotten or forged.

# The caller's firm if they are an active firm administrator, else an error.
CALLER_FIRM = """
    SELECT firm_id INTO v_firm FROM core_user WHERE id = app_user_id() AND is_firm_admin AND is_active;
    IF v_firm IS NULL THEN
        RAISE EXCEPTION 'only a firm administrator may do this' USING ERRCODE = '42501';
    END IF;
    -- One lock order everywhere (services.py): the firm first, so changes to its people take turns.
    PERFORM 1 FROM core_firm WHERE id = v_firm FOR UPDATE;
"""

# Mirrors services.create_accountant / create_firm_administrator, which run as the table owner and so cannot be
# used from the web. Returns the new user's id, or NULL when the address already has an account (the caller is
# not told which addresses are registered).
CREATE_FIRM_PERSON = f"""
CREATE FUNCTION create_firm_person(p_email text, p_kind text) RETURNS bigint
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
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_user;
    IF v_user IS NULL THEN
        RETURN NULL;
    END IF;
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
        "create_firm_person(text, text)": CREATE_FIRM_PERSON,
        "deactivate_firm_person(bigint)": DEACTIVATE_FIRM_PERSON,
        "firm_people()": FIRM_PEOPLE,
    }
)

# Administrators sign in to the firm trail like accountants (D50 (3)). Only the line that picks the firm changes.
ACCOUNTANT_ONLY = "CASE WHEN is_accountant THEN firm_id END"
FIRM_ROLE = "CASE WHEN is_accountant OR is_firm_admin THEN firm_id END"


def record_auth_event(replace, by):
    old = import_module("core.migrations.0023_firm_audit_entry").RECORD_AUTH_EVENT
    assert old.count(replace) == 2
    return old.replace(replace, by)


class Migration(migrations.Migration):
    dependencies = [("core", "0025_firm_administrator")]
    operations = [
        migrations.RunSQL(FUNCTION_FORWARD, FUNCTION_REVERSE),
        migrations.RunSQL(record_auth_event(ACCOUNTANT_ONLY, FIRM_ROLE), record_auth_event(ACCOUNTANT_ONLY, ACCOUNTANT_ONLY)),
    ]
