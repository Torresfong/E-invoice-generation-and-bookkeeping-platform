"""A firm registers itself and the operator approves it (ticket #142; D67 (1), D49 (3)).

Applying goes over HTTP with the email port faked. The operator's side is the management commands. The applications
table is closed to the web role: the database, not the view, keeps an applicant from reading anything.
"""

import re

import pytest
from django.core.management import CommandError, call_command
from django.db import ProgrammingError, transaction
from django.test import Client

from core.models import AuditEntry, Firm, FirmApplication, FirmAuditEntry, User

from .factories import PASSWORD, default_firm, make_company

NEW_PASSWORD = "a brand new passphrase"
RECEIVED = "If this application can be accepted"


@pytest.fixture
def applying_open(db, settings):
    settings.FIRM_APPLICATIONS_ENABLED = True


def apply(client=None, **overrides):
    data = {
        "firm_name": "Lim & Partners",
        "ssm_number": "202001000001",
        "contact_name": "Lim Wei",
        "email": "lim@limpartners.example",
        "people": "Two accountants, one assistant",
        **overrides,
    }
    return (client or Client()).post("/apply/", data)


def counts():
    return Firm.objects.count(), User.objects.count(), FirmApplication.objects.count()


def pending(**overrides):
    """An application as the web form stores it."""
    assert RECEIVED in apply(**overrides).content.decode()
    return FirmApplication.objects.get(contact_email=overrides.get("email", "lim@limpartners.example"))


def setup_link(fake_ports):
    return re.search(r"(/reset/[\w-]+/)", fake_ports.email.sent[-1]["body"]).group(1)


# --- applying: a stored application, nothing else ----------------------------------------------------------------


def test_applying_stores_an_application_and_creates_no_firm_and_no_user(applying_open, fake_ports, as_production_connection):
    before = Firm.objects.count(), User.objects.count()

    with as_production_connection():
        response = apply()

    assert response.status_code == 200 and RECEIVED in response.content.decode()
    assert (Firm.objects.count(), User.objects.count()) == before
    stored = FirmApplication.objects.get()
    assert (stored.firm_name, stored.ssm_number, stored.contact_email, stored.status) == (
        "Lim & Partners",
        "202001000001",
        "lim@limpartners.example",
        FirmApplication.PENDING,
    )
    assert fake_ports.email.sent == []


def test_the_form_is_shown_to_anyone(applying_open, as_production_connection):
    with as_production_connection():
        response = Client().get("/apply/")

    assert response.status_code == 200 and 'name="firm_name"' in response.content.decode()


def test_applying_can_be_switched_off(settings, as_production_connection):
    settings.FIRM_APPLICATIONS_ENABLED = False

    with as_production_connection():
        form, response = Client().get("/apply/"), apply()

    assert form.status_code == response.status_code == 403 and FirmApplication.objects.count() == 0


def test_it_is_switched_off_by_default():
    from django.conf import settings

    assert settings.FIRM_APPLICATIONS_ENABLED is False


def test_an_applicant_cannot_read_the_applications_table(applying_open, as_production_connection):
    apply()

    with as_production_connection():
        # A savepoint, so the refusal does not abort the test's transaction before the connection is reset.
        with pytest.raises(ProgrammingError), transaction.atomic():
            FirmApplication.objects.count()


@pytest.mark.parametrize("field", ["firm_name", "ssm_number", "contact_name", "email"])
def test_an_incomplete_application_is_asked_again_and_stores_nothing(applying_open, field, as_production_connection):
    with as_production_connection():
        response = apply(**{field: "   "})

    assert response.status_code == 200 and RECEIVED not in response.content.decode()
    assert FirmApplication.objects.count() == 0


def test_an_address_that_already_has_an_account_gets_the_same_answer_and_stores_nothing(applying_open, as_production_connection):
    make_company("Acme Sdn Bhd")
    User.objects.create_user("lim@limpartners.example", password=PASSWORD)

    with as_production_connection():
        response = apply(email="LIM@limpartners.example")

    assert response.status_code == 200 and RECEIVED in response.content.decode()
    assert FirmApplication.objects.count() == 0


def test_a_firm_name_that_matches_an_existing_firm_ignoring_case_gets_the_same_answer_and_stores_nothing(
    applying_open, as_production_connection
):
    existing = default_firm()

    with as_production_connection():
        response = apply(firm_name=f"  {existing.name.upper()} ")

    assert response.status_code == 200 and RECEIVED in response.content.decode()
    assert FirmApplication.objects.count() == 0


def test_a_second_application_with_the_same_name_or_address_while_one_is_pending_stores_nothing(
    applying_open, as_production_connection
):
    with as_production_connection():
        apply()
        same_name = apply(firm_name="LIM & PARTNERS", email="other@limpartners.example")
        same_address = apply(firm_name="Another Firm", email="Lim@LimPartners.example")

    assert same_name.status_code == same_address.status_code == 200
    assert FirmApplication.objects.count() == 1


