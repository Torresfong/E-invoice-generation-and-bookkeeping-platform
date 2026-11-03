from django.db import migrations

from core.rls import definer_function_sql

# Owner self-registration (D48, D49 (4)). Everything here is reachable before anyone is signed in, or by an accountant
# who cannot write the token table, so each step is a SECURITY DEFINER function (D40 (2)) and the rules sit in the
# database, not in the view: the firm comes from the caller or from the link, never from an argument.

# A firm accountant creates a single-use sign-up link for their own firm. The link grants no data access: it only lets
# someone start a registration. Returns nothing; the caller already holds the raw token. Capped at seven days.
CREATE_SIGNUP_LINK = """
CREATE FUNCTION create_signup_link(p_token_hash text, p_expires timestamptz) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_firm bigint;
    v_email text;
    v_token bigint;
BEGIN
    SELECT firm_id, email INTO v_firm, v_email FROM core_user
    WHERE id = app_user_id() AND is_accountant AND is_active AND firm_id IS NOT NULL;
    IF v_firm IS NULL THEN
        RAISE EXCEPTION 'only a firm accountant may create a sign-up link' USING ERRCODE = '42501';
    END IF;
    DELETE FROM core_authtoken WHERE expires_at < now() - interval '30 days';
    INSERT INTO core_authtoken (purpose, token_hash, email, role, firm_id, company_name, expires_at, created_at)
    VALUES ('signup', p_token_hash, '', '', v_firm, '', LEAST(p_expires, now() + interval '7 days'), now())
    RETURNING id INTO v_token;
    INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_firm, app_user_id(), v_email, 'create', 'signup_link', v_token::text, '[]'::jsonb, now());
END
$$
"""

# Spends a sign-up link and stores a pending registration for the link's firm: an address and a company name, nothing
# else. Nothing here creates a company or a user. Returns false, with the link spent, when the address already has an
# account, so the caller can answer the same either way and learns nothing about who is registered. An unknown, expired
# or used link raises 28000. The verification link is capped at 24 hours.
START_REGISTRATION = """
CREATE FUNCTION start_registration(p_link_hash text, p_token_hash text, p_email text, p_company_name text, p_expires timestamptz)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_link bigint;
    v_firm bigint;
    v_token bigint;
BEGIN
    IF btrim(p_company_name) = '' OR length(p_company_name) > 200 THEN
        RAISE EXCEPTION 'a company name of 1 to 200 characters is required' USING ERRCODE = '22023';
    END IF;
    SELECT id, firm_id INTO v_link, v_firm FROM core_authtoken
    WHERE token_hash = p_link_hash AND purpose = 'signup' AND used_at IS NULL AND expires_at > now()
    FOR UPDATE;
    IF v_link IS NULL THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;
    UPDATE core_authtoken SET used_at = now() WHERE id = v_link;
    IF EXISTS (SELECT 1 FROM core_user WHERE lower(email) = lower(btrim(p_email))) THEN
        RETURN false;
    END IF;
    INSERT INTO core_authtoken (purpose, token_hash, email, role, firm_id, company_name, expires_at, created_at)
    VALUES ('register', p_token_hash, lower(btrim(p_email)), '', v_firm, btrim(p_company_name),
            LEAST(p_expires, now() + interval '24 hours'), now())
    RETURNING id INTO v_token;
    INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_firm, NULL, '', 'create', 'registration', v_token::text, '["email", "company_name"]'::jsonb, now());
    RETURN true;
END
$$
"""

# Opening the verification link and choosing a password creates the company, its single owner, the owner's membership and
# the firm's accountants' memberships, in this one transaction (D48), and spends the link. The firm comes from the pending
# registration. If the address has since got an account the link is refused and nothing is created. The company's trail
# records the creation by the new owner; the firm's trail records it too (D50).
COMPLETE_REGISTRATION = """
CREATE FUNCTION complete_registration(p_token_hash text, p_password_hash text)
RETURNS TABLE (new_user_id bigint, new_user_email text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_token bigint;
    v_email text;
    v_name text;
    v_firm bigint;
    v_company bigint;
    v_user bigint;
BEGIN
    IF p_password_hash !~ '^argon2[$]argon2(id|i|d)[$]v=' THEN
        RAISE EXCEPTION 'not an Argon2 password hash' USING ERRCODE = '22023';
    END IF;
    SELECT id, email, company_name, firm_id INTO v_token, v_email, v_name, v_firm FROM core_authtoken
    WHERE token_hash = p_token_hash AND purpose = 'register' AND used_at IS NULL AND expires_at > now()
    FOR UPDATE;
    IF v_token IS NULL THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;
    -- The firm is locked, as everywhere a company or an accountant of the firm is created (services.py), so the
    -- accountants read below cannot change under this transaction.
    PERFORM 1 FROM core_firm WHERE id = v_firm FOR UPDATE;
    INSERT INTO core_user (password, last_login, is_superuser, email, is_active, date_joined)
    VALUES (p_password_hash, NULL, false, v_email, true, now())
    ON CONFLICT (lower(email)) DO NOTHING
    RETURNING id INTO v_user;
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;
    INSERT INTO core_company (name, firm_id, created_at) VALUES (v_name, v_firm, now()) RETURNING id INTO v_company;
    INSERT INTO core_companyaccess (user_id, company_id, role, created_at) VALUES (v_user, v_company, 'owner', now());
    INSERT INTO core_companyaccess (user_id, company_id, role, created_at)
    SELECT u.id, v_company, 'accountant', now() FROM core_user u WHERE u.is_accountant AND u.firm_id = v_firm;
    UPDATE core_authtoken SET used_at = now() WHERE id = v_token;
    INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_company, v_user, v_email, 'create', 'company', v_company::text, '["name", "firm"]'::jsonb, now());
    INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_firm, v_user, v_email, 'create', 'company', v_company::text, '["name", "firm"]'::jsonb, now());
    RETURN QUERY SELECT v_user, v_email;
END
$$
"""

FORWARD, REVERSE = definer_function_sql(
    {
        "create_signup_link(text, timestamptz)": CREATE_SIGNUP_LINK,
        "start_registration(text, text, text, text, timestamptz)": START_REGISTRATION,
        "complete_registration(text, text)": COMPLETE_REGISTRATION,
    }
)


class Migration(migrations.Migration):
    dependencies = [("core", "0028_signup_token_fields")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
