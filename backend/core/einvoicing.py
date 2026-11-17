"""The company e-invoice setting (D59 (2), (3), (5), D61 (3), D63).

The invoice engine asks one question of this module, `einvoicing_enabled(company, on_date)`. The MyInvois app reads the
supplier fields from `CompanyEInvoiceSetting`; `core` imports nothing from it.

`update_setting` is the one way to change a setting. It checks that the user is the company owner, validates the
fields, and writes the current row together with a history row (`CompanyEInvoiceSettingChange`) in one transaction.
The database holds the same rules (migration 0035), so a writer that skips this module still cannot let staff change
the setting, edit the history, or switch a mandatory company off without the acknowledgement.

TIN, BRN, ROB, the phone number and the SST and tourism-tax numbers never appear in an exception message, a log line or
an audit entry here: errors name the field, not its value. The registered name, phone, SST and tourism-tax number
(ticket #172) are part of the setting and of every history row; `Company.name` is a separate thing. A blank SST or
tourism-tax number means "none": Core never stores LHDN's sentinel `NA`, and the MyInvois mapper (#123) writes it.
"""

import re

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from . import jobs, lhdn_fields, permissions
from .db import scalar
from .models import Company, CompanyEInvoiceSetting, CompanyEInvoiceSettingChange, SupplierDetails

# Bumped whenever the wording below changes, so each recorded acknowledgement says which text the owner accepted.
ACKNOWLEDGEMENT_VERSION = "mandatory-off-v1"
ACKNOWLEDGEMENT_TEXT = gettext_lazy(
    "I understand that this company is a mandatory e-invoicing taxpayer. Switching e-invoicing off means its "
    "invoices will not be sent to LHDN MyInvois, and I am responsible for the consequences under the Income Tax Act "
    "and the e-invoice guidelines."
)

STATUSES = {SupplierDetails.MANDATORY, SupplierDetails.VOLUNTARY, SupplierDetails.EXEMPT}

SUPPLIER_FIELDS = (
    "legal_name", "phone", "sst_number", "tourism_tax_number",
    "tin", "brn", "rob", "msic_code", "address_line1", "address_line2", "city", "postal_zone", "state_code", "country_code",
)
# Needed before a company can e-invoice; a company may be saved off with these empty (D63: not yet checked). LHDN lists
# the supplier's name and telephone among the required fields (e-Invoice Guideline v4.8, Appendix 1). The SST and
# tourism-tax numbers are not here: a blank one means "none" and the mapper sends NA (#123).
REQUIRED_WHEN_ON = (
    "legal_name", "phone", "tin", "brn", "msic_code", "address_line1", "city", "postal_zone", "state_code", "country_code",
)
MAX_LENGTH = {
    "legal_name": 200, "phone": 20, "sst_number": 35, "tourism_tax_number": 17,
    "tin": 20, "brn": 20, "rob": 20, "address_line1": 150, "address_line2": 150, "city": 50,
}
PATTERNS = {
    "msic_code": re.compile(r"\d{5}"),
    "postal_zone": re.compile(r"\d{5}"),
    "state_code": re.compile(r"0[1-9]|1[0-7]"),  # LHDN state code list: 01 to 16, and 17 for not applicable
    "country_code": re.compile(r"[A-Z]{3}"),
}
# Checks that are more than a pattern. Core never stores the sentinel NA, so these refuse it (see `lhdn_fields`).
FORMATS = {"phone": lhdn_fields.phone_ok, "sst_number": lhdn_fields.sst_ok, "tourism_tax_number": lhdn_fields.ttx_ok}


class InvalidSetting(ValueError):
    """A field is missing or malformed. The message names the field and never its value."""


class AcknowledgementRequired(Exception):
    """Switching a mandatory company off needs the owner's acknowledgement (D59 (5))."""


def einvoicing_enabled(company, on_date):
    """Is e-invoicing on for `company` on `on_date`? A company with no recorded change is off (D59 (2))."""
    latest = (
        CompanyEInvoiceSettingChange.objects.filter(company=company, effective_from__lte=on_date)
        .order_by("-effective_from", "-id")
        .values_list("einvoicing_enabled", flat=True)
        .first()
    )
    return bool(latest)


def current_setting(company):
    """The company's setting row, or None when it has never been saved."""
    return CompanyEInvoiceSetting.objects.filter(company=company).first()


def is_mandatory(company):
    """Is the company's recorded status mandatory? A company with no setting is not (D59 (5))."""
    setting = current_setting(company)
    return setting is not None and setting.status == SupplierDetails.MANDATORY


def _labels():
    return {
        "status": _("status"),
        "effective_from": _("effective date"),
        "legal_name": _("registered name"),
        "phone": _("phone number"),
        "sst_number": _("SST registration number"),
        "tourism_tax_number": _("tourism tax registration number"),
        "tin": _("TIN"),
        "brn": _("BRN"),
        "rob": _("ROB"),
        "msic_code": _("MSIC code"),
        "address_line1": _("address line 1"),
        "address_line2": _("address line 2"),
        "city": _("city"),
        "postal_zone": _("postcode"),
        "state_code": _("state code"),
        "country_code": _("country code"),
    }


def _validate(enabled, status, effective_from, supplier):
    labels = _labels()
    if status is not None and status not in STATUSES:
        raise InvalidSetting(_("Check the %(field)s.") % {"field": labels["status"]})
    if effective_from is None:
        raise InvalidSetting(_("Check the %(field)s.") % {"field": labels["effective_from"]})
    for name in SUPPLIER_FIELDS:
        value = supplier[name]
        if enabled and name in REQUIRED_WHEN_ON and not value:
            raise InvalidSetting(_("Enter the %(field)s.") % {"field": labels[name]})
        if _unacceptable(name, value):
            raise InvalidSetting(_("Check the %(field)s.") % {"field": labels[name]})


