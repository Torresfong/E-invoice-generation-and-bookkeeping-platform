import django.db.models.deletion
from django.db import migrations, models

from core.audit import enable_audit
from core.rls import enable_tenant_rls

# Gapless numbering (ticket #45, D18, D44, D59 (6)). The counter is guarded in the database itself, so no writer
# (the numbering service, a shell, a later screen) can skip, reuse or take back a number:
#   - an update may only add one to `next_number`, or, while no number has been issued, restart the sequence from a
#     new prefix, padding and starting number;
#   - a sequence that has issued a number cannot be deleted, because a new one would start again from 1.
# The company and document type of a sequence never change.

GUARD_FUNCTION = """
CREATE FUNCTION document_sequence_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public, pg_temp AS
$$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.next_number > OLD.start_number THEN
            RAISE EXCEPTION 'document numbers have been issued from this sequence; it cannot be deleted';
        END IF;
        RETURN OLD;
    END IF;
    IF NEW.company_id <> OLD.company_id OR NEW.document_type <> OLD.document_type THEN
        RAISE EXCEPTION 'a sequence cannot move to another company or document type';
    END IF;
    IF NEW.prefix = OLD.prefix AND NEW.start_number = OLD.start_number AND NEW.padding = OLD.padding
       AND NEW.next_number IN (OLD.next_number, OLD.next_number + 1) THEN
        RETURN NEW;  -- a number was taken, or nothing changed
    END IF;
    IF OLD.next_number = OLD.start_number AND NEW.next_number = NEW.start_number THEN
        RETURN NEW;  -- nothing issued yet: the owner may still choose the prefix and starting number
    END IF;
    RAISE EXCEPTION 'issued document numbers cannot be skipped, reused or taken back';
END
$$
"""

GUARD_TRIGGER = """
CREATE TRIGGER document_sequence_guard BEFORE UPDATE OR DELETE ON core_documentsequence
FOR EACH ROW EXECUTE FUNCTION document_sequence_guard()
"""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_job_execution"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentSequence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(max_length=20)),
                ("prefix", models.CharField(blank=True, max_length=10)),
                ("start_number", models.BigIntegerField()),
                ("next_number", models.BigIntegerField()),
                ("padding", models.SmallIntegerField(default=0, db_default=0)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="core.company")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("company", "document_type"), name="one_sequence_per_company_and_type"),
                    models.CheckConstraint(
                        condition=models.Q(("padding__gte", 0), ("padding__lte", 10)), name="sequence_padding_range"
                    ),
                    models.CheckConstraint(condition=models.Q(("start_number__gte", 1)), name="sequence_start_positive"),
                    models.CheckConstraint(
                        condition=models.Q(("next_number__gte", models.F("start_number"))),
                        name="sequence_next_not_before_start",
                    ),
                ],
            },
        ),
        enable_tenant_rls("core_documentsequence"),
        enable_audit("core_documentsequence"),
        migrations.RunSQL(
            [GUARD_FUNCTION, GUARD_TRIGGER],
            ["DROP TRIGGER IF EXISTS document_sequence_guard ON core_documentsequence",
             "DROP FUNCTION IF EXISTS document_sequence_guard()"],
        ),
    ]
