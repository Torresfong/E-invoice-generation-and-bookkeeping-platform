"""The customer master: the buyers a company invoices (D46, D59 (7), (10); ticket #7).

Staff and the owner create customers; only the owner changes the legal name, TIN, ID type or ID value. A TIN that is
given is checked through the MyInvois gateway before it is saved (D46, D58 (2)): "bad format" and "not found" are
refused with different messages. A company with e-invoicing on may not save a customer without a TIN, an ID type and an
ID value (D59 (7)); with it off they may be empty. A customer is deactivated, never deleted, and every change is kept
with its before and after values in `CustomerChange`. The database holds the same rules (migration 0038), so a writer
that skips this module still cannot let staff change the identity fields, delete a customer or leave a change
unrecorded.

A customer also keeps what LHDN's buyer table needs besides the identity (ticket #172): a phone number, an SST
registration number and an address. Staff and the owner both edit those. They are checked for format whenever they are
given, but never required here: a customer saved while e-invoicing was off, or before these fields existed, has them
blank. `einvoice_gaps` tells a draft which fields still stand in the way once e-invoicing is on. Core never stores the
sentinel `NA`; a blank SST number is mapped to it by the MyInvois mapper (#123).

TIN, ID values, phone and SST numbers never appear in an exception message: errors name the field, not its value.
"""

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import lhdn_fields, permissions, ports
from .einvoicing import einvoicing_enabled
from .jobs import TransientError
from .models import Customer, CustomerChange
from .ports.interfaces import ID_TYPES, TinCheck

MAX_NAME_LENGTH = Customer._meta.get_field("legal_name").max_length
MAX_TIN_LENGTH = Customer._meta.get_field("tin").max_length
MAX_ID_VALUE_LENGTH = Customer._meta.get_field("id_value").max_length
IDENTITY_FIELDS = ("legal_name", "tin", "id_type", "id_value")
CONTACT_FIELDS = (
    "phone", "sst_number", "address_line1", "address_line2", "city", "postal_zone", "state_code", "country_code",
)
EDITABLE_FIELDS = IDENTITY_FIELDS + CONTACT_FIELDS
MAX_LENGTH = {name: Customer._meta.get_field(name).max_length for name in EDITABLE_FIELDS}
# What LHDN's buyer table requires of every buyer. The SST number, address line 2 and the postcode are optional there.
REQUIRED_CONTACT_FIELDS = ("phone", "address_line1", "city", "state_code", "country_code")

__all__ = [
    "CONTACT_FIELDS", "CustomerNotAvailable", "EDITABLE_FIELDS", "ID_TYPES", "IDENTITY_FIELDS", "InvalidCustomer",
    "create_customer", "customer_for_new_draft", "deactivate_customer", "einvoice_gaps", "history", "list_customers",
    "reactivate_customer", "update_customer",
]


class InvalidCustomer(ValueError):
    """A field is missing, malformed or refused by MyInvois. The message names the field and never its value."""


class CustomerNotAvailable(LookupError):
    """The customer does not exist for this company, or it has been deactivated and cannot go on a new draft."""


def _labels():
    return {
        "tin": _("TIN"),
        "id_type": _("ID type"),
        "id_value": _("ID value"),
        "phone": _("phone number"),
        "sst_number": _("SST registration number"),
        "address_line1": _("address line 1"),
        "address_line2": _("address line 2"),
        "city": _("city"),
        "postal_zone": _("postcode"),
        "state_code": _("state code"),
        "country_code": _("country code"),
    }


def _invalid_contact(fields):
    """The contact fields that hold a value LHDN would refuse, in field order. A blank value is not judged here."""
    country = fields["country_code"]
    formats = {
        "phone": lhdn_fields.phone_ok,
        "sst_number": lhdn_fields.sst_ok,
        "postal_zone": lambda value: lhdn_fields.postal_ok(value, country),
        "state_code": lambda value: lhdn_fields.state_ok(value, country),
        "country_code": lhdn_fields.country_ok,
    }
    invalid = []
    for field in CONTACT_FIELDS:
        value = fields[field]
        if value and (len(value) > MAX_LENGTH[field] or (field in formats and not formats[field](value))):
            invalid.append(field)
    return invalid


def _check_contact(fields):
    """Refuse the first contact value that LHDN would not accept. The message names the field, never the value."""
    for field in _invalid_contact(fields):
        raise InvalidCustomer(_("Check the %(field)s.") % {"field": _labels()[field]})


def einvoice_gaps(customer):
    """The labels of what stops `customer` going on an e-invoice: a missing identity or buyer field, or an invalid one.

    Empty when the customer is complete. A customer saved while e-invoicing was off, or before the phone and address
    existed, is incomplete here although the customer master accepted it. Names only; never a value.
    """
    fields = {name: getattr(customer, name) for name in EDITABLE_FIELDS}
    # Stripped first: a row written directly can hold " ", and LHDN's rules (#122) treat that as blank.
    wrong = {name for name in ("tin", "id_type", "id_value", *REQUIRED_CONTACT_FIELDS) if not fields[name].strip()}
    wrong.update(_invalid_contact(fields))
    labels = _labels()
    return [labels[name] for name in EDITABLE_FIELDS if name in wrong]


