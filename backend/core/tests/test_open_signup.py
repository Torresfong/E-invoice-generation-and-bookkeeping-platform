"""Owner sign-up with no firm link (ticket #143; D67 (2), (4), D48, D59 (5)).

Over HTTP with the email port faked and the connection switched to the production web role, so the database
functions and row-level security are the ones that run. The firm sign-up link flow stays and is tested in
`test_signup.py`.
"""

import re
from datetime import date

import pytest
from django.test import Client

from core import einvoicing
from core.models import AuditEntry, AuthToken, Company, CompanyAccess, CompanyEInvoiceSetting, CompanyEInvoiceSettingChange, FirmAuditEntry, User

from .factories import PASSWORD

PATH = "/signup/"
OWNER_PASSWORD = "a long enough passphrase"


def submit(client, email="solo@owner.example", name="Solo Sdn Bhd", einvoicing_choice="yes"):
    data = {"email": email, "company_name": name}
    if einvoicing_choice is not None:
        data["issues_einvoices"] = einvoicing_choice
    return client.post(PATH, data)


def register_path(fake_ports):
    return re.search(r"http://testserver(/register/[\w-]+/)", fake_ports.email.sent[-1]["body"]).group(1)


def verify(client, path, password=OWNER_PASSWORD):
    return client.post(path, {"password": password, "confirm": password})


def register(fake_ports, **form):
    """Run the whole open sign-up inside a production block and return the owner's signed-in client."""
    client = Client()
    submit(Client(), **form)
    verify(client, register_path(fake_ports))
    return client


def created(name="Solo Sdn Bhd", email="solo@owner.example"):
    """The company and owner, read as the table owner (outside the production connection)."""
    return Company.objects.get(name=name), User.objects.get(email=email)


def test_the_form_needs_no_link_and_creates_nothing_until_the_emailed_link_is_opened(
    db, signup_open, fake_ports, as_production_connection
):
    before = (Company.objects.count(), User.objects.count(), CompanyAccess.objects.count())

    with as_production_connection():
        form = Client().get(PATH)
        response = submit(Client())

    assert form.status_code == 200 and response.status_code == 200
    assert (Company.objects.count(), User.objects.count(), CompanyAccess.objects.count()) == before
    assert [mail["to"] for mail in fake_ports.email.sent] == ["solo@owner.example"]
    pending = AuthToken.objects.get(purpose="register")
    assert pending.firm is None and pending.company_name == "Solo Sdn Bhd"
    assert pending.token_hash not in fake_ports.email.sent[0]["body"]  # only the hash is kept


def test_the_new_company_has_no_firm_one_owner_and_no_accountant_rows(
    firms_world, signup_open, fake_ports, as_production_connection
):
    with as_production_connection():
        register(fake_ports)
    company, owner = created()

    assert company.firm is None
    rows = list(CompanyAccess.objects.filter(company=company))
    assert [(row.user, row.role) for row in rows] == [(owner, CompanyAccess.OWNER)]
    assert not owner.is_accountant and owner.firm is None


@pytest.mark.parametrize("choice, enabled", [("yes", True), ("no", False)])
def test_the_owners_choice_sets_the_einvoice_switch_with_a_history_row(
    db, signup_open, fake_ports, as_production_connection, choice, enabled
):
    with as_production_connection():
        register(fake_ports, einvoicing_choice=choice)
    company, owner = created()

    setting = CompanyEInvoiceSetting.objects.get(company=company)
    history = CompanyEInvoiceSettingChange.objects.get(company=company)
    assert setting.einvoicing_enabled is enabled and history.einvoicing_enabled is enabled
    assert history.changed_by == owner and setting.effective_from == history.effective_from
    assert einvoicing.einvoicing_enabled(company, date.today()) is enabled


def test_the_owner_can_change_the_setting_afterwards(db, signup_open, fake_ports, as_production_connection, as_user):
    with as_production_connection():
        register(fake_ports, einvoicing_choice="no")
    company, owner = created()

    with as_user(owner):
        einvoicing.update_setting(
            owner, company, einvoicing_enabled=False, status="exempt", effective_from=date.today()
        )
    assert CompanyEInvoiceSetting.objects.get(company=company).status == "exempt"


@pytest.mark.parametrize("choice", [None, "maybe", ""])
def test_the_form_asks_again_without_a_clear_choice(db, signup_open, fake_ports, as_production_connection, choice):
    with as_production_connection():
        refused = submit(Client(), einvoicing_choice=choice)

    assert refused.status_code == 200 and b'role="alert"' in refused.content
    assert fake_ports.email.sent == [] and not AuthToken.objects.exists()


