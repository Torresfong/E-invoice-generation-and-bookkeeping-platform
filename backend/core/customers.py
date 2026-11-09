"""The customer master: the buyers a company invoices (D46, D59 (7), (10); ticket #7).

Staff and the owner create customers; only the owner changes the legal name, TIN, ID type or ID value. A TIN that is
given is checked through the MyInvois gateway before it is saved (D46, D58 (2)): "bad format" and "not found" are
refused with different messages. A company with e-invoicing on may not save a customer without a TIN, an ID type and an
ID value (D59 (7)); with it off they may be empty. A customer is deactivated, never deleted, and every change is kept
with its before and after values in `CustomerChange`. The database holds the same rules (migration 0038), so a writer
that skips this module still cannot let staff change the identity fields, delete a customer or leave a change
unrecorded.

TIN and ID values never appear in an exception message: errors name the field, not its value.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import permissions, ports
from .einvoicing import einvoicing_enabled
from .models import Customer, CustomerChange
from .ports.interfaces import ID_TYPES, TinCheck

MAX_NAME_LENGTH = 200
MAX_TIN_LENGTH = 20
MAX_ID_VALUE_LENGTH = 20
IDENTITY_FIELDS = ("legal_name", "tin", "id_type", "id_value")

__all__ = [
    "CustomerNotAvailable", "ID_TYPES", "InvalidCustomer", "create_customer", "customer_for_new_draft",
    "deactivate_customer", "history", "list_customers", "reactivate_customer", "update_customer",
]


class InvalidCustomer(ValueError):
    """A field is missing, malformed or refused by MyInvois. The message names the field and never its value."""


class CustomerNotAvailable(LookupError):
    """The customer does not exist for this company, or it has been deactivated and cannot go on a new draft."""


def _check(fields, *, einvoicing_on):
    """Refuse a bad name, a half-filled ID, a missing TIN or ID when e-invoicing is on, and a TIN LHDN does not accept."""
    name, tin, id_type, id_value = (fields[f] for f in IDENTITY_FIELDS)
    if not name.strip() or len(name) > MAX_NAME_LENGTH:
        raise InvalidCustomer(_("Check the legal name."))
    if len(tin) > MAX_TIN_LENGTH:
        raise InvalidCustomer(_("Check the TIN."))
    if id_type and id_type not in ID_TYPES:
        raise InvalidCustomer(_("Check the ID type."))
    if len(id_value) > MAX_ID_VALUE_LENGTH or bool(id_type) != bool(id_value):
        raise InvalidCustomer(_("Check the ID value."))
    if einvoicing_on:
        for field, label in (("tin", _("TIN")), ("id_type", _("ID type")), ("id_value", _("ID value"))):
            if not fields[field]:
                raise InvalidCustomer(_("A customer needs a %(field)s while e-invoicing is on.") % {"field": label})
    if not tin:
        return
    if not id_type:
        raise InvalidCustomer(_("A TIN is checked together with an ID type and an ID value."))
    outcome = ports.get().gateway.validate_tin(tin, id_type, id_value)
    if outcome is TinCheck.BAD_FORMAT:
        raise InvalidCustomer(_("MyInvois does not accept the format of this TIN."))
    if outcome is TinCheck.NOT_FOUND:
        raise InvalidCustomer(_("MyInvois has no taxpayer with this TIN and ID."))


def list_customers(company, *, active_only=False):
    """The company's customers by legal name. Row-level security already hides every other company's."""
    found = Customer.objects.filter(company=company)
    return found.filter(is_active=True) if active_only else found


def create_customer(user, company, *, legal_name, tin="", id_type="", id_value=""):
    """Add a customer as `user`, who must be the owner or staff of `company`.

    Raises `PermissionDenied` for anyone else and `InvalidCustomer` for a bad field or a TIN MyInvois refuses.
    """
    permissions.require(user, "manage_customers", company)
    fields = {"legal_name": legal_name.strip(), "tin": tin.strip(), "id_type": id_type.strip(), "id_value": id_value.strip()}
    _check(fields, einvoicing_on=einvoicing_enabled(company, timezone.localdate()))
    return Customer.objects.create(company=company, **fields)


def update_customer(user, customer, *, legal_name=None, tin=None, id_type=None, id_value=None):
    """Change the given fields of `customer` as `user`; a field left as None stays as it is.

    Only the owner may change a field to a value that differs from the saved one; staff may save the form unchanged.
    The values are checked again (TIN through the gateway) only when one of them differs, so LHDN losing a TIN later
    never blocks an unchanged save. Raises `PermissionDenied` or `InvalidCustomer`. Nothing changed adds no history.
    """
    permissions.require(user, "manage_customers", customer.company)
    given = {"legal_name": legal_name, "tin": tin, "id_type": id_type, "id_value": id_value}
    current = Customer.objects.get(pk=customer.pk)
    wanted = {f: getattr(current, f) if given[f] is None else given[f].strip() for f in IDENTITY_FIELDS}
    if wanted != {f: getattr(current, f) for f in IDENTITY_FIELDS}:
        permissions.require(user, "change_customer_identity", current.company)
        # The gateway is called before the row is locked, so a slow answer from LHDN holds no lock.
        _check(wanted, einvoicing_on=einvoicing_enabled(current.company, timezone.localdate()))
        with transaction.atomic():
            Customer.objects.select_for_update().filter(pk=current.pk).update(**wanted)
    customer.refresh_from_db()
    return customer


def _set_active(user, customer, active):
    permissions.require(user, "manage_customers", customer.company)
    Customer.objects.filter(pk=customer.pk).update(is_active=active)
    customer.refresh_from_db()
    return customer


def deactivate_customer(user, customer):
    """Stop `customer` being chosen on new drafts. It stays on every invoice it already appears on (D59 (10))."""
    return _set_active(user, customer, False)


def reactivate_customer(user, customer):
    return _set_active(user, customer, True)


def customer_for_new_draft(company, customer_id):
    """The customer a new draft may use: it belongs to `company` and is active; otherwise `CustomerNotAvailable`."""
    try:
        return Customer.objects.get(company=company, pk=customer_id, is_active=True)
    except Customer.DoesNotExist:
        raise CustomerNotAvailable from None


def history(customer):
    """Every change to `customer`, oldest first, each with its values before and after and who made it."""
    return list(CustomerChange.objects.filter(customer=customer))
