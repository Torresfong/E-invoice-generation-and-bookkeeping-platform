import re

import pytest
from django.core.management import CommandError, call_command

from core import services
from core.models import Company, CompanyAccess, User

from .factories import PASSWORD, default_firm, make_company, make_member

NEW_PASSWORD = "a brand new passphrase"


def setup_link(fake_ports):
    body = fake_ports.email.sent[-1]["body"]
    return re.search(r"(/reset/[\w-]+/)", body).group(1)


def test_the_command_creates_an_accountant_with_access_to_every_company_of_the_firm(db, fake_ports):
    acme, beta = make_company("Acme Sdn Bhd"), make_company("Beta Trading")

    call_command("create_accountant", "Accountant@Firm.example", default_firm().name)

    accountant = User.objects.get(email="accountant@firm.example")
    assert set(CompanyAccess.objects.filter(user=accountant).values_list("company_id", "role")) == {
        (acme.pk, CompanyAccess.ACCOUNTANT),
        (beta.pk, CompanyAccess.ACCOUNTANT),
    }
    assert not accountant.has_usable_password()


def test_the_accountant_is_emailed_a_link_to_choose_a_password_and_can_then_sign_in(
    client, fake_ports, as_production_connection
):
    make_company("Acme Sdn Bhd")
    make_company("Beta Trading")
    call_command("create_accountant", "accountant@firm.example", default_firm().name)
    assert [mail["to"] for mail in fake_ports.email.sent] == ["accountant@firm.example"]

    with as_production_connection():
        link = setup_link(fake_ports)
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 302
        assert client.post("/login/", {"email": "accountant@firm.example", "password": NEW_PASSWORD}).status_code == 302
        page = client.get("/companies/").content.decode()

    assert "Acme Sdn Bhd" in page and "Beta Trading" in page and "Firm accountant" in page


def test_a_company_created_later_is_opened_to_the_existing_accountants(db, fake_ports):
    make_company("Acme Sdn Bhd")
    call_command("create_accountant", "accountant@firm.example", default_firm().name)

    company = services.create_company_with_owner("Gamma Holdings", "owner@gamma.example", PASSWORD, default_firm())

    accountant = User.objects.get(email="accountant@firm.example")
    assert CompanyAccess.objects.filter(user=accountant, company=company, role=CompanyAccess.ACCOUNTANT).exists()
    assert CompanyAccess.objects.get(company=company, role=CompanyAccess.OWNER).user.email == "owner@gamma.example"


def test_creating_a_company_creates_exactly_one_owner(db):
    company = services.create_company_with_owner("Gamma Holdings", "owner@gamma.example", PASSWORD, default_firm())

    assert CompanyAccess.objects.filter(company=company).count() == 1
    assert User.objects.get(email="owner@gamma.example").check_password(PASSWORD)


def test_an_address_that_already_has_an_account_cannot_become_a_second_owner_or_accountant(db, fake_ports):
    make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)

    with pytest.raises(CommandError):
        call_command("create_accountant", "owner@acme.example", default_firm().name)
    with pytest.raises(services.EmailInUse):
        services.create_company_with_owner("Gamma Holdings", "owner@acme.example", PASSWORD, default_firm())

    assert fake_ports.email.sent == []


def test_a_failed_company_creation_leaves_nothing_behind(db):
    make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)
    before = Company.objects.count()

    with pytest.raises(services.EmailInUse):
        services.create_company_with_owner("Gamma Holdings", "owner@acme.example", PASSWORD, default_firm())

    assert Company.objects.count() == before
