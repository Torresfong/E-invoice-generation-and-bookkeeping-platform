"""The firm administrator (D49): manages the accountants of their own firm and sees no client data."""

import re

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, IntegrityError, transaction

from core import services
from core.models import AuditEntry, CompanyAccess, Firm, FirmAuditEntry, User

from .factories import PASSWORD
from .test_firms import check_now, world  # noqa: F401  (two firms, each with an accountant and a company)

NEW_PASSWORD = "a brand new passphrase"


def setup_link(fake_ports):
    return re.search(r"(/reset/[\w-]+/)", fake_ports.email.sent[-1]["body"]).group(1)


# --- the operator creates a firm with its first administrator -----------------------------------------------


def test_the_operator_command_creates_a_firm_with_its_first_administrator_who_sets_a_password_from_the_emailed_link(
    client, db, fake_ports, as_production_connection
):
    call_command("create_firm", "Gamma Group", "Boss@Gamma.example")

    admin = User.objects.get(email="boss@gamma.example")
    assert (admin.firm.name, admin.is_firm_admin, admin.is_accountant, admin.has_usable_password()) == (
        "Gamma Group", True, False, False,
    )
    assert [mail["to"] for mail in fake_ports.email.sent] == ["boss@gamma.example"]
    assert not CompanyAccess.objects.filter(user=admin).exists()

    with as_production_connection():
        link = setup_link(fake_ports)
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 302
        assert client.post("/login/", {"email": "boss@gamma.example", "password": NEW_PASSWORD}).status_code == 302


def test_the_command_refuses_an_address_that_already_has_an_account_and_creates_no_firm(world, fake_ports):  # noqa: F811
    with pytest.raises(CommandError, match="already has an account"):
        call_command("create_firm", "Gamma Group", "owner@acme.example")

    assert not Firm.objects.filter(name="Gamma Group").exists()


def test_the_operator_command_records_the_firm_and_its_administrator_in_the_firms_trail(db, fake_ports):
    call_command("create_firm", "Gamma Group", "boss@gamma.example")

    entries = FirmAuditEntry.objects.filter(firm__name="Gamma Group").order_by("id")
    assert [(e.action, e.target_type, e.actor_email) for e in entries] == [
        (AuditEntry.CREATE, "firm", "operator"),
        (AuditEntry.CREATE, "user", "operator"),
    ]


# --- a person is an administrator or an accountant, never both -----------------------------------------------


def test_the_database_refuses_a_user_who_is_both_an_accountant_and_an_administrator_or_an_administrator_with_no_firm(
    world,  # noqa: F811
):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=world["alpha_accountant"].pk).update(is_firm_admin=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("nofirm@x.example", is_firm_admin=True)


def test_the_database_refuses_company_access_for_a_firm_administrator_however_it_is_written(world):  # noqa: F811
    administrator = services.create_firm_administrator("admin@alpha.example", world["alpha"])
    owner = User.objects.get(email="owner@acme.example")
    check_now()

    with pytest.raises(DatabaseError), transaction.atomic():  # also refused by 02h's check, with its own message
        CompanyAccess.objects.create(user=administrator, company=world["acme"], role=CompanyAccess.ACCOUNTANT)
    with pytest.raises(DatabaseError, match="administrator"), transaction.atomic():
        CompanyAccess.objects.create(user=administrator, company=world["acme"], role=CompanyAccess.STAFF)
    with pytest.raises(DatabaseError, match="administrator"), transaction.atomic():
        User.objects.filter(pk=owner.pk).update(is_firm_admin=True, firm=world["alpha"])  # a client made an administrator
    with pytest.raises(DatabaseError, match="administrator"), transaction.atomic():
        CompanyAccess.objects.filter(user=owner).update(user=administrator)

    assert not CompanyAccess.objects.filter(user=administrator).exists()
    assert not User.objects.get(pk=owner.pk).is_firm_admin


def test_clients_and_accountants_keep_their_company_access(world):  # noqa: F811
    check_now()

    CompanyAccess.objects.create(user=User.objects.create_user("staff@x.example"), company=world["acme"], role=CompanyAccess.STAFF)
    assert CompanyAccess.objects.filter(user=world["alpha_accountant"]).exists()


# --- a setup email that cannot be sent leaves nobody half-created ---------------------------------------------


@pytest.fixture
def broken_email(fake_ports):
    def refuse(to, subject, body):
        raise RuntimeError("the mail server is down")

    fake_ports.email.send = refuse


def test_a_firm_whose_first_administrators_email_cannot_be_sent_is_not_created(db, broken_email):
    with pytest.raises(RuntimeError):
        call_command("create_firm", "Gamma Group", "boss@gamma.example")

    assert not Firm.objects.filter(name="Gamma Group").exists()
    assert not User.objects.filter(email="boss@gamma.example").exists()
    assert not FirmAuditEntry.objects.filter(firm__name="Gamma Group").exists()


def test_the_same_command_can_be_run_again_once_the_email_works(db, fake_ports):
    call_command("create_firm", "Gamma Group", "boss@gamma.example")

    assert [mail["to"] for mail in fake_ports.email.sent] == ["boss@gamma.example"]


@pytest.mark.django_db(transaction=True)
def test_a_firm_administrator_with_company_access_is_refused_when_the_transaction_commits():
    alpha = services.create_firm("Alpha Accounting")
    administrator = services.create_firm_administrator("admin@alpha.example", alpha)
    company = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, alpha)
    owner = User.objects.get(email="owner@acme.example")

    # No early check is asked for: the refusal comes from the commit itself.
    with pytest.raises(DatabaseError, match="administrator"), transaction.atomic():
        CompanyAccess.objects.create(user=administrator, company=company, role=CompanyAccess.STAFF)
    with pytest.raises(DatabaseError, match="administrator"), transaction.atomic():
        User.objects.filter(pk=owner.pk).update(is_firm_admin=True, firm=alpha)

    assert not CompanyAccess.objects.filter(user=administrator).exists()
    assert not User.objects.get(pk=owner.pk).is_firm_admin
