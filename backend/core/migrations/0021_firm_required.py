import django.db.models.deletion
from django.db import migrations, models

from core.rls import IDENTITY_FUNCTION, WEB_ROLE, owner_policy_sql

# A firm is visible to the people who belong to it: a user sees the firm on their own user row, and no other
# (D49). The user table is itself under a policy that shows a user only their own row, so the lookup cannot
# reach anyone else's firm. The web role can read firms and never write them; the operator commands run as the
# table owner, which the owner policy lets through `FORCE ROW LEVEL SECURITY`. The job worker has no access.
FORWARD = [
    f"GRANT SELECT ON core_firm TO {WEB_ROLE}",
    "ALTER TABLE core_firm ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_firm FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY own_firm ON core_firm FOR SELECT TO {WEB_ROLE} "
    f"USING (id = (SELECT firm_id FROM core_user WHERE id = {IDENTITY_FUNCTION}()))",
    *owner_policy_sql("core_firm"),
]
REVERSE = [
    "DROP POLICY IF EXISTS definer_functions ON core_firm",
    "DROP POLICY IF EXISTS own_firm ON core_firm",
    "ALTER TABLE core_firm NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core_firm DISABLE ROW LEVEL SECURITY",
    f"REVOKE ALL ON core_firm FROM {WEB_ROLE}",
]


class Migration(migrations.Migration):

    dependencies = [('core', '0020_default_firm')]

    operations = [
        migrations.AlterField(
            model_name='company',
            name='firm',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='companies', to='core.firm'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.CheckConstraint(condition=models.Q(('is_accountant', True), _negated=True) | models.Q(('firm__isnull', False)), name='accountant_has_firm'),
        ),
        migrations.RunSQL(FORWARD, REVERSE),
    ]
