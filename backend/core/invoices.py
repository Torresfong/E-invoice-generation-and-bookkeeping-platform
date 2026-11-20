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
from collections import Counter, namedtuple
from decimal import ROUND_HALF_UP, Decimal, localcontext

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import audit, customers, einvoicing, items, jobs, numbering, permissions
from .einvoicing import einvoicing_enabled
from .models import AuditEntry, Customer, Invoice, InvoiceLine, Item

# Enqueued by name only: `core` never imports `myinvois` (D61 (4)). The MyInvois app registers the matching
# handler under this same literal string from `myinvois/jobs.py`.
SUBMIT_INVOICE_JOB = "myinvois_submit_invoice"

# The column sizes of InvoiceLine: NUMERIC(18, 4), so at most 14 digits before the point and 4 after it.
# ASCII digits only: `\d` alone would also accept the digits of other scripts.
AMOUNT = re.compile(r"[0-9]{1,14}(\.[0-9]{1,4})?")

CENTS = Decimal("0.01")
# A tax rate is a percentage from 0 to 100: both the validation ceiling and the divisor that turns it into a
# fraction of the subtotal.
PERCENT = Decimal("100")

# `InvoiceLine.quantity` and `.unit_price` are each NUMERIC(18,4); their exact product, and the tax on it, can need
# more significant digits than Python's default 28-digit context before rounding to the sen (same reasoning as
# `myinvois.document`'s own `LINE_AMOUNT_PRECISION`, for the same column sizes).
LINE_AMOUNT_PRECISION = 50

__all__ = [
    "InvalidInvoice", "InvoiceLocked", "approve_invoice", "cancel_invoice", "create_draft", "delete_draft",
    "invoice_totals", "line_totals", "list_invoices", "update_draft",
]


class InvalidInvoice(ValueError):
    """A field of the draft is missing or not acceptable. The message names the field or line and never a value."""


class InvoiceLocked(PermissionError):
    """The invoice has left the draft state and cannot be changed or deleted (D17, D19)."""


def list_invoices(company):
    """The company's invoices, newest first, with their customers. Row-level security already hides every other company's."""
    return Invoice.objects.filter(company=company).select_related("customer")


def line_totals(line):
    """The subtotal, tax and total of one invoice line, calculated fresh from its stored quantity, price and rate.

    `InvoiceLine` stores none of these itself (D59 (9), ticket #52): quantity times unit price is the subtotal, and
    the rate applies to that, both rounded to the sen under ROUND_HALF_UP (1 x 0.005 rounds up to 0.01, matching
    `myinvois.document`'s own rounding rule for the same column sizes).
    """
    with localcontext() as ctx:
        ctx.prec = LINE_AMOUNT_PRECISION
        subtotal = (line.quantity * line.unit_price).quantize(CENTS, rounding=ROUND_HALF_UP)
        tax_amount = (subtotal * line.tax_rate / PERCENT).quantize(CENTS, rounding=ROUND_HALF_UP)
    return {"subtotal": subtotal, "tax_amount": tax_amount, "total": subtotal + tax_amount}


def invoice_totals(invoice):
    """The invoice's totals: the sum of every line's subtotal, tax and total."""
    amounts = [line_totals(line) for line in invoice.lines.all()]
    with localcontext() as ctx:
        ctx.prec = LINE_AMOUNT_PRECISION
        total_excluding_tax = sum((amount["subtotal"] for amount in amounts), Decimal("0.00"))
        total_tax = sum((amount["tax_amount"] for amount in amounts), Decimal("0.00"))
    return {
        "total_excluding_tax": total_excluding_tax,
        "total_tax": total_tax,
        "total_including_tax": total_excluding_tax + total_tax,
    }


def _pk(value):
    try:
        found = int(value)
    except (TypeError, ValueError):
        return None
    return found if found > 0 else None


