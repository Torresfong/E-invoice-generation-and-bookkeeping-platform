from django.core.management.base import BaseCommand, CommandError

from core import services


class Command(BaseCommand):
    help = (
        "Move a company to another firm, or bind a company without a firm to one: the old firm's accountants lose "
        "access and the new firm's gain it. A company changes firm only by this operator action (D49, D67); "
        "use unlink_company to end a link."
    )

    def add_arguments(self, parser):
        parser.add_argument("company_id", type=int)
        parser.add_argument("firm", help="The name of the firm the company moves to")

    def handle(self, *args, company_id, firm, **options):
        try:
            company = services.get_company(company_id)
            target = services.get_firm(firm)
        except services.CompanyNotFound as error:
            raise CommandError(f"No company has id {company_id}.") from error
        except services.FirmNotFound as error:
            raise CommandError(f"No firm is called {firm}.") from error
        try:
            services.move_company(company, target)
        except services.CompanyChanged as error:
            raise CommandError("The company was moved by someone else while this waited. Check where it is now and retry.") from error
        self.stdout.write(f"{company.name} now belongs to {target.name}.")
