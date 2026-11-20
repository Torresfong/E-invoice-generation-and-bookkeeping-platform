"""A client owner invites a firm by email and a firm administrator accepts (D67 (3)).

Sending and accepting both run in a SECURITY DEFINER function (migration 0048), like the invitation link style
of D40 (5): sending checks, inside the database, that the caller owns the company and that it has no firm yet;
accepting is done by a firm administrator who has no `CompanyAccess` row for this company at all. Tokens are
random and only their hashes are stored, as with every other single-use link (see `tokens`).
"""

from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import ports
from .db import scalar
from .tokens import InvalidLink, hash_token, link, new_token

# SQLSTATEs the functions of migration 0048 raise.
ALREADY_HAS_FIRM = "55000"
UNIQUE_VIOLATION = "23505"
NOT_OWNER_OR_NOT_ADMIN = "42501"
INVALID_LINK = "28000"


class CompanyAlreadyHasFirm(Exception):
    """The company already has a firm; it cannot invite another (D67 (3))."""


class InvitationAlreadyPending(Exception):
    """An invitation is already pending for this company; it can be sent again once that one expires."""


class NotFirmAdministrator(Exception):
    """The signed-in user is not a firm administrator of an approved firm, so they cannot accept."""


def send(company, email):
    """Email an invitation to `email` to act as `company`'s firm. The database checks that the caller owns the
    company and that it has no firm yet.

    Raises CompanyAlreadyHasFirm or InvitationAlreadyPending; neither creates or sends anything.
    """
    raw, hashed = new_token()
    expires = timezone.now() + timedelta(days=settings.FIRM_INVITATION_LINK_DAYS)
    try:
        with transaction.atomic():
            scalar("SELECT create_firm_invitation(%s, %s, %s, %s)", [company.pk, email.strip(), hashed, expires])
    except DatabaseError as error:
        sqlstate = getattr(error.__cause__, "sqlstate", None)
        if sqlstate == ALREADY_HAS_FIRM:
            raise CompanyAlreadyHasFirm from error
        if sqlstate == UNIQUE_VIOLATION:
            raise InvitationAlreadyPending from error
        raise
    ports.get().email.send(
        email,
        _("You are invited to act for %(company)s") % {"company": company.name},
        _(
            "You have been invited to act as the accounting firm for %(company)s. Sign in, or register your firm "
            "first, then use this link to accept. It works once and expires in %(days)s days:\n\n%(url)s\n"
        )
        % {"company": company.name, "days": settings.FIRM_INVITATION_LINK_DAYS, "url": link("firm-invite", raw)},
    )


def accept(raw):
    """Accept the invitation behind `raw` as the signed-in firm administrator. Returns the company's id.

    Raises InvalidLink for a link that is unknown, expired, used or tampered with; the three are deliberately not
    told apart. Raises NotFirmAdministrator when the signed-in user is not a firm administrator of an approved
    firm, so the view can offer registration instead of a plain "invalid link". Any other database error (a
    deadlock, an outage) is re-raised rather than misreported as one of the two: it is not the invitation's fault.
    """
    try:
        with transaction.atomic():
            return scalar("SELECT accept_firm_invitation(%s)", [hash_token(raw)])
    except DatabaseError as error:
        sqlstate = getattr(error.__cause__, "sqlstate", None)
        if sqlstate == NOT_OWNER_OR_NOT_ADMIN:
            raise NotFirmAdministrator from error
        if sqlstate == INVALID_LINK:
            raise InvalidLink from error
        raise