def _unacceptable(name, value):
    """Is a given supplier value too long or malformed? An empty value is judged by `REQUIRED_WHEN_ON`, not here."""
    too_long = name in MAX_LENGTH and len(value) > MAX_LENGTH[name]
    malformed = value and (
        (name in PATTERNS and not PATTERNS[name].fullmatch(value)) or (name in FORMATS and not FORMATS[name](value))
    )
    return bool(too_long or malformed)


def supplier_gaps(company):
    """The labels of what stops `company` issuing an e-invoice: a missing or invalid supplier field, never a value.

    A setting saved before #172 can still hold a blank registered name or phone, because the rule is checked only at
    the next save. Approval (#46) refuses such a supplier rather than snapshot blanks.
    """
    setting = current_setting(company)
    labels = _labels()
    if setting is None:
        return [labels[name] for name in SUPPLIER_FIELDS if name in REQUIRED_WHEN_ON]
    gaps = []
    for name in SUPPLIER_FIELDS:
        value = (getattr(setting, name) or "").strip()
        if (name in REQUIRED_WHEN_ON and not value) or _unacceptable(name, value):
            gaps.append(labels[name])
    return gaps


def companies_with_supplier_gaps():
    """For an operator: each company with e-invoicing on and what is missing, as `(company, labels)`, by company id.

    The setting table is under forced row-level security and the table owner has no policy on it, so each company is
    read as the worker role in that company's context (`jobs.as_worker_in`): the database shows one company at a time,
    and only `Company` is read across all of them. Only labels leave this function, never a value.
    """
    listed = []
    for company in Company.objects.order_by("pk"):
        with jobs.as_worker_in(company):
            setting = current_setting(company)
            if setting is not None and setting.einvoicing_enabled and (gaps := supplier_gaps(company)):
                listed.append((company, gaps))
    return listed


def acknowledgement_required(previous, enabled, status):
    """Does this change switch a mandatory company off? `previous` is the latest history row or None.

    Mandatory counts when the status after the change, or the status just before it, is mandatory. A status that is
    None ("not yet checked") is not mandatory, so switching such a company off needs no acknowledgement.
    """
    if previous is None or not previous.einvoicing_enabled or enabled:
        return False
    return SupplierDetails.MANDATORY in (status, previous.status)


def update_setting(
    user,
    company,
    *,
    einvoicing_enabled,
    status,
    effective_from,
    legal_name="",
    phone="",
    sst_number="",
    tourism_tax_number="",
    tin="",
    brn="",
    rob="",
    msic_code="",
    address_line1="",
    address_line2="",
    city="",
    postal_zone="",
    state_code="",
    country_code="MYS",
    acknowledged=False,
):
    """Save the company's setting as `user`, who must be its owner, and keep the change in the history.

    Raises `PermissionDenied` for anyone else, `InvalidSetting` for a bad field or a date before an earlier change, and
    `AcknowledgementRequired` when a mandatory company is switched off without `acknowledged=True`.
    """
    permissions.require(user, "manage_einvoicing", company)
    supplier = dict(
        legal_name=legal_name, phone=phone, sst_number=sst_number, tourism_tax_number=tourism_tax_number,
        tin=tin, brn=brn, rob=rob, msic_code=msic_code, address_line1=address_line1, address_line2=address_line2,
        city=city, postal_zone=postal_zone, state_code=state_code, country_code=country_code,
    )
    _validate(einvoicing_enabled, status, effective_from, supplier)

    with transaction.atomic():
        # Saves of one company take turns, including the very first save when there is no setting row to lock yet.
        # The lock is the one the history trigger takes, so it is held through the whole transaction.
        scalar(
            "SELECT pg_advisory_xact_lock(hashtext('core_companyeinvoicesettingchange'), (%s::bigint %% 2147483647)::int)",
            [company.pk],
        )
        # The history row goes first: the database accepts a setting only when it equals the latest history row.
        setting =CompanyEInvoiceSetting.objects.select_for_update().filter(company=company).first()
        previous = (
            CompanyEInvoiceSettingChange.objects.filter(company=company).order_by("-effective_from", "-id").first()
        )
        if previous is not None and effective_from < previous.effective_from:
            raise InvalidSetting(_("The change cannot take effect before an earlier change."))
        needs_acknowledgement = acknowledgement_required(previous, einvoicing_enabled, status)
        if needs_acknowledgement and not acknowledged:
            raise AcknowledgementRequired

        CompanyEInvoiceSettingChange.objects.create(
            company=company,
            changed_by=user,
            einvoicing_enabled=einvoicing_enabled,
            status=status,
            effective_from=effective_from,
            acknowledged_by=user if needs_acknowledgement else None,
            acknowledged_at=timezone.now() if needs_acknowledgement else None,
            acknowledgement_version=ACKNOWLEDGEMENT_VERSION if needs_acknowledgement else "",
            **supplier,
        )
        values = {"einvoicing_enabled": einvoicing_enabled, "status": status, "effective_from": effective_from, **supplier}
        if setting is None:
            return CompanyEInvoiceSetting.objects.create(company=company, **values)
        for name, value in values.items():
            setattr(setting, name, value)
        setting.save()
        return setting