def _check_identity(fields, *, einvoicing_on):
    """Refuse a bad name, a half-filled ID, a missing TIN or ID when e-invoicing is on, and a TIN LHDN does not accept."""
    name, tin, id_type, id_value = (fields[f] for f in ("legal_name", "tin", "id_type", "id_value"))
    if not name.strip() or len(name) > MAX_NAME_LENGTH:
        raise InvalidCustomer(_("Check the legal name."))
    if len(tin) > MAX_TIN_LENGTH:
        raise InvalidCustomer(_("Check the TIN."))
    if id_type and id_type not in ID_TYPES:
        raise InvalidCustomer(_("Check the ID type."))
    if id_value and not id_type:
        raise InvalidCustomer(_("Choose the ID type."))
    if len(id_value) > MAX_ID_VALUE_LENGTH or (id_type and not id_value):
        raise InvalidCustomer(_("Check the ID value."))
    if einvoicing_on:
        for field, label in (("tin", _("TIN")), ("id_type", _("ID type")), ("id_value", _("ID value"))):
            if not fields[field]:
                raise InvalidCustomer(_("A customer needs a %(field)s while e-invoicing is on.") % {"field": label})
    if not tin:
        return
    if not id_type:
        raise InvalidCustomer(_("A TIN is checked together with an ID type and an ID value."))
    try:
        outcome = ports.get().gateway.validate_tin(tin, id_type, id_value)
    except TransientError:  # a network failure, a 429 or a 5xx (D57 (4)): not an answer about this TIN, so nothing is saved
        raise InvalidCustomer(
            _("MyInvois cannot be reached right now, so the TIN could not be checked. Nothing was saved. Try again in a few minutes.")
        ) from None
    if outcome is TinCheck.BAD_FORMAT:
        raise InvalidCustomer(_("MyInvois does not accept the format of this TIN."))
    if outcome is TinCheck.NOT_FOUND:
        raise InvalidCustomer(_("MyInvois has no taxpayer with this TIN and ID."))


def list_customers(company, *, active_only=False):
    """The company's customers by legal name. Row-level security already hides every other company's."""
    found = Customer.objects.filter(company=company)
    return found.filter(is_active=True) if active_only else found


def create_customer(user, company, *, legal_name, tin="", id_type="", id_value="", **contact):
    """Add a customer as `user`, who must be the owner or staff of `company`.

    `contact` may hold any of `CONTACT_FIELDS` (the phone, SST number and address); the rest are blank. Raises
    `PermissionDenied` for anyone else and `InvalidCustomer` for a bad field or a TIN MyInvois refuses.
    """
    unknown = set(contact) - set(CONTACT_FIELDS)
    if unknown:
        raise TypeError(f"unexpected customer fields: {', '.join(sorted(unknown))}")
    permissions.require(user, "manage_customers", company)
    fields = {"legal_name": legal_name, "tin": tin, "id_type": id_type, "id_value": id_value}
    fields.update({name: contact.get(name, "") for name in CONTACT_FIELDS})
    fields = {name: value.strip() for name, value in fields.items()}
    _check_identity(fields, einvoicing_on=einvoicing_enabled(company, timezone.localdate()))
    _check_contact(fields)
    return Customer.objects.create(company=company, **fields)


def update_customer(user, customer, **given):
    """Change the given fields of `customer` as `user`; a field left out (or None) stays as it is.

    The fields are `EDITABLE_FIELDS`. The owner and staff may change the phone, SST number and address. Only the owner
    may change a legal name, TIN or ID to a value that differs from the saved one; staff may save the form unchanged.
    The identity is checked again (TIN through the gateway) only when it differs, so LHDN losing a TIN later never
    blocks an unchanged save or a change of phone. Raises `PermissionDenied` or `InvalidCustomer`. Nothing changed adds
    no history.
    """
    unknown = set(given) - set(EDITABLE_FIELDS)
    if unknown:
        raise TypeError(f"unexpected customer fields: {', '.join(sorted(unknown))}")
    permissions.require(user, "manage_customers", customer.company)
    seen = Customer.objects.get(pk=customer.pk)
    before = {f: getattr(seen, f) for f in EDITABLE_FIELDS}
    wanted = {f: before[f] if given.get(f) is None else given[f].strip() for f in EDITABLE_FIELDS}
    if wanted != before:
        identity_changed = any(wanted[f] != before[f] for f in IDENTITY_FIELDS)
        if identity_changed:
            permissions.require(user, "change_customer_identity", seen.company)
            # The gateway is called before the row is locked, so a slow answer from LHDN holds no lock.
            _check_identity(wanted, einvoicing_on=einvoicing_enabled(seen.company, timezone.localdate()))
        if any(wanted[f] != before[f] for f in CONTACT_FIELDS):
            _check_contact(wanted)
        with transaction.atomic():
            locked = Customer.objects.select_for_update().get(pk=seen.pk)
            if {f: getattr(locked, f) for f in EDITABLE_FIELDS} != before:
                # Someone saved first: what was checked above no longer describes the row, so nothing is overwritten.
                raise InvalidCustomer(_("This customer was changed by someone else. Reload it and try again."))
            Customer.objects.filter(pk=seen.pk).update(**wanted)
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
    """Let `customer` be chosen on new drafts again."""
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
