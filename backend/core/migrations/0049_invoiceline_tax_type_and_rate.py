# Per-line SST type and rate (ticket #52, D59 (9)).
#
# `tax_type` and `tax_rate` have no default at the model level: the owner must choose both for every line, so the
# one-off defaults below exist only to satisfy NOT NULL for any row that already exists (migration 0041 has no
# default of its own either, so this is the same shape); `preserve_default=False` means neither becomes the
# field's permanent default, matching Item.default_tax_type's own choice to require a value rather than guess one.
from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0048_worker_owner_email'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoiceline',
            name='tax_type',
            field=models.CharField(default='06', max_length=2),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='invoiceline',
            name='tax_rate',
            field=models.DecimalField(decimal_places=2, default=Decimal('0'), max_digits=5),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name='invoiceline',
            constraint=models.CheckConstraint(
                condition=models.Q(('tax_rate__gte', 0), ('tax_rate__lte', 100)),
                name='invoiceline_tax_rate_in_range',
            ),
        ),
    ]
