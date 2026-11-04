from importlib import import_module

from django.db import migrations, models

# D54, ticket #84. `record_auth_event` used to write a sign-in event for any address a caller named, even with nobody signed
# in, so a bare call could fill a company's or a firm's append-only trail with forged history. Now:
#
# - with no signed-in user it writes only `login_failed`, and only by using up one counted sign-in attempt for that
#   address (`core_loginfailure.audited`), so one counted attempt gives at most one entry and a call with no unused
#   attempt writes nothing;
# - a signed-in user can still record only their own events, as before.
#
# Only the function is replaced and one column is added: the policies and the append-only and time-stamp triggers of both
# trails are untouched. Rows that already exist count as used, so a stored attempt cannot be turned into an entry.
RECORD_AUTH_EVENT = """
CREATE OR REPLACE FUNCTION record_auth_event(p_action text, p_email text DEFAULT NULL) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_user bigint;
    v_email text;
    v_firm bigint;
    v_caller bigint := app_user_id();
    v_correlation uuid := audit_correlation_id();
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
        IF v_caller IS NULL THEN
            -- Nobody is signed in: only a failed sign-in, and only against an attempt the throttle has counted and
            -- no entry has used yet (the key is worked out as in start_login_attempt).
            IF p_action <> 'login_failed' OR v_user IS NULL THEN
                RETURN;
            END IF;
            UPDATE core_loginfailure SET audited = true
            WHERE id = (
                SELECT id FROM core_loginfailure
                WHERE email_hash = encode(sha256(convert_to(lower(btrim(p_email)), 'UTF8')), 'hex') AND NOT audited
                ORDER BY at, id LIMIT 1 FOR UPDATE SKIP LOCKED
            );
            IF NOT FOUND THEN
                RETURN;
            END IF;
        END IF;
    END IF;
    IF v_user IS NULL THEN
        RETURN;
    END IF;
    IF v_firm IS NOT NULL THEN
        INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at, correlation_id)
        VALUES (v_firm, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now(), v_correlation);
        RETURN;
    END IF;
    INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at, correlation_id)
    SELECT a.company_id, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now(), v_correlation
    FROM core_companyaccess a WHERE a.user_id = v_user;
END
$$
"""
PREVIOUS = import_module("core.migrations.0031_record_auth_event_correlation_id").RECORD_AUTH_EVENT


class Migration(migrations.Migration):
    dependencies = [("core", "0031_record_auth_event_correlation_id")]
    operations = [
        # Existing rows are marked used; a new row, written by start_login_attempt, is not.
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    "ALTER TABLE core_loginfailure ADD COLUMN audited boolean NOT NULL DEFAULT true; "
                    "ALTER TABLE core_loginfailure ALTER COLUMN audited SET DEFAULT false",
                    "ALTER TABLE core_loginfailure DROP COLUMN audited",
                )
            ],
            state_operations=[
                migrations.AddField(
                    model_name="loginfailure",
                    name="audited",
                    field=models.BooleanField(db_default=False, default=False),
                )
            ],
        ),
        migrations.RunSQL(RECORD_AUTH_EVENT, PREVIOUS),
    ]
