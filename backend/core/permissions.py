"""Action-level permissions (D40 (4)).

Row-level security decides which companies a user can reach at all. This module decides what the user's
role in that company lets them do. The role comes from the `CompanyAccess` row for the company in play,
never from the user, so one login can hold different roles in different companies later.

To protect a new action, register it in ACTIONS and call `require` in the view or `can` where a choice is
shown. There is no delegation (D17): an action is allowed for a role or it is not.
"""

from django.core.exceptions import PermissionDenied

from .models import CompanyAccess

OWNER, STAFF, ACCOUNTANT = CompanyAccess.OWNER, CompanyAccess.STAFF, CompanyAccess.ACCOUNTANT

ACTIONS = {
    "view_company": {OWNER, STAFF, ACCOUNTANT},
    "invite_staff": {OWNER},
    "configure_numbering": {OWNER},  # prefix and starting number of the invoice sequence (D18)
    "view_einvoicing": {OWNER, STAFF, ACCOUNTANT},
    "manage_einvoicing": {OWNER},  # status, on/off switch and supplier details of the e-invoice setting (D59 (2))
    "view_items": {OWNER, STAFF, ACCOUNTANT},
    "manage_items": {OWNER, STAFF},  # create, rename, change the default tax type, deactivate (D59 (9))
    "change_item_codes": {OWNER},  # the LHDN classification and unit codes, as only the owner changes a customer's TIN
    "view_customers": {OWNER, STAFF, ACCOUNTANT},
    "manage_customers": {OWNER, STAFF},  # create, deactivate (D59 (10))
    "change_customer_identity": {OWNER},  # legal name, TIN, ID type and value (D46, ticket #7)
    "view_invoices": {OWNER, STAFF, ACCOUNTANT},
    "manage_invoices": {OWNER, STAFF},  # draft, edit and delete a draft invoice (D17, ticket #44)
    "view_audit_trail": {OWNER, ACCOUNTANT},
    "view_own_activity": {OWNER, STAFF, ACCOUNTANT},
}


def role_in(user, company):
    """The user's role in the company, or None when they have no membership there."""
    if not user.is_authenticated:
        return None
    return CompanyAccess.objects.filter(user=user, company=company).values_list("role", flat=True).first()


def can(user, action, company):
    return role_in(user, company) in ACTIONS[action]


def require(user, action, company):
    if not can(user, action, company):
        raise PermissionDenied


def is_firm_admin(user):
    """A signed-in, active firm administrator (D49). A firm-level role: it is on the user, not on a membership row."""
    return user.is_authenticated and user.is_active and user.is_firm_admin and user.firm_id is not None


def is_accountant(user):
    """A signed-in, active firm accountant (D49)."""
    return user.is_authenticated and user.is_active and user.is_accountant and user.firm_id is not None


def require_accountant(user):
    if not is_accountant(user):
        raise PermissionDenied


def require_firm_admin(user):
    if not is_firm_admin(user):
        raise PermissionDenied
