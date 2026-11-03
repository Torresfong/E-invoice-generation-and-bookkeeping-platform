"""Owner self-registration through a firm's sign-up link (D48, D49 (4)).

An accountant makes a sign-up link for their firm. Whoever opens it gives an email address and a company name, and
nothing is created yet: a pending registration is stored and a verification link is emailed. Only when the owner
opens that link and sets a password are the company, the owner and the owner's membership created, in one database
transaction. Every step runs in a SECURITY DEFINER function (migration 0029), because none of them is signed in.
Tokens are random and only their hashes are stored (see `tokens`).
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import ports
from .db import scalar
from .tokens import InvalidLink, hash_token, link, new_token

# The SQLSTATE the functions of migration 0029 raise for a link that is unknown, expired or used.
INVALID_LINK = "28000"


def create_signup_link():
    """A sign-up link for the signed-in accountant's own firm; the database takes the firm from the caller."""
    raw, hashed = new_token()
    expires = timezone.now() + timedelta(days=settings.SIGNUP_LINK_DAYS)
    with transaction.atomic():
        scalar("SELECT create_signup_link(%s, %s)", [hashed, expires])
    return link("signup", raw)


def start(signup_link, email, company_name):
    """Spend a firm's sign-up link, store a pending registration and email the verification link.

    An address that already has an account is silently skipped, so the caller answers the same either way. The email
    is sent inside the transaction: if it cannot be sent the link is not spent. Raises InvalidLink for a link that is
    unknown, expired or used.
    """
    raw, hashed = new_token()
    expires = timezone.now() + timedelta(hours=settings.REGISTRATION_LINK_HOURS)
    try:
        with transaction.atomic():
            created = scalar(
                "SELECT start_registration(%s, %s, %s, %s, %s)",
                [hash_token(signup_link), hashed, email, company_name, expires],
            )
            if created:
                ports.get().email.send(
                    email,
                    _("Confirm your registration"),
                    _(
                        "Use this link to choose a password and finish creating %(company)s. "
                        "It works once and expires in %(hours)s hours:\n\n%(url)s\n"
                    )
                    % {"company": company_name, "hours": settings.REGISTRATION_LINK_HOURS, "url": link("register", raw)},
                )
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) == INVALID_LINK:
            raise InvalidLink from error
        raise


def complete(verification_link, password):
    """Create the company, the owner and the owner's membership from a pending registration. Returns the new owner.

    Raises InvalidLink for a link that is unknown, expired or used, or whose address got an account in the meantime.
    The returned user carries only what the database function returned, as `EmailBackend` does, because the web role
    cannot read a user before one is signed in.
    """
    encoded = make_password(password)
    try:
        # A savepoint keeps the refusal from aborting the request's transaction.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT new_user_id, new_user_email FROM complete_registration(%s, %s)",
                [hash_token(verification_link), encoded],
            )
            user_id, email = cursor.fetchone()
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) == INVALID_LINK:
            raise InvalidLink from error
        raise
    return get_user_model()(pk=user_id, email=email, password=encoded, is_active=True)
