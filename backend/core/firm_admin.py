"""What a firm administrator does for their own firm (D49): add and deactivate its accountants and administrators.

The web role cannot write users, so each step is a database function that checks the caller is an active firm
administrator and acts on the caller's own firm only (migration 0026). Its firm-trail entry is written there too.
"""

from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from . import tokens
from .db import scalar


# The SQLSTATEs that `deactivate_firm_person` raises (migration 0026).
NO_SUCH_PERSON = "P0002"
LAST_ADMINISTRATOR = "55000"


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
    The email is sent inside the transaction: if it cannot be sent the person is not created, so the same address can
    be added again. (A view's exception becomes a 500 response before the request's own transaction sees it, so that
    transaction would commit.)
    """
    email = email.strip().lower()
    raw, hashed = tokens.new_token()
    expires = timezone.now() + timedelta(hours=settings.SETUP_LINK_HOURS)
    with transaction.atomic():
        created = scalar("SELECT create_firm_person(%s, %s, %s, %s)", [email, kind, hashed, expires])
        if created:
            tokens.send_setup_link(email, raw)


def deactivate(person_id):
    try:
        with transaction.atomic():
            scalar("SELECT deactivate_firm_person(%s)", [person_id])
    except DatabaseError as error:
        state = getattr(error.__cause__, "sqlstate", None)
        if state == NO_SUCH_PERSON:
            raise PersonNotFound from error
        if state == LAST_ADMINISTRATOR:
            raise LastAdministrator from error
        raise
