from django.core.management.base import BaseCommand

from core import einvoicing


class Command(BaseCommand):
    help = (
        "List the companies with e-invoicing on whose supplier details are incomplete, naming what is missing by label "
        "and never by value (D59, D63). Approval refuses such a company; run this before go-live and fix each one "
        "with its owner. Needs the owner connection to be allowed the worker role (docs/architecture/tenancy.md)."
    )

    def handle(self, *args, **options):
        listed = einvoicing.companies_with_supplier_gaps()
        if not listed:
            self.stdout.write("No company with e-invoicing on has incomplete supplier details.")
        for company, gaps in listed:
            self.stdout.write(f"#{company.pk}  {company.name}: missing {', '.join(str(gap) for gap in gaps)}")
