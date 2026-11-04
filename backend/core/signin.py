from django.contrib.auth import login
from django.db import connection

from .rls import IDENTITY_SETTING


def complete_sign_in(request, user):
    """The single place where a verified user becomes a signed-in session.

    Two-factor authentication will plug in here (D40 (7)): a second check can run before `login`.

    The request began with no identity, because the session did not exist yet. The new user becomes the database
    identity for the rest of the transaction, so the sign-in is recorded as theirs and not as a bare call (D54).
    """
    login(request, user, backend="core.auth.EmailBackend")
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, true)", [IDENTITY_SETTING, str(user.pk)])
