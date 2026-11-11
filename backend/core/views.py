import logging
from datetime import date
from itertools import zip_longest

from django.conf import settings
from django.contrib.auth import authenticate, logout, password_validation
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from . import audit, customers, einvoicing, firm_admin, firm_applications, invoices, items, permissions, registration, throttle, tokens
from .models import AuditEntry, AuthToken, CompanyAccess, Customer, FirmAuditEntry, Invoice, Item, SupplierDetails
from .signin import complete_sign_in

logger = logging.getLogger(__name__)


def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok", "database": "ok"})


def home(request):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    return render(request, "home.html")


def sign_in(request):
    if request.user.is_authenticated:
        # Sign out first to use another account: a signed-in session may only record its own sign-in events.
        return redirect("/")
    if request.method != "POST":
        return render(request, "login.html")

    email = request.POST.get("email", "").strip()
    password = request.POST.get("password", "")
    context = {"email": email}

    if not throttle.allow_attempt(email):
        context["error"] = _("Too many failed attempts. Try again in a few minutes.")
        return render(request, "login.html", context, status=429)

    user = authenticate(request, email=email, password=password)
    if user is None:
        audit.record_auth_event(AuditEntry.LOGIN_FAILED, email)
        context["error"] = _("The email address or password is not correct.")
        return render(request, "login.html", context)

    throttle.clear(email)
    complete_sign_in(request, user)
    audit.record_auth_event(AuditEntry.LOGIN)
    return redirect("/")


@require_POST
def sign_out(request):
    if request.user.is_authenticated:
        audit.record_auth_event(AuditEntry.LOGOUT)
    logout(request)
    return redirect("/login/")


def _password_errors(request):
    """What is wrong with the password and its repeat in a POST, as messages; empty when it is acceptable."""
    password = request.POST.get("password", "")
    if password != request.POST.get("confirm", ""):
        return [_("The two passwords do not match.")]
    try:
        password_validation.validate_password(password)
    except ValidationError as error:
        return list(error.messages)
    return []


def _choose_password(request, token, purpose, heading, finish):
    """Choose a password through a single-use link. `finish(token, password)` consumes the link."""
    if not tokens.link_is_valid(token, purpose):
        return render(request, "link_invalid.html", status=410)
    if request.method != "POST":
        return render(request, "password_set.html", {"heading": heading})

    password = request.POST.get("password", "")
    errors = _password_errors(request)
    if errors:
        return render(request, "password_set.html", {"heading": heading, "errors": errors})

    try:
        finish(token, password)
    except tokens.InvalidLink:
        return render(request, "link_invalid.html", status=410)
    return redirect("/login/")


def password_reset_request(request):
    if request.method != "POST":
        return render(request, "password_reset.html")
    email = request.POST.get("email", "").strip()
    if email:
        tokens.request_password_reset(email)
    return render(request, "password_reset.html", {"sent": True})


def password_reset_confirm(request, token):
    return _choose_password(
        request, token, AuthToken.RESET, _("Choose a new password"), tokens.complete_password_reset
    )


def _own_access(request, pk):
    """The signed-in user's membership in company `pk`. A company they cannot reach is not found, so its existence is not revealed."""
    return get_object_or_404(CompanyAccess.objects.select_related("company"), user=request.user, company_id=pk)


