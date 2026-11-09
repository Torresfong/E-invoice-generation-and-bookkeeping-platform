from django.core.management.base import BaseCommand

from core import firm_applications


class Command(BaseCommand):
    help = "List the firm applications waiting for a decision (D67). Decide each with approve_firm_application or refuse_firm_application."

    def handle(self, *args, **options):
        waiting = list(firm_applications.pending())
        if not waiting:
            self.stdout.write("No applications are waiting.")
        for application in waiting:
            self.stdout.write(
                f"#{application.pk}  {application.firm_name}  SSM {application.ssm_number}  "
                f"{application.contact_name} <{application.contact_email}>  {application.created_at:%Y-%m-%d %H:%M}"
            )
            if application.people:
                self.stdout.write(f"    People: {application.people}")
