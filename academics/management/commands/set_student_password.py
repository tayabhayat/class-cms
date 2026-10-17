
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User


class Command(BaseCommand):
    help = "Set a shared temporary password for all student accounts."

    def handle(self, *args, **options):
        students = User.objects.filter(role=User.Role.STUDENT)
        count = students.count()

        if count == 0:
            raise CommandError("No student accounts were found.")

        self.stdout.write(f"Found {count} student accounts.")

        password = getpass("Enter the shared temporary password: ")
        confirmation = getpass("Confirm the temporary password: ")

        if not password or password != confirmation:
            raise CommandError("Passwords are empty or do not match.")

        if len(password) < 12:
            raise CommandError(
                "Use a temporary password of at least 12 characters."
            )

        with transaction.atomic():
            for student in students.iterator():
                student.set_password(password)
                student.must_change_password = True
                student.save(
                    update_fields=["password", "must_change_password"]
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {count} student accounts. "
                "Students must change their password on first login."
            )
        )