def companies(request):
    """The companies the signed-in user can reach. Row-level security does the filtering; the role is shown for context."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    accesses = CompanyAccess.objects.filter(user=request.user).select_related("company").order_by("company__name")
    return render(request, "companies.html", {"accesses": accesses})


def _company_page(request, access, **extra):
    context = {"company": access.company, "access": access, **extra}
    if request.GET.get("invited"):
        context["message"] = _("Invitation sent.")
    return render(request, "company.html", context)


@audit.audited_read("company", id_arg="pk")
def company(request, pk):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_company", access.company)
    return _company_page(request, access)


@require_POST
def invite_staff(request, pk):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "invite_staff", access.company)

    email = request.POST.get("email", "").strip()
    try:
        validate_email(email)
    except ValidationError:
        return _company_page(request, access, error=_("Enter a valid email address."))
    try:
        # Answers "sent" even when the address already has an account and nothing was sent.
        tokens.invite_staff(access.company, email)
    except tokens.TooManyInvitations:
        return _company_page(request, access, error=_("Too many invitations today. Try again tomorrow."))
    return redirect(f"/companies/{pk}/?invited=1")


def _einvoice_page(request, access, form=None, **extra):
    setting = einvoicing.current_setting(access.company)
    context = {
        "company": access.company,
        "access": access,
        "setting": setting,
        "form": form if form is not None else setting,
        "statuses": SupplierDetails.STATUS_CHOICES,
        "acknowledgement_text": einvoicing.ACKNOWLEDGEMENT_TEXT,
        **extra,
    }
    if request.GET.get("saved"):
        context["message"] = _("E-invoice setting saved.")
    return render(request, "einvoice_setting.html", context)


def _submitted_setting(post):
    """The owner's form as `update_setting` arguments. A blank status means "not yet checked"."""
    try:
        effective_from = date.fromisoformat(post.get("effective_from", "").strip())
    except ValueError:
        effective_from = None
    values = {name: post.get(name, "").strip() for name in einvoicing.SUPPLIER_FIELDS}
    values["country_code"] = values["country_code"].upper()
    return {
        "einvoicing_enabled": "einvoicing_enabled" in post,
        "status": post.get("status", "").strip() or None,
        "effective_from": effective_from,
        "acknowledged": "acknowledged" in post,
        **values,
    }


