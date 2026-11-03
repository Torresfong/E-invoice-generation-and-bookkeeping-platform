from django.db import migrations, models

# The correlation id on the company trail (D50 (6), ticket 02k), the same `audit_correlation_id()` that fills the firm
# trail's column (0023). The column is added empty and the default is set afterwards, so the rows that exist stay null
# instead of being given ids that tie them to nothing. Every writer of an entry (the audit trigger, `record_auth_event`
# and the worker's copy of the trigger, and the ORM) leaves the column out, so the default supplies the id and none of
# those functions has to be replaced. The policies and the append-only and time-stamp triggers are untouched.
FORWARD = [
    "ALTER TABLE core_auditentry ADD COLUMN correlation_id uuid NULL",
    "ALTER TABLE core_auditentry ALTER COLUMN correlation_id SET DEFAULT audit_correlation_id()",
]
REVERSE = ["ALTER TABLE core_auditentry DROP COLUMN correlation_id"]


class Migration(migrations.Migration):
    dependencies = [("core", "0029_owner_signup")]
    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(FORWARD, REVERSE)],
            state_operations=[
                migrations.AddField(
                    model_name="auditentry",
                    name="correlation_id",
                    field=models.UUIDField(
                        null=True,
                        blank=True,
                        db_default=models.Func(function="audit_correlation_id", output_field=models.UUIDField()),
                    ),
                )
            ],
        )
    ]