def test_applications_are_throttled_per_address_and_per_client(applying_open, settings, as_production_connection):
    settings.SIGNUP_MAX_PER_EMAIL = 2
    settings.SIGNUP_MAX_PER_CLIENT = 3

    with as_production_connection():
        by_address = [apply(Client(REMOTE_ADDR=f"10.0.0.{n}"), email="a@x.example", firm_name=f"F{n}") for n in range(3)]
        by_client = [apply(Client(REMOTE_ADDR="10.0.1.1"), email=f"b{n}@x.example", firm_name=f"G{n}") for n in range(4)]

    assert [r.status_code for r in by_address] == [200, 200, 429]
    assert [r.status_code for r in by_client] == [200, 200, 200, 429]


# --- the operator decides ----------------------------------------------------------------------------------------


def test_the_operator_lists_pending_applications(applying_open, capsys):
    pending()

    call_command("list_firm_applications")

    out = capsys.readouterr().out
    assert "Lim & Partners" in out and "202001000001" in out and "lim@limpartners.example" in out


def test_approving_creates_the_firm_and_its_administrator_who_is_emailed_a_password_link(applying_open, fake_ports):
    application = pending()

    call_command("approve_firm_application", str(application.pk))

    firm = Firm.objects.get(name="Lim & Partners")
    administrator = User.objects.get(email="lim@limpartners.example")
    assert administrator.is_firm_admin and administrator.firm == firm and not administrator.has_usable_password()
    assert [mail["to"] for mail in fake_ports.email.sent] == ["lim@limpartners.example"]
    application.refresh_from_db()
    assert application.status == FirmApplication.APPROVED and application.firm == firm
    assert application.decided_by == "operator" and application.decided_at is not None


def test_approving_is_recorded_in_the_new_firms_trail(applying_open):
    application = pending()

    call_command("approve_firm_application", str(application.pk))

    entry = FirmAuditEntry.objects.get(firm__name="Lim & Partners", target_type="firm_application")
    assert (entry.action, entry.target_id, entry.actor_email) == (AuditEntry.UPDATE, str(application.pk), "operator")
    assert entry.changed_fields == ["status"]


def test_the_new_administrator_chooses_a_password_and_adds_accountants_but_reads_no_client_data(
    applying_open, fake_ports, as_production_connection
):
    make_company("Acme Sdn Bhd")
    application = pending()
    call_command("approve_firm_application", str(application.pk))
    link = setup_link(fake_ports)

    with as_production_connection():
        client = Client()
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 302
        assert client.post("/login/", {"email": "lim@limpartners.example", "password": NEW_PASSWORD}).status_code == 302
        assert client.post("/firm/add/", {"email": "acc@limpartners.example", "kind": "accountant"}).status_code == 302
        companies = client.get("/companies/")

    assert User.objects.get(email="acc@limpartners.example").firm.name == "Lim & Partners"
    assert "Acme Sdn Bhd" not in companies.content.decode()


def test_an_applicant_cannot_sign_in_before_approval(applying_open, as_production_connection):
    apply()

    with as_production_connection():
        response = Client().post("/login/", {"email": "lim@limpartners.example", "password": NEW_PASSWORD})

    assert response.status_code != 302


def test_refusing_creates_nothing_and_records_who_and_when(applying_open, fake_ports):
    application = pending()
    firms, users = Firm.objects.count(), User.objects.count()

    call_command("refuse_firm_application", str(application.pk), "--note", "Cannot verify the SSM number")

    assert (Firm.objects.count(), User.objects.count()) == (firms, users)
    application.refresh_from_db()
    assert (application.status, application.decided_by, application.refusal_note) == (
        FirmApplication.REFUSED,
        "operator",
        "Cannot verify the SSM number",
    )
    assert application.decided_at is not None and application.firm is None
    assert fake_ports.email.sent == []


def test_an_application_is_decided_only_once(applying_open):
    application = pending()
    call_command("refuse_firm_application", str(application.pk))
    before = counts()

    with pytest.raises(CommandError):
        call_command("approve_firm_application", str(application.pk))
    with pytest.raises(CommandError):
        call_command("refuse_firm_application", str(application.pk))

    assert counts() == before


def test_approval_is_refused_when_the_address_got_an_account_meanwhile(applying_open):
    application = pending()
    User.objects.create_user("lim@limpartners.example", password=PASSWORD)

    with pytest.raises(CommandError):
        call_command("approve_firm_application", str(application.pk))

    application.refresh_from_db()
    assert application.status == FirmApplication.PENDING and not Firm.objects.filter(name="Lim & Partners").exists()


def test_approval_is_refused_when_a_firm_of_that_name_exists_meanwhile(applying_open):
    application = pending()
    Firm.objects.create(name="lim & partners")

    with pytest.raises(CommandError):
        call_command("approve_firm_application", str(application.pk))

    application.refresh_from_db()
    assert application.status == FirmApplication.PENDING


def test_an_approval_whose_email_cannot_be_sent_changes_nothing(applying_open, email_down):
    application = pending()
    before = Firm.objects.count(), User.objects.count()

    with pytest.raises(RuntimeError):
        call_command("approve_firm_application", str(application.pk))

    application.refresh_from_db()
    assert (Firm.objects.count(), User.objects.count()) == before and application.status == FirmApplication.PENDING


def test_an_unknown_application_is_reported(db):
    with pytest.raises(CommandError):
        call_command("approve_firm_application", "999999")


def test_the_operator_command_that_creates_a_firm_still_works(db, fake_ports):
    call_command("create_firm", "Direct Firm", "admin@direct.example")

    assert User.objects.get(email="admin@direct.example").firm.name == "Direct Firm"
