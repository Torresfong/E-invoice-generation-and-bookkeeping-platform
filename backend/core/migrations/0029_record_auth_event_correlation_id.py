from importlib import import_module

from django.db import migrations

# 0026's `record_auth_event`, with one change (02k): the correlation id is read once and stored on every entry it
# writes, so a sign-in for a user in several companies carries one id even outside a request or job, where each
# row's column default would otherwise draw its own. Only the function is replaced: the policies and the append-only
# and time-stamp triggers of both trails are untouched.
RECORD_AUTH_EVENT = (
    import_module("core.migrations.0026_firm_administrator_functions")
    .RECORD_AUTH_EVENT.replace("    v_caller bigint := app_user_id();\n", "    v_caller bigint := app_user_id();\n    v_correlation uuid := audit_correlation_id();\n")
    .replace(
        "(firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)\n        VALUES (v_firm, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now());",
        "(firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at, correlation_id)\n        VALUES (v_firm, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now(), v_correlation);",
    )
    .replace(
        "(company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at)\n    SELECT a.company_id, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now()",
        "(company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at, correlation_id)\n    SELECT a.company_id, v_user, v_email, p_action, 'user', v_user::text, '[]'::jsonb, now(), v_correlation",
    )
)
PREVIOUS = import_module("core.migrations.0026_firm_administrator_functions").RECORD_AUTH_EVENT
assert RECORD_AUTH_EVENT.count("v_correlation") == 3, "the text of 0026 has drifted: the function would not store the id"


class Migration(migrations.Migration):
    dependencies = [("core", "0028_audit_entry_correlation_id")]
    operations = [migrations.RunSQL(RECORD_AUTH_EVENT, PREVIOUS)]
