from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Build the current combined Emprendedoras master application."

    def handle(self, *args, **options):
        call_command("build_master_forms", "--only", "E_A2", stdout=self.stdout)
