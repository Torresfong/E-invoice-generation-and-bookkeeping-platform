from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core import services, tokens


class Command(BaseCommand):
    help = (
        "Create an accounting firm with its first firm administrator, and email them a link to choose a password. "
        "Firms are created only here, by the platform operator (D49)."
    )

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("administrator_email", help="The firm's first administrator, who then creates its accountants")

    def handle(self, *args, name, administrator_email, **options):
        try:
            # One transaction with the email, so a firm whose first administrator cannot be reached is not left behind.
            with transaction.atomic():
                administrator = services.create_firm_with_administrator(name, administrator_email)
                tokens.issue_setup_link(administrator)
        except services.FirmNameRequired as error:
            raise CommandError("A firm needs a name.") from error
        except services.EmailInUse as error:
            raise CommandError("That address already has an account.") from error
        except IntegrityError as error:
            raise CommandError("A firm with that name already exists.") from error
        self.stdout.write(f"Created firm {administrator.firm.name} and its administrator {administrator.email}; emailed a password link.")
