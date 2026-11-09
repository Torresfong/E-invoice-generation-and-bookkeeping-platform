from django.core.management.base import BaseCommand, CommandError

from core import firm_applications


class Command(BaseCommand):
    help = "Refuse a firm's application. Nothing is created and the applicant is not emailed (D67)."

    def add_arguments(self, parser):
        parser.add_argument("application_id", type=int)
        parser.add_argument("--note", default="", help="Why, kept on the application for the operator's own records")

    def handle(self, *args, application_id, note, **options):
        try:
            firm_applications.refuse(application_id, note)
        except firm_applications.ApplicationNotFound as error:
            raise CommandError("No such application.") from error
        except firm_applications.AlreadyDecided as error:
            raise CommandError("That application was decided already.") from error
        self.stdout.write(f"Refused application {application_id}.")