def _amount(raw, label, line_number, *, allow_zero, maximum=None):
    if isinstance(raw, float):
        raise TypeError("an amount is a Decimal or a string, never a float")
    text = format(raw, "f") if isinstance(raw, Decimal) else str(raw).strip()
    if not AMOUNT.fullmatch(text) or (not allow_zero and Decimal(text) == 0):
        raise InvalidInvoice(_("Check the %(field)s on line %(line)s.") % {"field": label, "line": line_number})
    value = Decimal(text)
    if maximum is not None and value > maximum:
        raise InvalidInvoice(_("Check the %(field)s on line %(line)s.") % {"field": label, "line": line_number})
    return value


def _tax_type(raw, line_number):
    """One of LHDN's tax-type codes, chosen by the owner. Never read from the item's `default_tax_type`: an
    uncertain or missing tax type is refused rather than guessed (the brief's "the system never decides")."""
    value = (raw or "").strip()
    if value not in items.tax_types():
        raise InvalidInvoice(_("Choose a tax type on line %(line)s.") % {"line": line_number})
    return value


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


ResolvedLine = namedtuple("ResolvedLine", ["item", "quantity", "unit_price", "tax_type", "tax_rate"])


def _resolve(company, customer_id, lines, *, current=None):
    """The customer and a `ResolvedLine` for each line, or `InvalidInvoice` naming what is wrong."""
    customer = _customer(company, customer_id, current=current.customer if current else None)
    if not lines:
        raise InvalidInvoice(_("Add at least one line."))
    kept = Counter(current.lines.values_list("item_id", flat=True)) if current else Counter()
    resolved = []
    for number, line in enumerate(lines, start=1):
        item = _item(company, line.get("item"), number, kept=kept)
        quantity = _amount(line.get("quantity", ""), _("quantity"), number, allow_zero=False)
        price = _amount(line.get("unit_price", ""), _("unit price"), number, allow_zero=True)
        tax_type = _tax_type(line.get("tax_type"), number)
        tax_rate = _amount(line.get("tax_rate", ""), _("tax rate"), number, allow_zero=True, maximum=PERCENT)
        resolved.append(ResolvedLine(item, quantity, price, tax_type, tax_rate))
    return customer, resolved


def _write_lines(invoice, resolved):
    InvoiceLine.objects.bulk_create(
        InvoiceLine(
            company_id=invoice.company_id, invoice=invoice, item=line.item, quantity=line.quantity,
            unit_price=line.unit_price, tax_type=line.tax_type, tax_rate=line.tax_rate,
        )
        for line in resolved
    )


def create_draft(user, company, *, customer_id, lines):
    """Save a new draft for `company` as `user`, who must be its owner or staff; it has no number.

    `lines` is a list of dicts with `item` (an item id), `quantity`, `unit_price`, `tax_type` (one of
    `core.lhdn_codes.tax_types()`) and `tax_rate` (a percentage from 0 to 100), each as a string or a Decimal except
    `tax_type`. Raises `PermissionDenied` for anyone else and `InvalidInvoice` for a missing customer, no lines, an
    item or customer of another company or a deactivated one, a quantity that is not above zero, a price that is
    negative or too long, a tax type that is not on LHDN's list, or a tax rate outside 0 to 100.
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
            if current.is_einvoice:
                # D60 (2): a stale or otherwise permanently failed job stops and tells the owner, who approves
                # again; that one action both renews the issue time above and queues a fresh attempt, so the
                # invoice is not stranded (a Torres review finding on PR #194). This module still does not know
                # whether an earlier attempt already succeeded (#48's status display); the job guards itself
                # against resubmitting an invoice it already has a recorded outcome for.
                jobs.enqueue(SUBMIT_INVOICE_JOB, company=current.company, invoice_id=current.pk)
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
        if is_einvoice:
            # One owner action, "Approve & submit" (D44): a normal invoice finishes here with no submission (D59
            # (3)). Re-approval (the branch above) is the D60 (2) path for a stuck or stale submission and queues
            # its own replacement job; this first approval always starts the first one.
            jobs.enqueue(SUBMIT_INVOICE_JOB, company=company, invoice_id=current.pk)
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
