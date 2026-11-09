import logging
from datetime import date

from django.conf import settings
from django.contrib.auth import authenticate, logout, password_validation
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_safe

from . import audit, einvoicing, firm_admin, permissions, registration, throttle, tokens
from .models import AuditEntry, AuthToken, CompanyAccess, FirmAuditEntry, SupplierDetails
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
