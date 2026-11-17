"""Drafting an invoice by form (D44, D59 (1); ticket #44).

A draft is a customer from the customer master and lines from the item master, each with a quantity and a unit price.
It has no number: the number is taken at approval, so deleting a draft cannot leave a gap (D44). The owner and staff
draft, edit and delete; nobody else may. Only a draft can be changed (D17, D19), and migration 0041 holds the same
rules in the database, so a writer that skips this module still cannot touch another company's data or an invoice
that has left the draft state.

Money is Decimal here and NUMERIC in the database, never a float. A message names the field and the line, never a
value. Anything specific to LHDN (the codes on a line, the document mapping) is not here: it reads the item.
"""

import re
from collections import Counter
from decimal import Decimal

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import audit, customers, einvoicing, items, numbering, permissions
from .einvoicing import einvoicing_enabled
from .models import AuditEntry, Customer, Invoice, InvoiceLine, Item

# The column sizes of InvoiceLine: NUMERIC(18, 4), so at most 14 digits before the point and 4 after it.
# ASCII digits only: `\d` alone would also accept the digits of other scripts.
AMOUNT = re.compile(r"[0-9]{1,14}(\.[0-9]{1,4})?")

__all__ = [
    "InvalidInvoice", "InvoiceLocked", "approve_invoice", "cancel_invoice", "create_draft", "delete_draft",
    "list_invoices", "update_draft",
]


class InvalidInvoice(ValueError):
    """A field of the draft is missing or not acceptable. The message names the field or line and never a value."""


class InvoiceLocked(PermissionError):
    """The invoice has left the draft state and cannot be changed or deleted (D17, D19)."""


def list_invoices(company):
    """The company's invoices, newest first, with their customers. Row-level security already hides every other company's."""
    return Invoice.objects.filter(company=company).select_related("customer")


def _pk(value):
    try:
        found = int(value)
    except (TypeError, ValueError):
        return None
    return found if found > 0 else None


def _amount(raw, label, line_number, *, allow_zero):
    if isinstance(raw, float):
        raise TypeError("an amount is a Decimal or a string, never a float")
    text = format(raw, "f") if isinstance(raw, Decimal) else str(raw).strip()
    if not AMOUNT.fullmatch(text) or (not allow_zero and Decimal(text) == 0):
        raise InvalidInvoice(_("Check the %(field)s on line %(line)s.") % {"field": label, "line": line_number})
    return Decimal(text)


def _customer(company, customer_id, *, current=None):
    """The customer the draft names. A customer deactivated since the draft was started may stay on it.

    With e-invoicing on the customer must have a TIN, an ID type and an ID value (D59 (7)), and the telephone and address
    LHDN needs on the buyer (#172). The customer master requires the identity only when a customer is saved and the rest
    never, so one saved while e-invoicing was off, or before those fields existed, is caught here, when the draft picks
    it. The error names every field to fix and none of their values, so approval (#46) never locks a bad buyer.
    """
    pk = _pk(customer_id)
    try:
        if current is not None and pk == current.pk:
            customer = Customer.objects.get(company=company, pk=pk)
        else:
            customer = customers.customer_for_new_draft(company, pk)
    except (Customer.DoesNotExist, customers.CustomerNotAvailable):
        raise InvalidInvoice(_("Choose a customer.")) from None
    gaps = customers.einvoice_gaps(customer) if einvoicing_enabled(company, timezone.localdate()) else []
    if gaps:
        raise InvalidInvoice(
            _("This customer needs these details while e-invoicing is on: %(fields)s. Update the customer first.")
            % {"fields": ", ".join(gaps)}
        )
    return customer


def _item(company, item_id, line_number, *, kept):
    """The item of a line. `kept` counts how often each item is already on the draft: a deactivated item may stay on
    the lines it already has, but every further line with it must pass the active check like any new line."""
    pk = _pk(item_id)
    try:
        if kept[pk] > 0:
            kept[pk] -= 1
            return Item.objects.get(company=company, pk=pk)
        return items.item_for_new_draft(company, pk)
    except (Item.DoesNotExist, items.ItemNotAvailable):
        raise InvalidInvoice(_("Choose an item on line %(line)s.") % {"line": line_number}) from None


def _resolve(company, customer_id, lines, *, current=None):
    """The customer and the (item, quantity, unit price) of each line, or `InvalidInvoice` naming what is wrong."""
    customer = _customer(company, customer_id, current=current.customer if current else None)
    if not lines:
        raise InvalidInvoice(_("Add at least one line."))
    kept = Counter(current.lines.values_list("item_id", flat=True)) if current else Counter()
    resolved = []
    for number, line in enumerate(lines, start=1):
        item = _item(company, line.get("item"), number, kept=kept)
        quantity = _amount(line.get("quantity", ""), _("quantity"), number, allow_zero=False)
        price = _amount(line.get("unit_price", ""), _("unit price"), number, allow_zero=True)
        resolved.append((item, quantity, price))
    return customer, resolved


def _write_lines(invoice, resolved):
    InvoiceLine.objects.bulk_create(
        InvoiceLine(company_id=invoice.company_id, invoice=invoice, item=item, quantity=quantity, unit_price=price)
        for item, quantity, price in resolved
    )


def create_draft(user, company, *, customer_id, lines):
    """Save a new draft for `company` as `user`, who must be its owner or staff; it has no number.

    `lines` is a list of dicts with `item` (an item id), `quantity` and `unit_price` (strings or Decimals). Raises
    `PermissionDenied` for anyone else and `InvalidInvoice` for a missing customer, no lines, an item or customer of
    another company or a deactivated one, a quantity that is not above zero, or a price that is negative or too long.
    """
    permissions.require(user, "manage_invoices", company)
    customer, resolved = _resolve(company, customer_id, lines)
    with transaction.atomic():
        invoice = Invoice.objects.create(company=company, customer=customer)
        _write_lines(invoice, resolved)
    return invoice


