import django.db.models.deletion
import django.db.models.functions.text
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0018_audit_viewed_action'),
    ]

    operations = [
        migrations.CreateModel(
            name='Firm',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.AddConstraint(
            model_name='firm',
            constraint=models.UniqueConstraint(django.db.models.functions.text.Lower('name'), name='firm_name_unique_ignoring_case'),
        ),
        # Nullable for now: the next migration gives existing rows a firm, the one after makes it required.
        migrations.AddField(
            model_name='user',
            name='firm',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='users', to='core.firm'),
        ),
        migrations.AddField(
            model_name='company',
            name='firm',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='companies', to='core.firm'),
        ),
    ]
