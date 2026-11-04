from django.core.management.base import BaseCommand

from core import jobs


class Command(BaseCommand):
    help = (
        "Print the number of failed background jobs, across all companies. Alert when it is above zero: a failed "
        "job keeps only its error class (ADR 0001), so this count is the signal to go and look."
    )

    def handle(self, *args, **options):
        self.stdout.write(str(jobs.failed_count()))
