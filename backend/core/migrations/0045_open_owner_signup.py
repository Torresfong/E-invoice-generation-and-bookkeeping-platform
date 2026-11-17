from importlib import import_module

from django.db import migrations, models

from core.rls import definer_function_sql

# Owner sign-up with no firm link (ticket #143, D67 (2), (4)). Torres approved this migration on 2026-11-17: #138's
# approval (D67 (7)) did not cover the sign-up functions. It changes only these functions and adds one nullable column
# to the pending registration; no policy, no table and no grant other than the new function's EXECUTE changes.
#
#   - `start_open_registration` stores a pending registration with no firm and no link, and the owner's answer to
#     "does this company issue e-invoices?". Like `start_registration` it returns false when the address already has an
#     account, so the caller answers the same either way. The throttle and the on/off setting stay in the application.
#   - `complete_registration` now accepts a registration with no firm: the company is created with no firm, only the
#     owner gets a membership (the database already refuses an accountant row for a firm-less company, #138), and only
#     the company trail records it. If the registration carries an e-invoice answer, the company's setting and its first
#     history row are written in the same transaction, with the status left unchecked (D63) and the switch as chosen.
#     A registration through a firm's link carries no answer and behaves exactly as before.

START_OPEN_REGISTRATION = """
CREATE FUNCTION start_open_registration(p_token_hash text, p_email text, p_company_name text, p_einvoicing boolean,
                                        p_expires timestamptz)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
BEGIN
    IF btrim(p_company_name) = '' OR length(p_company_name) > 200 THEN
        RAISE EXCEPTION 'a company name of 1 to 200 characters is required' USING ERRCODE = '22023';
    END IF;
    IF p_einvoicing IS NULL THEN
        RAISE EXCEPTION 'an e-invoice answer is required' USING ERRCODE = '22023';
    END IF;
    DELETE FROM core_authtoken WHERE expires_at < now() - interval '30 days';
    IF EXISTS (SELECT 1 FROM core_user WHERE lower(email) = lower(btrim(p_email))) THEN
        RETURN false;
    END IF;
    INSERT INTO core_authtoken (purpose, token_hash, email, role, firm_id, company_name, einvoicing_enabled, expires_at, created_at)
    VALUES ('register', p_token_hash, lower(btrim(p_email)), '', NULL, btrim(p_company_name), p_einvoicing,
            LEAST(p_expires, now() + interval '24 hours'), now());
    RETURN true;
END
$$
"""

COMPLETE_REGISTRATION = """
CREATE OR REPLACE FUNCTION complete_registration(p_token_hash text, p_password_hash text)
RETURNS TABLE (new_user_id bigint, new_user_email text)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_token bigint;
    v_email text;
    v_name text;
    v_firm bigint;
    v_einvoicing boolean;
    v_company bigint;
    v_user bigint;
BEGIN
    IF p_password_hash !~ '^argon2[$]argon2(id|i|d)[$]v=' THEN
        RAISE EXCEPTION 'not an Argon2 password hash' USING ERRCODE = '22023';
    END IF;
    SELECT id, email, company_name, firm_id, einvoicing_enabled INTO v_token, v_email, v_name, v_firm, v_einvoicing
    FROM core_authtoken
    WHERE token_hash = p_token_hash AND purpose = 'register' AND used_at IS NULL AND expires_at > now()
    FOR UPDATE;
    IF v_token IS NULL THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;
    -- The firm, when there is one, is locked, as everywhere a company or an accountant of the firm is created
    -- (services.py), so the accountants read below cannot change under this transaction.
    IF v_firm IS NOT NULL THEN
        PERFORM 1 FROM core_firm WHERE id = v_firm FOR UPDATE;
    END IF;
    INSERT INTO core_user (password, last_login, is_superuser, email, is_active, date_joined)
    VALUES (p_password_hash, NULL, false, v_email, true, now())
    ON CONFLICT (lower(email)) DO NOTHING
    RETURNING id INTO v_user;
    IF v_user IS NULL THEN
        RAISE EXCEPTION 'invalid or expired link' USING ERRCODE = '28000';
    END IF;
    INSERT INTO core_company (name, firm_id, created_at) VALUES (v_name, v_firm, now()) RETURNING id INTO v_company;
    INSERT INTO core_companyaccess (user_id, company_id, role, created_at) VALUES (v_user, v_company, 'owner', now());
    IF v_firm IS NOT NULL THEN
        INSERT INTO core_companyaccess (user_id, company_id, role, created_at)
        SELECT u.id, v_company, 'accountant', now() FROM core_user u WHERE u.is_accountant AND u.firm_id = v_firm;
    END IF;
    IF v_einvoicing IS NOT NULL THEN
        -- The history row goes first: the database accepts a setting only when it equals the latest history row.
        INSERT INTO core_companyeinvoicesettingchange (company_id, changed_by_id, einvoicing_enabled, status, effective_from,
            legal_name, phone, sst_number, tourism_tax_number, tin, brn, rob, msic_code, address_line1, address_line2, city,
            postal_zone, state_code, country_code, acknowledged_by_id, acknowledged_at, acknowledgement_version, changed_at)
        VALUES (v_company, v_user, v_einvoicing, NULL, current_date, '', '', '', '', '', '', '', '', '', '', '', '', '',
                'MYS', NULL, NULL, '', now());
        INSERT INTO core_companyeinvoicesetting (company_id, einvoicing_enabled, status, effective_from, legal_name, phone,
            sst_number, tourism_tax_number, tin, brn, rob, msic_code, address_line1, address_line2, city, postal_zone,
            state_code, country_code, updated_at)
        VALUES (v_company, v_einvoicing, NULL, current_date, '', '', '', '', '', '', '', '', '', '', '', '', '', 'MYS', now());
    END IF;
    UPDATE core_authtoken SET used_at = now() WHERE id = v_token;
    INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
    VALUES (v_company, v_user, v_email, 'create', 'company', v_company::text,
            CASE WHEN v_firm IS NULL THEN '["name"]' ELSE '["name", "firm"]' END::jsonb, now());
    IF v_firm IS NOT NULL THEN
        INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)
        VALUES (v_firm, v_user, v_email, 'create', 'company', v_company::text, '["name", "firm"]'::jsonb, now());
    END IF;
    RETURN QUERY SELECT v_user, v_email;
END
$$
"""

FORWARD, REVERSE = definer_function_sql(
    {"start_open_registration(text, text, text, boolean, timestamptz)": START_OPEN_REGISTRATION}
)
# complete_registration is replaced in place (same signature, so its grants stay). The reverse puts 0029's back; it
# runs before the column it no longer reads is dropped.
FORWARD.insert(0, COMPLETE_REGISTRATION)
RESTORED = import_module("core.migrations.0029_owner_signup").COMPLETE_REGISTRATION
REVERSE.append(RESTORED.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))


class Migration(migrations.Migration):
    dependencies = [("core", "0044_invoiceline_item_name")]
    operations = [
        migrations.AddField(
            model_name="authtoken",
            name="einvoicing_enabled",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
