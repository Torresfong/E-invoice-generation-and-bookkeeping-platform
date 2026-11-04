from django.core.management.base import BaseCommand

from core import jobs


class Command(BaseCommand):
    help = (
        "Queue the recurring background jobs (today: the daily purge of finished jobs) unless they are already queued. "
        "Run it on every deploy; a job that is already waiting or running is left alone."
    )

    def handle(self, *args, **options):
        jobs.schedule_purge()
        self.stdout.write("Scheduled jobs are queued.")
