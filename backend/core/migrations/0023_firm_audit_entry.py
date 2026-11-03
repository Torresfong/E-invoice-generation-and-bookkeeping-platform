import django.db.models.deletion
import django.db.models.functions.datetime
from django.db import migrations, models

from core.rls import IDENTITY_FUNCTION, WEB_ROLE, owner_policy_sql

# The firm audit trail (D50): the company trail's counterpart for events that belong to a firm, not a company.
# The rules are the company trail's (0014): the web role may only read and insert, as the caller; the same two
# trigger functions refuse changes and stamp the time. A user reads an entry only for their own firm.

OWN_FIRM = f"firm_id = (SELECT firm_id FROM core_user WHERE id = {IDENTITY_FUNCTION}())"
AS_CALLER = (
    f"actor_id IS NOT DISTINCT FROM {IDENTITY_FUNCTION}() AND actor_email IN ('', "
    f"coalesce((SELECT email FROM core_user WHERE id = {IDENTITY_FUNCTION}()), ''))"
)

# The correlation id of the request or job (`app.correlation_id`, set once and transaction-local), or a new one for
# an entry written outside both. The web role can set any setting, so a value that is not a UUID is ignored rather
# than allowed to make the caller's own inserts fail.
CORRELATION_FUNCTION = """
CREATE FUNCTION audit_correlation_id() RETURNS uuid LANGUAGE sql VOLATILE
SET search_path = pg_catalog, pg_temp AS
$$ SELECT CASE WHEN current_setting('app.correlation_id', true)
        ~ '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    THEN current_setting('app.correlation_id', true)::uuid ELSE gen_random_uuid() END $$
"""

# A firm account's sign-in goes to its firm's trail; anyone else's still goes into the trail of each company they
# belong to (0014, which holds the rest of this function's reasoning).
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
        SELECT id, email, CASE WHEN is_accountant THEN firm_id END INTO v_user, v_email, v_firm
        FROM core_user WHERE id = app_user_id();
    ELSE
        SELECT id, email, CASE WHEN is_accountant THEN firm_id END INTO v_user, v_email, v_firm
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

TABLE_POLICIES = [
    f"GRANT SELECT, INSERT ON core_firmauditentry TO {WEB_ROLE}",
    "ALTER TABLE core_firmauditentry ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_firmauditentry FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY firm_entries ON core_firmauditentry FOR SELECT TO {WEB_ROLE} USING ({OWN_FIRM})",
    f"CREATE POLICY firm_inserts ON core_firmauditentry FOR INSERT TO {WEB_ROLE} WITH CHECK ({OWN_FIRM} AND {AS_CALLER})",
    *owner_policy_sql("core_firmauditentry"),
    "CREATE TRIGGER audit_entries_are_stamped BEFORE INSERT ON core_firmauditentry "
    "FOR EACH ROW EXECUTE FUNCTION audit_entry_stamp()",
    "CREATE TRIGGER audit_rows_are_append_only BEFORE UPDATE OR DELETE ON core_firmauditentry "
    "FOR EACH ROW EXECUTE FUNCTION audit_entries_are_append_only()",
]

TABLE_POLICIES_REVERSE = [
    "DROP TRIGGER IF EXISTS audit_rows_are_append_only ON core_firmauditentry",
    "DROP TRIGGER IF EXISTS audit_entries_are_stamped ON core_firmauditentry",
    "DROP POLICY IF EXISTS definer_functions ON core_firmauditentry",
    "DROP POLICY IF EXISTS firm_inserts ON core_firmauditentry",
    "DROP POLICY IF EXISTS firm_entries ON core_firmauditentry",
    "ALTER TABLE core_firmauditentry DISABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON core_firmauditentry FROM {WEB_ROLE}",
]


def previous_record_auth_event():
    from importlib import import_module

    old = import_module("core.migrations.0014_audit_trail").RECORD_AUTH_EVENT
    return old.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


class Migration(migrations.Migration):
    dependencies = [("core", "0022_accountant_rows_match_firm")]
    operations = [
        migrations.RunSQL(CORRELATION_FUNCTION, "DROP FUNCTION IF EXISTS audit_correlation_id()"),
        migrations.CreateModel(
            name="FirmAuditEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("actor_id", models.BigIntegerField(blank=True, null=True)),
                ("actor_email", models.CharField(blank=True, max_length=254)),
                ("action", models.CharField(choices=[("login", "Signed in"), ("login_failed", "Failed sign-in"), ("logout", "Signed out"), ("create", "Created"), ("update", "Updated"), ("delete", "Deleted"), ("viewed", "Viewed")], max_length=20)),
                ("target_type", models.CharField(blank=True, max_length=100)),
                ("target_id", models.CharField(blank=True, max_length=40)),
                ("changed_fields", models.JSONField(default=list)),
                ("occurred_at", models.DateTimeField(db_default=django.db.models.functions.datetime.Now())),
                ("correlation_id", models.UUIDField(db_default=models.Func(function="audit_correlation_id", output_field=models.UUIDField()))),
                ("firm", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="+", to="core.firm")),
            ],
            options={
                "ordering": ["-occurred_at", "-id"],
                "indexes": [models.Index(fields=["firm", "-occurred_at"], name="firm_audit_firm_time")],
            },
        ),
        migrations.RunSQL(TABLE_POLICIES, TABLE_POLICIES_REVERSE),
        migrations.RunSQL(RECORD_AUTH_EVENT, previous_record_auth_event()),
    ]
