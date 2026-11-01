from django.core.management.base import BaseCommand, CommandError

from core import services, tokens


class Command(BaseCommand):
    help = (
        "Create a firm accountant in a firm, with access to that firm's companies, and email them a link to choose "
        "a password. Accountants are created only here, never by a client (D48, D49)."
    )

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("firm", help="The name of the firm, created with create_firm")

    def handle(self, *args, email, firm, **options):
        try:
            accountant = services.create_accountant(email, services.get_firm(firm))
        except services.FirmNotFound as error:
            raise CommandError(f"No firm is called {firm}.") from error
        except services.EmailInUse as error:
            raise CommandError("That address already has an account.") from error
        tokens.request_password_reset(accountant.email)
        self.stdout.write(f"Created accountant {accountant.email} of {accountant.firm.name} and emailed a password link.")
