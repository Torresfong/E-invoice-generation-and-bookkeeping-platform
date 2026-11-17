"""The operator's pre-go-live listing of companies whose e-invoice supplier details are incomplete (ticket #187; D59, D63).

Tested through the operator command, as in test_operator.py. A setting saved before #172 can hold a blank registered name
or phone, which approval (#46) refuses; the listing names those companies before the owner finds out at the first approval.
"""

from datetime import date, timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from core import einvoicing, jobs, services
from core.models import Company, CompanyAccess, CompanyEInvoiceSetting
from core.rls import IDENTITY_SETTING

from .factories import make_company, make_member
from .test_customers import switch_einvoicing_on


def blank_supplier_details(company, **blanked):
    """What a setting saved before #172 can look like: the service would not save these blanks now."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # run the deferred checks, or ALTER TABLE is refused
        cursor.execute("ALTER TABLE core_companyeinvoicesetting DISABLE TRIGGER USER")
        CompanyEInvoiceSetting.objects.filter(company=company).update(**blanked)
        cursor.execute("ALTER TABLE core_companyeinvoicesetting ENABLE TRIGGER USER")


def listing():
    out = StringIO()
    call_command("list_einvoice_supplier_gaps", stdout=out)
    return out.getvalue()


def test_a_company_with_e_invoicing_on_and_a_blank_name_and_phone_is_listed_by_label(world):
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    blank_supplier_details(world["acme"], phone="", legal_name="")

    shown = listing()

    assert f"#{world['acme'].pk}" in shown and "Acme Sdn Bhd" in shown
    assert "phone number" in shown and "registered name" in shown
    # Labels only: no value of the setting that is still stored reaches the output (the blanked ones are gone anyway).
    for stored in ("C9999999999", "200001000001", "1 Test Street", "82193", "50000", "Kuala Lumpur"):
        assert stored not in shown


def test_only_companies_with_the_switch_on_and_a_gap_are_listed(world):
    switch_einvoicing_on(world["acme_owner"], world["acme"])  # on, blanked below
    switch_einvoicing_on(world["beta_owner"], world["beta"])  # on and complete
    off_with_gaps = make_company("Off Trading")
    einvoicing.update_setting(
        make_member("owner@off.example", off_with_gaps, CompanyAccess.OWNER),
        off_with_gaps,
        einvoicing_enabled=False,
        status=None,
        effective_from=date(2020, 1, 1),
    )
    never_saved = make_company("Never Saved Bhd")
    blank_supplier_details(world["acme"], phone="")  # last: it leaves the setting out of step with its history

    shown = listing()

    assert f"#{world['acme'].pk}" in shown
    for left_out in (world["beta"], off_with_gaps, never_saved):
        assert f"#{left_out.pk}" not in shown and left_out.name not in shown


def test_a_company_switched_on_from_a_future_date_is_not_listed_until_that_date(world):
    # Approval treats the switch as on only from its effective date (D59 (2)), so the listing must agree.
    einvoicing.update_setting(
        world["acme_owner"], world["acme"], einvoicing_enabled=True, status="voluntary",
        effective_from=date.today() + timedelta(days=30), legal_name="Acme Registered Name Sdn Bhd",
        phone="+60312345678", tin="C9999999999", brn="200001000001", msic_code="82193", address_line1="1 Test Street",
        city="Kuala Lumpur", postal_zone="50000", state_code="14", country_code="MYS",
    )
    blank_supplier_details(world["acme"], phone="")

    assert "No company" in listing()


def test_it_says_so_when_no_company_has_a_gap(world):
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    assert "No company" in listing()


# --- who can run it: the listing needs the owner's connection, shown through the real database roles -----------------


def sign_in_to_the_database_as(user):
    """Give the connection `user` as the signed-in identity, the way the web middleware does."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT set_config(%s, %s, false)", [IDENTITY_SETTING, str(user.pk)])


def test_a_firm_accountant_who_can_see_the_companies_still_cannot_run_the_listing(world, as_production_connection):
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    blank_supplier_details(world["acme"], phone="")

    with as_production_connection():
        sign_in_to_the_database_as(world["accountant"])  # sees Acme and Beta, as in a request
        assert Company.objects.count() == 2
        with pytest.raises(DatabaseError, match="permission denied to set role"), transaction.atomic():
            einvoicing.companies_with_supplier_gaps()


def test_a_firm_administrator_has_no_company_to_list(firms_world, as_production_connection):
    administrator = services.create_firm_administrator("admin@alpha.example", firms_world["alpha"])

    with as_production_connection():
        sign_in_to_the_database_as(administrator)
        assert Company.objects.count() == 0
        assert einvoicing.companies_with_supplier_gaps() == []


def test_the_worker_role_in_one_companys_context_sees_no_other_companys_setting(world):
    switch_einvoicing_on(world["acme_owner"], world["acme"])
    switch_einvoicing_on(world["beta_owner"], world["beta"])

    with jobs.as_worker_in(world["acme"]):
        assert set(CompanyEInvoiceSetting.objects.values_list("company_id", flat=True)) == {world["acme"].pk}
