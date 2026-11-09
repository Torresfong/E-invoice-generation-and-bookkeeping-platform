import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0035_company_einvoice_setting")]

    operations = [
        migrations.AlterField(
            model_name="company",
            name="firm",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="companies",
                to="core.firm",
            ),
        ),
    ]