@audit.audited_read("einvoice_setting")
def einvoice_setting(request, pk):
    """The company's e-invoice setting: every member reads it, only the owner changes it (D59 (2))."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_einvoicing", access.company)
    if request.method == "POST":
        permissions.require(request.user, "manage_einvoicing", access.company)
        submitted = _submitted_setting(request.POST)
        try:
            einvoicing.update_setting(request.user, access.company, **submitted)
        except einvoicing.InvalidSetting as problem:
            return _einvoice_page(request, access, submitted, error=str(problem))
        except einvoicing.AcknowledgementRequired:
            return _einvoice_page(request, access, submitted, needs_acknowledgement=True)
        return redirect(f"/companies/{pk}/einvoicing/?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    return _einvoice_page(request, access)


def _items_url(company_id, *rest):
    return "/".join([f"/companies/{company_id}/items", *map(str, rest), ""])


def _item_context(access, **extra):
    return {
        "company": access.company,
        "access": access,
        "classification_codes": sorted(items.classification_codes().items()),
        "unit_codes": sorted(items.unit_codes().items()),
        "tax_types": sorted(items.tax_types().items()),
        **extra,
    }


def _item_fields(post):
    """The item form as `items` arguments. Fields that are not posted stay None, meaning unchanged.

    Staff are not shown the code fields, so their form never posts them; a hand-made request that does is judged by
    `items.update_item`, which refuses a code that differs from the saved one.
    """
    names = ("name", "default_tax_type", *items.LHDN_CODE_FIELDS)
    return {name: post[name].strip() for name in names if name in post}


@audit.audited_read("items")
def item_list(request, pk):
    """The company's items: everyone with access reads them, the owner and staff add them (D59 (9))."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_items", access.company)
    page = {"items": items.list_items(access.company), "form": {}}
    if request.method == "POST":
        permissions.require(request.user, "manage_items", access.company)
        submitted = _item_fields(request.POST)
        try:
            items.create_item(
                request.user,
                access.company,
                **{name: submitted.get(name, "") for name in ("name", "classification_code", "unit_code", "default_tax_type")},
            )
        except items.InvalidItem as problem:
            return render(request, "items.html", _item_context(access, **{**page, "form": submitted, "error": str(problem)}))
        return redirect(_items_url(pk) + "?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    if request.GET.get("saved"):
        page["message"] = _("Item saved.")
    return render(request, "items.html", _item_context(access, **page))


@audit.audited_read("item", id_arg="item_pk")
def item_detail(request, pk, item_pk):
    """One item: change its name and default tax type; the owner also changes its LHDN codes."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_items", access.company)
    item = get_object_or_404(Item, pk=item_pk, company_id=pk)
    page = {
        "item": item,
        "form": item,
        "history": items.history(item),
        # Codes LHDN has since retired stay selectable, so saving the form never silently re-codes the item.
        "kept_classification": item.classification_code if item.classification_code not in items.classification_codes() else "",
        "kept_tax_type": item.default_tax_type if item.default_tax_type not in items.tax_types() else "",
    }
    if request.method == "POST":
        permissions.require(request.user, "manage_items", access.company)
        submitted = _item_fields(request.POST)
        try:
            items.update_item(request.user, item, **submitted)
        except items.InvalidItem as problem:
            page.update(form=submitted, error=str(problem))
            return render(request, "item.html", _item_context(access, **page))
        return redirect(_items_url(pk, item_pk) + "?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    if request.GET.get("saved"):
        page["message"] = _("Item saved.")
    return render(request, "item.html", _item_context(access, **page))


def _set_item_active(request, pk, item_pk, active):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "manage_items", access.company)
    item = get_object_or_404(Item, pk=item_pk, company_id=pk)
    (items.reactivate_item if active else items.deactivate_item)(request.user, item)
    return redirect(_items_url(pk))


@require_POST
def item_deactivate(request, pk, item_pk):
    return _set_item_active(request, pk, item_pk, False)


@require_POST
def item_activate(request, pk, item_pk):
    return _set_item_active(request, pk, item_pk, True)


def _invoices_url(company_id, *rest):
    return "/".join([f"/companies/{company_id}/invoices", *map(str, rest), ""])


def _invoice_fields(post):
    """The draft form as `invoices` arguments: the customer and one dict per line that has anything typed in it."""
    rows = [
        {"item": item.strip(), "quantity": quantity.strip(), "unit_price": price.strip()}
        for item, quantity, price in zip_longest(
            post.getlist("item"), post.getlist("quantity"), post.getlist("unit_price"), fillvalue=""
        )
    ]
    return {"customer_id": post.get("customer", "").strip(), "lines": [row for row in rows if any(row.values())]}


def _invoice_context(access, invoice=None, **extra):
    """The draft form: the customers and items that may be chosen, and the typed (or saved) lines plus blank rows."""
    company = access.company
    kept_items = set(invoice.lines.values_list("item_id", flat=True)) if invoice else set()
    chosen = invoice.customer_id if invoice else None
    form = extra.pop("form", None)
    if form is None:
        form = {
            "customer_id": chosen or "",
            "lines": [
                {"item": line.item_id, "quantity": line.quantity, "unit_price": line.unit_price}
                for line in invoice.lines.all()
            ]
            if invoice
            else [],
        }
    blanks = max(2, 3 - len(form["lines"]))
    return {
        "company": company,
        "access": access,
        "invoice": invoice,
        "customers": [c for c in customers.list_customers(company) if c.is_active or c.pk == chosen],
        "items": [i for i in items.list_items(company) if i.is_active or i.pk in kept_items],
        "form": form,
        "rows": [*form["lines"], *({} for _ in range(blanks))],
        **extra,
    }


@audit.audited_read("invoices")
def invoice_list(request, pk):
    """The company's invoices; for now every one is a draft (ticket #44)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_invoices", access.company)
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    page = {"company": access.company, "access": access, "invoices": invoices.list_invoices(access.company)}
    if request.GET.get("deleted"):
        page["message"] = _("Draft deleted.")
    return render(request, "invoices.html", page)


def invoice_new(request, pk):
    """Start a draft: the owner and staff pick a customer and add lines from the item master."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "manage_invoices", access.company)
    if request.method == "POST":
        submitted = _invoice_fields(request.POST)
        try:
            invoice = invoices.create_draft(request.user, access.company, **submitted)
        except invoices.InvalidInvoice as problem:
            context = _invoice_context(access, form=_form_of(submitted), error=str(problem))
            return render(request, "invoice.html", context)
        return redirect(_invoices_url(pk, invoice.pk) + "?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    return render(request, "invoice.html", _invoice_context(access))


def _form_of(submitted):
    return {"customer_id": submitted["customer_id"], "lines": submitted["lines"]}


@audit.audited_read("invoice", id_arg="invoice_pk")
def invoice_detail(request, pk, invoice_pk):
    """One draft: the owner and staff change its customer and lines; an accountant reads it."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_invoices", access.company)
    invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=invoice_pk, company_id=pk)
    if request.method == "POST":
        permissions.require(request.user, "manage_invoices", access.company)
        submitted = _invoice_fields(request.POST)
        try:
            invoices.update_draft(request.user, invoice, **submitted)
        except invoices.InvalidInvoice as problem:
            context = _invoice_context(access, invoice, form=_form_of(submitted), error=str(problem))
            return render(request, "invoice.html", context)
        except invoices.InvoiceLocked:
            raise PermissionDenied from None
        return redirect(_invoices_url(pk, invoice_pk) + "?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    context = _invoice_context(access, invoice)
    if request.GET.get("saved"):
        context["message"] = _("Draft saved.")
    return render(request, "invoice.html", context)


@require_POST
def invoice_delete(request, pk, invoice_pk):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "manage_invoices", access.company)
    invoice = get_object_or_404(Invoice, pk=invoice_pk, company_id=pk)
    try:
        invoices.delete_draft(request.user, invoice)
    except invoices.InvoiceLocked:
        raise PermissionDenied from None
    return redirect(_invoices_url(pk) + "?deleted=1")


