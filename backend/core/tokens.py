"""Single-use, expiring links for password reset and invitations (D40 (5), D48).

The token is random and only its SHA-256 hash is stored. Everything that runs before anyone is signed in
goes through SECURITY DEFINER functions (D40 (2)). Links are built from `settings.BASE_URL`, never from the
request's Host header, so a forged header cannot point an emailed link at another site.
"""

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import ports
from .db import scalar
from .models import AuthToken


class InvalidLink(Exception):
    """The link is unknown, expired or already used. The three cases are deliberately not told apart."""


class TooManyInvitations(Exception):
    """The company has reached its daily limit of invitations."""


def new_token():
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def link(path, raw):
    return f"{settings.BASE_URL.rstrip('/')}/{path}/{raw}/"


def link_is_valid(raw, purpose):
    return scalar("SELECT token_is_valid(%s, %s)", [hash_token(raw), purpose])


def request_password_reset(email):
    """Email a reset link if the address has an account; do nothing otherwise. The caller answers the same either way."""
    raw, hashed = new_token()
    expires = timezone.now() + timedelta(hours=settings.RESET_LINK_HOURS)
    if not scalar("SELECT create_password_reset(%s, %s, %s)", [hashed, email, expires]):
        return
    ports.get().email.send(
        email,
        _("Reset your password"),
        _("Use this link to choose a new password. It works once and expires in %(hours)s hour(s):\n\n%(url)s\n")
        % {"hours": settings.RESET_LINK_HOURS, "url": link("reset", raw)},
    )


def complete_password_reset(raw, password):
    try:
        # A savepoint keeps the refusal from aborting the request's transaction.
        with transaction.atomic():
            return scalar("SELECT complete_password_reset(%s, %s)", [hash_token(raw), make_password(password)])
    except DatabaseError as error:
        raise InvalidLink from error


def invite_staff(company, email):
    """Email an invitation to join `company` as staff. The database checks that the caller owns the company.

    An address that already has an account is silently skipped, so the caller learns nothing about who is
    registered. Raises TooManyInvitations at the daily limit.
    """
    raw, hashed = new_token()
    expires = timezone.now() + timedelta(days=settings.INVITE_LINK_DAYS)
    try:
        with transaction.atomic():
            created = scalar("SELECT create_invitation(%s, %s, %s, %s)", [hashed, email, company.pk, expires])
    except DatabaseError as error:
        if "too many invitations" in str(error):
            raise TooManyInvitations from error
        raise
    if not created:
        return
    ports.get().email.send(
        email,
        _("You are invited to join %(company)s") % {"company": company.name},
        _(
            "You have been invited to join %(company)s. Use this link to choose a password. "
            "It works once and expires in %(days)s days:\n\n%(url)s\n"
        )
        % {"company": company.name, "days": settings.INVITE_LINK_DAYS, "url": link("accept", raw)},
    )


def accept_invitation(raw, password):
    try:
        with transaction.atomic():
            return scalar("SELECT accept_invitation(%s, %s)", [hash_token(raw), make_password(password)])
    except DatabaseError as error:
        raise InvalidLink from error


def send_setup_link(email, raw):
    """Email the link for choosing a first password. The firm portal is English only (D49)."""
    ports.get().email.send(
        email,
        "Choose your password",
        "An account has been created for you. Use this link to choose a password. "
        f"It works once and expires in {settings.SETUP_LINK_HOURS} hours:\n\n{link('reset', raw)}\n",
    )


def issue_setup_link(user):
    """Create and email a first-password link for `user`. For operator commands, which run as the table owner."""
    raw, hashed = new_token()
    expires = timezone.now() + timedelta(hours=settings.SETUP_LINK_HOURS)
    AuthToken.objects.create(purpose=AuthToken.RESET, token_hash=hashed, email=user.email, user=user, expires_at=expires)
    send_setup_link(user.email, raw)
