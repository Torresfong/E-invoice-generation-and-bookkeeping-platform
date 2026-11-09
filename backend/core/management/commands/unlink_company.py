from django.core.management.base import BaseCommand, CommandError

from core import services


class Command(BaseCommand):
    help = (
        "End a company's firm link: the old firm's accountants lose access and the company keeps its client access. "
        "A company loses its firm only by this operator action (D49, D67)."
    )

    def add_arguments(self, parser):
        parser.add_argument("company_id", type=int)

    def handle(self, *args, company_id, **options):
        try:
            company = services.get_company(company_id)
        except services.CompanyNotFound as error:
            raise CommandError(f"No company has id {company_id}.") from error
        try:
            company = services.move_company(company, None)
        except services.CompanyChanged as error:
            raise CommandError(
                "The company was moved by someone else while this waited. Check where it is now and retry."
            ) from error
        self.stdout.write(f"{company.name} no longer belongs to a firm.")