def _customers_url(company_id, *rest):
    return "/".join([f"/companies/{company_id}/customers", *map(str, rest), ""])


def _customer_context(access, **extra):
    return {"company": access.company, "access": access, "id_types": customers.ID_TYPES, **extra}


def _customer_fields(post):
    """The customer form as `customers` arguments. Fields that are not posted stay out, meaning unchanged.

    Staff are not shown the identity fields, so their form never posts them; a hand-made request that does is judged by
    `customers.update_customer`, which refuses a value that differs from the saved one.
    """
    return {name: post[name].strip() for name in customers.IDENTITY_FIELDS if name in post}


@audit.audited_read("customers")
def customer_list(request, pk):
    """The company's customers: everyone with access reads them, the owner and staff add them (ticket #7)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_customers", access.company)
    page = {"customers": customers.list_customers(access.company), "form": {}}
    if request.method == "POST":
        permissions.require(request.user, "manage_customers", access.company)
        submitted = _customer_fields(request.POST)
        try:
            customers.create_customer(
                request.user, access.company, **{name: submitted.get(name, "") for name in customers.IDENTITY_FIELDS}
            )
        except customers.InvalidCustomer as problem:
            return render(
                request, "customers.html", _customer_context(access, **{**page, "form": submitted, "error": str(problem)})
            )
        return redirect(_customers_url(pk) + "?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    if request.GET.get("saved"):
        page["message"] = _("Customer saved.")
    return render(request, "customers.html", _customer_context(access, **page))


@audit.audited_read("customer", id_arg="customer_pk")
def customer_detail(request, pk, customer_pk):
    """One customer: the owner changes its legal name, TIN and ID; staff see them (D46)."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "view_customers", access.company)
    customer = get_object_or_404(Customer, pk=customer_pk, company_id=pk)
    page = {"customer": customer, "form": customer, "history": customers.history(customer)}
    if request.method == "POST":
        permissions.require(request.user, "manage_customers", access.company)
        submitted = _customer_fields(request.POST)
        try:
            customers.update_customer(request.user, customer, **submitted)
        except customers.InvalidCustomer as problem:
            page.update(form=submitted, error=str(problem))
            return render(request, "customer.html", _customer_context(access, **page))
        return redirect(_customers_url(pk, customer_pk) + "?saved=1")
    if request.method not in ("GET", "HEAD"):
        return HttpResponse(status=405)
    if request.GET.get("saved"):
        page["message"] = _("Customer saved.")
    return render(request, "customer.html", _customer_context(access, **page))


