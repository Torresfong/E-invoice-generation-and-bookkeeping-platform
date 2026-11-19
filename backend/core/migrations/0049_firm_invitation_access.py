from django.db import migrations

from core.audit import audit_trigger_sql, drop_audit_trigger_sql
from core.rls import USER_COMPANY_CHECK, WEB_ROLE, definer_function_sql, owner_policy_sql

# Firm invitations (D67 (3), D40 (5) for the invitation link style, ticket #144).
#
# The web role may only SELECT its own company's rows, so the owner can see where an invitation stands; it has no
# INSERT, UPDATE or DELETE grant at all. Sending and accepting both run through SECURITY DEFINER functions, like
# `create_invitation`/`accept_invitation` (migration 0010): sending checks, inside the database, that the caller
# owns the company and that it has no firm yet; accepting is done by a firm administrator who has no
# `CompanyAccess` row for this company, so only a function running as the table owner can read the token, lock the
# row and change `core_company.firm_id`. The one-pending-per-company uniqueness is migration 0048's constraint;
# the function clears a stale pending row to "expired" first, so a new invitation can be sent once the old one's
# time is up (it does not rest on a background job).

CREATE_FIRM_INVITATION = """
CREATE FUNCTION create_firm_invitation(p_company bigint, p_email text, p_token_hash text, p_expires timestamptz)
RETURNS bigint LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_firm bigint;
    v_id bigint;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM core_companyaccess
        WHERE user_id = app_user_id() AND company_id = p_company AND role = 'owner'
    ) THEN
        RAISE EXCEPTION 'only the owner of the company may invite a firm' USING ERRCODE = '42501';
    END IF;
    SELECT firm_id INTO v_firm FROM core_company WHERE id = p_company FOR UPDATE;
    IF v_firm IS NOT NULL THEN
        RAISE EXCEPTION 'the company already has a firm' USING ERRCODE = '55000';
    END IF;
    UPDATE core_firminvitation SET status = 'expired'
    WHERE company_id = p_company AND status = 'pending' AND expires_at <= now();
    INSERT INTO core_firminvitation (company_id, email, token_hash, status, created_by_id, created_at, expires_at)
    VALUES (p_company, lower(btrim(p_email)), p_token_hash, 'pending', app_user_id(), now(), p_expires)
    RETURNING id INTO v_id;
    RETURN v_id;
END
$$
"""

ACCEPT_FIRM_INVITATION = """
CREATE FUNCTION accept_firm_invitation(p_token_hash text) RETURNS bigint
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_id bigint;
    v_company bigint;
    v_admin_firm bigint;
    v_email text;
BEGIN
    SELECT firm_id INTO v_admin_firm FROM core_user
    WHERE id = app_user_id() AND is_firm_admin = true AND firm_id IS NOT NULL;
    IF v_admin_firm IS NULL THEN
        RAISE EXCEPTION 'only a firm administrator may accept an invitation' USING ERRCODE = '42501';
    END IF;

    -- A row is matched only while it is pending and not yet past its time; an exception here must raise before any
    -- write, because raising after one would roll it back along with everything else in this transaction. A stale
    -- pending row is swept to 'expired' by the next `create_firm_invitation` call for this company instead.
    SELECT id, company_id INTO v_id, v_company
    FROM core_firminvitation WHERE token_hash = p_token_hash AND status = 'pending' AND expires_at > now() FOR UPDATE;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;

    PERFORM 1 FROM core_company WHERE id = v_company AND firm_id IS NULL FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;

    UPDATE core_company SET firm_id = v_admin_firm WHERE id = v_company;
    UPDATE core_firminvitation
    SET status = 'accepted', accepted_at = now(), accepted_by_id = app_user_id(), accepted_firm_id = v_admin_firm
    WHERE id = v_id;

    SELECT email INTO v_email FROM core_user WHERE id = app_user_id();
    INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_company, app_user_id(), coalesce(v_email, ''), 'update', 'company', v_company::text, '["firm"]'::jsonb, now());
    INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_admin_firm, app_user_id(), coalesce(v_email, ''), 'update', 'company', v_company::text, '["firm"]'::jsonb, now());

    RETURN v_company;
END
$$
"""

FUNCTION_FORWARD, FUNCTION_REVERSE = definer_function_sql({
    "create_firm_invitation(bigint, text, text, timestamptz)": CREATE_FIRM_INVITATION,
    "accept_firm_invitation(text)": ACCEPT_FIRM_INVITATION,
})

FORWARD = [
    f"GRANT SELECT ON core_firminvitation TO {WEB_ROLE}",
    "ALTER TABLE core_firminvitation ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_firminvitation FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY tenant_select ON core_firminvitation FOR SELECT TO {WEB_ROLE} USING ({USER_COMPANY_CHECK})",
    *owner_policy_sql("core_firminvitation"),
    *audit_trigger_sql("core_firminvitation"),
    *FUNCTION_FORWARD,
]

REVERSE = [
    *FUNCTION_REVERSE,
    *drop_audit_trigger_sql("core_firminvitation"),
    "DROP POLICY IF EXISTS definer_functions ON core_firminvitation",
    "DROP POLICY IF EXISTS tenant_select ON core_firminvitation",
    "ALTER TABLE core_firminvitation NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core_firminvitation DISABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON core_firminvitation FROM {WEB_ROLE}",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0048_firm_invitation")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
