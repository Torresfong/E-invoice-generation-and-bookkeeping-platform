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
from decimal import Decimal

from django.db import transaction
from django.utils.translation import gettext as _

from . import customers, items, permissions
from .models import Customer, Invoice, InvoiceLine, Item

# The column sizes of InvoiceLine: NUMERIC(18, 4), so at most 14 digits before the point and 4 after it.
# ASCII digits only: `\d` alone would also accept the digits of other scripts.
AMOUNT = re.compile(r"[0-9]{1,14}(\.[0-9]{1,4})?")

__all__ = ["InvalidInvoice", "InvoiceLocked", "create_draft", "delete_draft", "list_invoices", "update_draft"]


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
    """The customer the draft names. A customer deactivated since the draft was started may stay on it."""
    pk = _pk(customer_id)
    try:
        if current is not None and pk == current.pk:
            return Customer.objects.get(company=company, pk=pk)
        return customers.customer_for_new_draft(company, pk)
    except (Customer.DoesNotExist, customers.CustomerNotAvailable):
        raise InvalidInvoice(_("Choose a customer.")) from None


def _item(company, item_id, line_number, *, kept):
    pk = _pk(item_id)
    try:
        if pk in kept:
            return Item.objects.get(company=company, pk=pk)
        return items.item_for_new_draft(company, pk)
    except (Item.DoesNotExist, items.ItemNotAvailable):
        raise InvalidInvoice(_("Choose an item on line %(line)s.") % {"line": line_number}) from None


def _resolve(company, customer_id, lines, *, current=None):
    """The customer and the (item, quantity, unit price) of each line, or `InvalidInvoice` naming what is wrong."""
    customer = _customer(company, customer_id, current=current.customer if current else None)
    if not lines:
        raise InvalidInvoice(_("Add at least one line."))
    kept = set(current.lines.values_list("item_id", flat=True)) if current else set()
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


def delete_draft(user, invoice):
    """Delete a draft and its lines. It never had a number, so no number is lost (D44). `InvoiceLocked` after approval."""
    permissions.require(user, "manage_invoices", invoice.company)
    with transaction.atomic():
        _locked_draft(invoice).delete()