def _set_customer_active(request, pk, customer_pk, active):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, "manage_customers", access.company)
    customer = get_object_or_404(Customer, pk=customer_pk, company_id=pk)
    (customers.reactivate_customer if active else customers.deactivate_customer)(request.user, customer)
    return redirect(_customers_url(pk))


@require_POST
def customer_deactivate(request, pk, customer_pk):
    return _set_customer_active(request, pk, customer_pk, False)


@require_POST
def customer_activate(request, pk, customer_pk):
    return _set_customer_active(request, pk, customer_pk, True)


def accept_invitation(request, token):
    return _choose_password(
        request, token, AuthToken.INVITE, _("Choose your password"), tokens.accept_invitation
    )


ENTRIES_SHOWN = 200


def _trail_page(request, pk, action, heading, **only):
    """A page of audit entries for company `pk`. Company isolation is row-level security; `action` is the role rule."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    access = _own_access(request, pk)
    permissions.require(request.user, action, access.company)
    entries = AuditEntry.objects.filter(company=access.company, **only)[:ENTRIES_SHOWN]
    return render(request, "audit.html", {"company": access.company, "entries": entries, "heading": heading})


@audit.audited_read("audit_trail")
@require_safe
def audit_trail(request, pk):
    """The company's full trail: the owner and the firm's accountants. Staff see their own activity instead."""
    return _trail_page(request, pk, "view_audit_trail", _("Audit trail"))


@require_safe
def my_activity(request, pk):
    """What the signed-in user themself has done in the company."""
    return _trail_page(request, pk, "view_own_activity", _("My activity"), actor_id=request.user.pk)


# --- the firm administrator's pages (D49, D50): English only, and no client data ---------------------------------


def _firm_page(request, **extra):
    context = {"firm": request.user.firm, "people": firm_admin.people(), **extra}
    if request.GET.get("added"):
        context["message"] = "Added. They have been emailed a link to choose a password."
    if request.GET.get("deactivated"):
        context["message"] = "Deactivated."
    return render(request, "firm.html", context)


def firm(request):
    """The firm administrator's page: their firm's accountants and administrators, and a form to add one."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    permissions.require_firm_admin(request.user)
    return _firm_page(request)


@require_POST
def add_firm_person(request):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    permissions.require_firm_admin(request.user)

    email, kind = request.POST.get("email", "").strip(), request.POST.get("kind", "")
    try:
        validate_email(email)
    except ValidationError:
        return _firm_page(request, error="Enter a valid email address.")
    if kind not in ("accountant", "firm_admin"):
        return _firm_page(request, error="Choose accountant or administrator.")
    # Answers "added" even when the address already has an account and nothing was created.
    firm_admin.add(email, kind)
    return redirect("/firm/?added=1")


@require_POST
def deactivate_firm_person(request, pk):
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    permissions.require_firm_admin(request.user)
    try:
        firm_admin.deactivate(pk)
    except firm_admin.PersonNotFound as error:
        raise Http404 from error
    except firm_admin.LastAdministrator:
        return _firm_page(request, error="The last active administrator of the firm cannot be deactivated.")
    return redirect("/firm/?deactivated=1")