@pytest.mark.parametrize("email, name", [("not-an-email", "Solo Sdn Bhd"), ("solo@owner.example", "   ")])
def test_the_form_asks_again_for_a_bad_address_or_a_blank_name(
    db, signup_open, fake_ports, as_production_connection, email, name
):
    with as_production_connection():
        refused = submit(Client(), email=email, name=name)

    assert refused.status_code == 200 and b'role="alert"' in refused.content and fake_ports.email.sent == []


def test_an_address_that_already_has_an_account_gets_the_same_answer_and_no_email(
    firms_world, signup_open, fake_ports, as_production_connection
):
    with as_production_connection():
        taken = submit(Client(), email="OWNER@gamma.example")
        fresh = submit(Client())

    assert taken.status_code == fresh.status_code == 200 and taken.content == fresh.content
    assert [mail["to"] for mail in fake_ports.email.sent] == ["solo@owner.example"]


def test_a_verification_link_works_once_and_creates_nothing_when_refused(
    db, signup_open, fake_ports, as_production_connection
):
    with as_production_connection():
        submit(Client())
        path = register_path(fake_ports)
        first = verify(Client(), path)
        second = verify(Client(), path, "another long passphrase")

    assert first.status_code == 302 and second.status_code == 410
    assert Company.objects.filter(name="Solo Sdn Bhd").count() == 1


def test_registration_is_recorded_in_the_company_trail_only(firms_world, signup_open, fake_ports, as_production_connection):
    firm_entries = FirmAuditEntry.objects.count()
    with as_production_connection():
        register(fake_ports)
    company, owner = created()

    entry = AuditEntry.objects.get(company=company, action="create", target_type="company")
    assert entry.actor_email == "solo@owner.example" and entry.changed_fields == ["name"]
    assert FirmAuditEntry.objects.count() == firm_entries  # a company with no firm has no firm trail


def test_no_accountant_of_any_firm_can_read_the_new_company_through_the_real_database_role(
    firms_world, signup_open, fake_ports, as_production_connection
):
    with as_production_connection():
        register(fake_ports)
    company, _ = created()

    for accountant in (firms_world["alpha_accountant"], firms_world["beta_accountant"]):
        with as_production_connection():
            client = Client()
            assert client.post("/login/", {"email": accountant.email, "password": PASSWORD}).status_code == 302
            detail = client.get(f"/companies/{company.pk}/")
            listing = client.get("/companies/").content.decode()
        assert detail.status_code == 404 and "Solo Sdn Bhd" not in listing


def test_a_company_with_no_firm_uses_every_client_portal_screen(firms_world, signup_open, fake_ports, as_production_connection):
    with as_production_connection():
        client = register(fake_ports)
    company, _ = created()
    with as_production_connection():
        base = f"/companies/{company.pk}"
        pages = ["/", "/companies/", f"{base}/", f"{base}/einvoicing/", f"{base}/items/", f"{base}/customers/",
                 f"{base}/invoices/", f"{base}/invoices/new/", f"{base}/audit/", f"{base}/activity/"]
        statuses = {page: client.get(page).status_code for page in pages}

    assert statuses == {page: 200 for page in pages}


def test_sign_up_is_rate_limited_per_address(db, signup_open, settings, fake_ports, as_production_connection):
    settings.SIGNUP_MAX_PER_EMAIL = 2
    with as_production_connection():
        statuses = [submit(Client()).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_with_sign_up_switched_off_the_screen_says_registration_is_closed(db, settings, fake_ports, as_production_connection):
    assert settings.SIGNUP_ENABLED is False
    with as_production_connection():
        form, response = Client().get(PATH), submit(Client())

    assert form.status_code == response.status_code == 403 and b"closed" in response.content
    assert fake_ports.email.sent == [] and not AuthToken.objects.exists()


def test_opening_the_link_while_signed_in_as_someone_else_registers_and_signs_in_the_new_owner(
    firms_world, signup_open, fake_ports, as_production_connection
):
    with as_production_connection():
        client = Client()
        assert client.post("/login/", {"email": "owner@gamma.example", "password": PASSWORD}).status_code == 302
        submit(Client())
        response = verify(client, register_path(fake_ports))
    company, owner = created()

    assert response.status_code == 302
    assert CompanyEInvoiceSetting.objects.filter(company=company).count() == 1
    with as_production_connection():
        listing = client.get("/companies/").content.decode()
    assert "Solo Sdn Bhd" in listing and "Gamma Holdings" not in listing  # the session is now the new owner's
