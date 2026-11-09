from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from core import firm_applications, services


class Command(BaseCommand):
    help = (
        "Approve a firm's application: create the firm and its first firm administrator, and email them a link to choose "
        "a password (D67)."
    )

    def add_arguments(self, parser):
        parser.add_argument("application_id", type=int)

    def handle(self, *args, application_id, **options):
        try:
            administrator = firm_applications.approve(application_id)
        except firm_applications.ApplicationNotFound as error:
            raise CommandError("No such application.") from error
        except firm_applications.AlreadyDecided as error:
            raise CommandError("That application was decided already.") from error
        except services.EmailInUse as error:
            raise CommandError("That address has an account now; nothing was created.") from error
        except IntegrityError as error:
            raise CommandError("A firm with that name exists now; nothing was created.") from error
        self.stdout.write(
            f"Created firm {administrator.firm.name} and its administrator {administrator.email}; emailed a password link."
        )
