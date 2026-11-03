"""What a firm administrator does for their own firm (D49): add and deactivate its accountants and administrators.

The web role cannot write users, so each step is a database function that checks the caller is an active firm
administrator and acts on the caller's own firm only (migration 0026). Its firm-trail entry is written there too.
"""

from django.db import DatabaseError, connection, transaction

from . import tokens
from .db import scalar


class PersonNotFound(Exception):
    """Nobody by that id is an accountant or administrator of the caller's firm."""


class LastAdministrator(Exception):
    """The firm would be left with no active administrator."""


def people():
    """The caller's firm's accountants and administrators, administrators first."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT person_id, person_email, person_is_admin, person_is_active FROM firm_people()")
        return [
            {"id": id_, "email": email, "is_admin": is_admin, "is_active": is_active}
            for id_, email, is_admin, is_active in cursor.fetchall()
        ]


def add(email, kind):
    """Create an accountant or administrator of the caller's firm and email them a link to choose a password.

    An address that already has an account is silently skipped, so the caller learns nothing about who is registered.
    """
    email = email.strip().lower()
    with transaction.atomic():
        created = scalar("SELECT create_firm_person(%s, %s)", [email, kind])
    if created:
        tokens.request_password_reset(email)


def deactivate(person_id):
    try:
        with transaction.atomic():
            scalar("SELECT deactivate_firm_person(%s)", [person_id])
    except DatabaseError as error:
        if "no such person" in str(error):
            raise PersonNotFound from error
        if "last active administrator" in str(error):
            raise LastAdministrator from error
        raise