@require_safe
def firm_audit_trail(request):
    """The firm's own trail (D50). Only its administrators read it; row-level security limits it to their firm."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    permissions.require_firm_admin(request.user)
    entries = FirmAuditEntry.objects.filter(firm=request.user.firm)[:ENTRIES_SHOWN]
    return render(request, "firm_audit.html", {"firm": request.user.firm, "entries": entries})


# --- a firm accountant's sign-up links (D49 (4)): English only ---------------------------------------------------


def signup_links(request):
    """An accountant makes a single-use sign-up link for their own firm. The link is shown once and only its hash is kept."""
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    permissions.require_accountant(request.user)
    if request.method != "POST":
        return render(request, "signup_links.html")
    return render(request, "signup_links.html", {"url": registration.create_signup_link()})


# --- a firm applies to join (D67 (1)): English only, and it creates no firm and no account -----------------------

APPLICATION_FIELDS = ("firm_name", "ssm_number", "contact_name", "email", "people")


def apply_as_firm(request):
    """The public form for a firm to apply. The operator decides later; nothing here creates a firm or a user."""
    if not settings.FIRM_APPLICATIONS_ENABLED:
        return render(request, "firm_apply.html", {"closed": True}, status=403)
    if request.method != "POST":
        return render(request, "firm_apply.html")

    form = {name: request.POST.get(name, "").strip() for name in APPLICATION_FIELDS}
    # Counted before anything else is checked, like owner sign-up, and apart from its counts.
    if not throttle.allow_signup(form["email"], request.META.get("REMOTE_ADDR", ""), kind="firm-application"):
        logger.info("firm application refused: throttled")
        return render(request, "firm_apply.html", {"form": form, "error": "Too many attempts. Try again later."}, status=429)

    problem = None
    try:
        validate_email(form["email"])
    except ValidationError:
        problem = "Enter a valid email address."
    if problem is None and not (form["firm_name"] and form["ssm_number"] and form["contact_name"]):
        problem = "Enter the firm's name, its SSM registration number and your name."
    if problem is None and (
        len(form["firm_name"]) > 200 or len(form["ssm_number"]) > 50 or len(form["contact_name"]) > 200
        or len(form["email"]) > 254 or len(form["people"]) > 2000
    ):
        problem = "One of the answers is too long."
    if problem:
        return render(request, "firm_apply.html", {"form": form, "error": problem})

    # Answers "received" even when the address or the name is taken and nothing was stored.
    firm_applications.submit(form["firm_name"], form["ssm_number"], form["contact_name"], form["email"], form["people"])
    return render(request, "firm_apply.html", {"received": True})


# --- owner self-registration (D48, D49 (4)) ---------------------------------------------------------------------


def _registration_closed(request):
    return render(request, "signup_closed.html", status=403)


def sign_up(request, token):
    """The public form behind a firm's sign-up link: an address and a company name. It creates nothing by itself."""
    if not settings.SIGNUP_ENABLED:
        return _registration_closed(request)
    if request.method == "POST":
        # Counted before anything else is checked, so guessing links is limited as well as sending mail.
        email = request.POST.get("email", "").strip()
        if not throttle.allow_signup(email, request.META.get("REMOTE_ADDR", "")):
            logger.info("signup refused: throttled")
            return render(request, "signup.html", {"error": _("Too many attempts. Try again later.")}, status=429)
    if not tokens.link_is_valid(token, AuthToken.SIGNUP):
        logger.info("signup refused: link not valid")
        return render(request, "link_invalid.html", status=410)
    if request.method != "POST":
        return render(request, "signup.html")

    company_name = request.POST.get("company_name", "").strip()
    context = {"email": email, "company_name": company_name}
    try:
        validate_email(email)
        if len(email) > 254:  # the column's width
            raise ValidationError("too long")
    except ValidationError:
        return render(request, "signup.html", {**context, "error": _("Enter a valid email address.")})
    if not company_name or len(company_name) > 200:
        return render(request, "signup.html", {**context, "error": _("Enter your company name (up to 200 characters).")})
    try:
        # Answers "sent" even when the address already has an account and nothing was sent.
        registration.start(token, email, company_name)
    except tokens.InvalidLink:
        logger.info("signup refused: link not valid")
        return render(request, "link_invalid.html", status=410)
    return render(request, "signup.html", {"sent": True})


def register(request, token):
    """The emailed verification link: the owner chooses a password, and only then do the company and the owner exist."""
    if not settings.SIGNUP_ENABLED:
        return _registration_closed(request)
    if not tokens.link_is_valid(token, AuthToken.REGISTER):
        return render(request, "link_invalid.html", status=410)
    heading = _("Choose your password")
    if request.method != "POST":
        return render(request, "password_set.html", {"heading": heading})

    errors = _password_errors(request)
    if errors:
        return render(request, "password_set.html", {"heading": heading, "errors": errors})
    try:
        user = registration.complete(token, request.POST["password"])
    except tokens.InvalidLink:
        return render(request, "link_invalid.html", status=410)
    complete_sign_in(request, user)
    audit.record_auth_event(AuditEntry.LOGIN)
    return redirect("/")
