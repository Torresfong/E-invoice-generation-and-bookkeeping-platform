from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError

from core import services


class Command(BaseCommand):
    help = "Create an accounting firm. Firms are created only here, by the platform operator (D49)."

    def add_arguments(self, parser):
        parser.add_argument("name")

    def handle(self, *args, name, **options):
        try:
            firm = services.create_firm(name)
        except services.FirmNameRequired as error:
            raise CommandError("A firm needs a name.") from error
        except IntegrityError as error:
            raise CommandError("A firm with that name already exists.") from error
        self.stdout.write(f"Created firm {firm.name}.")
