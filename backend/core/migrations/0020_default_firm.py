from django.db import migrations

DEFAULT_FIRM = "Default firm"


def assign_default_firm(apps, schema_editor):
    """Development and test data from before firms existed belongs to one default firm (D49). There is no production data."""
    Firm, Company, User = apps.get_model("core", "Firm"), apps.get_model("core", "Company"), apps.get_model("core", "User")
    companies, accountants = Company.objects.filter(firm__isnull=True), User.objects.filter(is_accountant=True, firm__isnull=True)
    if not (companies.exists() or accountants.exists()):
        return
    firm, _ = Firm.objects.get_or_create(name=DEFAULT_FIRM)
    companies.update(firm=firm)
    accountants.update(firm=firm)


class Migration(migrations.Migration):

    dependencies = [('core', '0019_firm')]
    operations = [migrations.RunPython(assign_default_firm, migrations.RunPython.noop)]