def _locked_draft(invoice):
    current = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if current.status != Invoice.DRAFT:
        raise InvoiceLocked
    return current


def update_draft(user, invoice, *, customer_id, lines):
    """Replace the customer and the lines of a draft. Raises `InvoiceLocked` once it is no longer a draft.

    The customer and the items already on the draft may stay on it after they were deactivated (D59 (10)); anything
    new must be active. The rest is as for `create_draft`.
    """
    permissions.require(user, "manage_invoices", invoice.company)
    with transaction.atomic():
        current = _locked_draft(invoice)
        customer, resolved = _resolve(current.company, customer_id, lines, current=current)
        current.lines.all().delete()
        current.customer = customer
        current.save(update_fields=["customer"])
        _write_lines(current, resolved)
    invoice.refresh_from_db()
    return invoice


BUYER_FIELDS = (
    "legal_name", "tin", "id_type", "id_value", "phone", "sst_number",
    "address_line1", "address_line2", "city", "postal_zone", "state_code", "country_code",
)


def _refuse_incomplete(company, customer):
    """With e-invoicing on, refuse a buyer or supplier with gaps. The draft checked the buyer only when it picked the
    customer and the supplier setting only when it was saved, so either can have become incomplete since (#172)."""
    problems = []
    buyer = customers.einvoice_gaps(customer)
    if buyer:
        problems.append(_("the customer needs: %(fields)s") % {"fields": ", ".join(buyer)})
    supplier = einvoicing.supplier_gaps(company)
    if supplier:
        problems.append(_("the e-invoice setting needs: %(fields)s") % {"fields": ", ".join(supplier)})
    if problems:
        raise InvalidInvoice(_("This invoice cannot be approved: %(problems)s.") % {"problems": "; ".join(problems)})


def approve_invoice(user, invoice, *, acknowledged=False):
    """The owner approves a draft: it takes the next number, copies the buyer, records the route and locks (D17, D19, D44).

    Everything happens in one transaction, so a failure anywhere leaves the draft as it was and uses no number.
    `acknowledged=True` is the owner's acceptance of the D59 (5) notice, needed to issue a normal invoice for a company
    of status mandatory; it is recorded on the invoice. Raises `PermissionDenied` for anyone but the owner,
    `InvalidInvoice` (naming fields, never values) for a missing line or an incomplete buyer or supplier,
    `AcknowledgementRequired`, `SequenceNotConfigured`, and `InvoiceLocked` for a cancelled invoice.

    An invoice that is already approved is approved again: its number, buyer and route stay, and only the approval
    time and approver are renewed (D60 (2)), so numbering is never called a second time. This module does not know
    whether the invoice has been submitted; the submission ticket decides when a second approval is offered.
    """
    permissions.require(user, "approve_invoices", invoice.company)
    with transaction.atomic():
        current = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if current.status == Invoice.CANCELLED:
            raise InvoiceLocked
        if current.status == Invoice.APPROVED:
            current.approved_by, current.approved_at = user, timezone.now()
            current.save(update_fields=["approved_by", "approved_at"])
            audit.record(
                AuditEntry.APPROVED, current.company, user, "invoice", current.pk, ["approved_at", "approved_by"]
            )
            invoice.refresh_from_db()
            return invoice
        if not current.lines.exists():
            raise InvalidInvoice(_("Add at least one line."))

        company, today = current.company, timezone.localdate()
        # Held until the end, so the customer cannot be edited between the check and the copy.
        customer = Customer.objects.select_for_update().get(pk=current.customer_id)
        is_einvoice = einvoicing_enabled(company, today)
        if is_einvoice:
            _refuse_incomplete(company, customer)
        elif einvoicing.is_mandatory(company):
            if not acknowledged:
                raise einvoicing.AcknowledgementRequired
            current.acknowledged_by, current.acknowledged_at = user, timezone.now()
            current.acknowledged_version = einvoicing.ACKNOWLEDGEMENT_VERSION

        for name in BUYER_FIELDS:
            setattr(current, f"buyer_{name}", getattr(customer, name))
        # Before the status changes: the lines of an approved invoice are locked by the database.
        for line in current.lines.select_related("item"):
            line.item_name = line.item.name
            line.save(update_fields=["item_name"])
        current.is_einvoice = is_einvoice
        current.number = numbering.allocate_number(company, current.document_type)
        current.status = Invoice.APPROVED
        current.approved_by, current.approved_at = user, timezone.now()
        current.save()
        audit.record(
            AuditEntry.APPROVED, company, user, "invoice", current.pk,
            ["status", "number", "is_einvoice", "approved_at", "approved_by", "buyer"],
        )
    invoice.refresh_from_db()
    return invoice


def cancel_invoice(user, invoice):
    """The owner cancels an approved invoice. It keeps its number, which is never reused, and numbering is not called (D44).

    Only the engine side: telling LHDN is the MyInvois module's job. Raises `PermissionDenied` for anyone but the owner
    and `InvoiceLocked` for an invoice that is not approved.
    """
    permissions.require(user, "approve_invoices", invoice.company)
    with transaction.atomic():
        current = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if current.status != Invoice.APPROVED:
            raise InvoiceLocked
        current.status = Invoice.CANCELLED
        current.save(update_fields=["status"])
    invoice.refresh_from_db()
    return invoice


def delete_draft(user, invoice):
    """Delete a draft and its lines. It never had a number, so no number is lost (D44). `InvoiceLocked` after approval."""
    permissions.require(user, "manage_invoices", invoice.company)
    with transaction.atomic():
        _locked_draft(invoice).delete()
