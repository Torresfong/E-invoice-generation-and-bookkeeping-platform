"""The firm administrator's pages (D49, D50): manage the firm's accountants, read the firm trail, see no client data."""

import re

import pytest
from django.core.management import call_command
from django.db import DatabaseError, transaction
from django.utils import timezone

from core import services
from core.models import AuditEntry, AuthToken, CompanyAccess, FirmAuditEntry, User

from .factories import PASSWORD, make_member
from .test_tenancy import query

NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def firms(firms_world):
    """`firms_world` plus a firm administrator in each firm and a staff member at Acme. Passwords are `factories.PASSWORD`."""
    firms_world["alpha_admin"] = services.create_firm_administrator("admin@alpha.example", firms_world["alpha"])
    firms_world["beta_admin"] = services.create_firm_administrator("admin@beta.example", firms_world["beta"])
    firms_world["acme_owner"] = User.objects.get(email="owner@acme.example")
    firms_world["acme_staff"] = make_member("staff@acme.example", firms_world["acme"], CompanyAccess.STAFF)
    for key in ("alpha_admin", "beta_admin"):
        firms_world[key].set_password(PASSWORD)
        firms_world[key].save()
    return firms_world


def setup_link(fake_ports):
    return re.search(r"(/reset/[\w-]+/)", fake_ports.email.sent[-1]["body"]).group(1)


def sign_in(client, user, password=PASSWORD):
    return client.post("/login/", {"email": user.email, "password": password})


def add(client, email, kind="accountant"):
    return client.post("/firm/add/", {"email": email, "kind": kind})


def test_the_home_page_links_an_administrator_to_the_firm_pages_and_nobody_else(client, firms, as_production_connection):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        admin_home = client.get("/").content.decode()
        client.post("/logout/")
        sign_in(client, firms["alpha_accountant"])
        accountant_home = client.get("/").content.decode()

    assert "/firm/" in admin_home and "/firm/" not in accountant_home


# --- listing and adding -----------------------------------------------------------------------------------------


def test_an_administrator_lists_their_own_firms_people_and_nobody_elses(client, firms, as_production_connection):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        page = client.get("/firm/").content.decode()

    assert "acc@alpha.example" in page and "admin@alpha.example" in page
    assert "acc@beta.example" not in page and "admin@beta.example" not in page and "owner@acme.example" not in page


def test_adding_an_accountant_gives_them_the_firms_companies_and_no_others_and_emails_a_setup_link(
    client, firms, fake_ports, as_production_connection
):
    new_person = type(client)()
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        assert add(client, "New.Person@Alpha.example").status_code == 302

    created = User.objects.get(email="new.person@alpha.example")
    assert (created.is_accountant, created.is_firm_admin, created.firm_id, created.has_usable_password()) == (
        True, False, firms["alpha"].pk, False,
    )
    assert set(CompanyAccess.objects.filter(user=created).values_list("company_id", "role")) == {
        (firms["acme"].pk, CompanyAccess.ACCOUNTANT)
    }
    assert [mail["to"] for mail in fake_ports.email.sent] == ["new.person@alpha.example"]

    with as_production_connection():
        link = setup_link(fake_ports)
        assert new_person.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 302
        assert new_person.post("/login/", {"email": "new.person@alpha.example", "password": NEW_PASSWORD}).status_code == 302
        companies = new_person.get("/companies/").content.decode()

    assert "Acme Sdn Bhd" in companies and "Gamma Holdings" not in companies


def test_the_accountant_an_administrator_adds_is_the_same_as_one_the_operator_adds(client, firms, as_production_connection):
    operator_made = services.create_accountant("op@alpha.example", firms["alpha"])
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        add(client, "web@alpha.example")

    def shape(user):
        rows = set(CompanyAccess.objects.filter(user=user).values_list("company_id", "role"))
        return (user.is_accountant, user.is_firm_admin, user.firm_id, user.is_active, user.has_usable_password(), rows)

    assert shape(User.objects.get(email="web@alpha.example")) == shape(operator_made)


def test_an_administrator_can_add_another_administrator_of_the_same_firm_who_has_no_company_access(
    client, firms, as_production_connection
):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        add(client, "second@alpha.example", kind="firm_admin")

    second = User.objects.get(email="second@alpha.example")
    assert (second.is_firm_admin, second.is_accountant, second.firm_id) == (True, False, firms["alpha"].pk)
    assert not CompanyAccess.objects.filter(user=second).exists()


def test_adding_an_address_that_already_has_an_account_sends_nothing_and_changes_nothing(
    client, firms, fake_ports, as_production_connection
):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        response = add(client, "owner@acme.example")

    owner = User.objects.get(email="owner@acme.example")
    assert response.status_code == 302 and fake_ports.email.sent == []
    assert (owner.is_accountant, owner.firm_id) == (False, None)


@pytest.mark.parametrize("email, kind", [("not-an-address", "accountant"), ("x@alpha.example", "owner"), ("x@alpha.example", "")])
def test_a_bad_address_or_kind_creates_nobody(client, firms, fake_ports, as_production_connection, email, kind):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        response = add(client, email, kind)

    assert response.status_code == 200 and fake_ports.email.sent == []
    assert not User.objects.filter(email__in=["not-an-address", "x@alpha.example"]).exists()


# --- deactivating -----------------------------------------------------------------------------------------------


def test_a_deactivated_accountant_cannot_sign_in_and_loses_their_open_session(client, firms, as_production_connection):
    accountant, session = firms["alpha_accountant"], type(client)()
    with as_production_connection():
        assert sign_in(session, accountant).status_code == 302
        assert session.get("/companies/").status_code == 200
        sign_in(client, firms["alpha_admin"])
        assert client.post(f"/firm/{accountant.pk}/deactivate/").status_code == 302

        assert session.get("/companies/").status_code == 401
        assert "not correct" in sign_in(type(client)(), accountant).content.decode()

    assert User.objects.get(pk=accountant.pk).is_active is False


def test_the_last_active_administrator_cannot_be_deactivated_but_one_of_two_can(client, firms, as_production_connection):
    first = firms["alpha_admin"]
    with as_production_connection():
        sign_in(client, first)
        refused = client.post(f"/firm/{first.pk}/deactivate/")
        assert refused.status_code == 200 and "last active administrator" in refused.content.decode()
        add(client, "second@alpha.example", kind="firm_admin")
    second = User.objects.get(email="second@alpha.example")

    with as_production_connection():
        assert client.post(f"/firm/{second.pk}/deactivate/").status_code == 302
        assert client.post(f"/firm/{first.pk}/deactivate/").status_code == 200  # the second is no longer active

    assert User.objects.get(pk=first.pk).is_active and not User.objects.get(pk=second.pk).is_active


def test_an_administrator_can_deactivate_themself_when_another_is_active(client, firms, as_production_connection):
    first = firms["alpha_admin"]
    with as_production_connection():
        sign_in(client, first)
        add(client, "second@alpha.example", kind="firm_admin")
        assert client.post(f"/firm/{first.pk}/deactivate/").status_code == 302

    assert not User.objects.get(pk=first.pk).is_active


def test_an_administrator_cannot_see_or_change_another_firms_people(client, firms, as_production_connection):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        for person in ("beta_accountant", "beta_admin"):
            assert client.post(f"/firm/{firms[person].pk}/deactivate/").status_code == 404
        assert client.post("/firm/99999999/deactivate/").status_code == 404

    assert User.objects.filter(pk__in=[firms["beta_accountant"].pk, firms["beta_admin"].pk], is_active=True).count() == 2


def test_clients_cannot_be_deactivated_from_the_firm_pages(client, firms, as_production_connection):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        statuses = [client.post(f"/firm/{firms[who].pk}/deactivate/").status_code for who in ("acme_owner", "acme_staff")]

    assert statuses == [404, 404]
    assert User.objects.filter(pk__in=[firms["acme_owner"].pk, firms["acme_staff"].pk], is_active=True).count() == 2


# --- who may use the pages --------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff", "alpha_accountant"])
def test_only_a_firm_administrator_reaches_the_firm_pages(client, firms, fake_ports, as_production_connection, who):
    with as_production_connection():
        assert sign_in(client, firms[who]).status_code == 302
        responses = [
            client.get("/firm/"),
            client.get("/firm/audit/"),
            add(client, "sneaky@alpha.example"),
            client.post(f"/firm/{firms['beta_accountant'].pk}/deactivate/"),
        ]

    assert [r.status_code for r in responses] == [403, 403, 403, 403]
    assert not User.objects.filter(email="sneaky@alpha.example").exists() and fake_ports.email.sent == []
    assert User.objects.get(pk=firms["beta_accountant"].pk).is_active


def test_anonymous_visitors_are_refused_the_firm_pages(client, firms):
    statuses = [client.get("/firm/").status_code, client.get("/firm/audit/").status_code, add(client, "x@y.example").status_code]
    assert statuses == [401, 401, 401]


@pytest.mark.parametrize(
    "path", ["/companies/{pk}/", "/companies/{pk}/audit/", "/companies/{pk}/activity/", "/companies/{pk}/invite/"]
)
def test_an_administrator_cannot_open_any_client_company_page_not_even_their_own_firms(client, firms, as_production_connection, path):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        send = client.post if path.endswith("/invite/") else client.get
        statuses = [send(path.format(pk=firms[c].pk)).status_code for c in ("acme", "gamma")]
        companies = client.get("/companies/").content.decode()

    assert statuses == [404, 404]
    assert "Acme Sdn Bhd" not in companies and "Gamma Holdings" not in companies


# --- the firm trail ----------------------------------------------------------------------------------------------


def test_adding_and_deactivating_people_are_recorded_in_the_firms_trail_with_who_and_what(client, firms, as_production_connection):
    admin = firms["alpha_admin"]
    with as_production_connection():
        sign_in(client, admin)
        add(client, "new@alpha.example")
        client.post(f"/firm/{firms['alpha_accountant'].pk}/deactivate/")

    new = User.objects.get(email="new@alpha.example")
    entries = FirmAuditEntry.objects.filter(firm=firms["alpha"], actor_id=admin.pk).exclude(action=AuditEntry.LOGIN).order_by("id")
    assert [(e.action, e.target_type, e.target_id, e.actor_email, e.changed_fields) for e in entries] == [
        (AuditEntry.CREATE, "user", str(new.pk), "admin@alpha.example", ["email", "firm"]),
        (AuditEntry.UPDATE, "user", str(firms["alpha_accountant"].pk), "admin@alpha.example", ["is_active"]),
    ]


def test_an_administrator_reads_their_firms_trail_including_their_own_sign_in_and_not_another_firms(
    client, firms, as_production_connection
):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        add(client, "new@alpha.example")
        page = client.get("/firm/audit/").content.decode()

    assert "admin@alpha.example" in page and "Signed in" in page and "Created" in page
    assert "admin@beta.example" not in page and "acc@beta.example" not in page


def test_an_administrators_sign_in_and_sign_out_are_in_the_firm_trail_and_in_no_company_trail(client, firms, as_production_connection):
    admin = firms["alpha_admin"]
    with as_production_connection():
        sign_in(client, admin)
        client.post("/logout/")

    actions = FirmAuditEntry.objects.filter(actor_id=admin.pk).order_by("id").values_list("action", flat=True)
    assert list(actions) == [AuditEntry.LOGIN, AuditEntry.LOGOUT]
    assert not AuditEntry.objects.filter(actor_id=admin.pk).exists()


# --- the database functions behind the pages -----------------------------------------------------------------------


@pytest.mark.parametrize("who", ["alpha_accountant", "acme_staff", "acme_owner"])
def test_the_database_refuses_the_firm_functions_to_anyone_who_is_not_an_active_firm_administrator(firms, as_user, who):
    for statement in [
        "SELECT create_firm_person('x@alpha.example', 'accountant', 'hash', now() + interval '1 day')",
        "SELECT deactivate_firm_person(1)",
        "SELECT * FROM firm_people()",
    ]:
        with as_user(firms[who]), pytest.raises(DatabaseError), transaction.atomic():
            query(statement)


def test_a_deactivated_administrator_is_refused_by_the_database_too(firms, as_user):
    User.objects.filter(pk=firms["alpha_admin"].pk).update(is_active=False)

    with as_user(firms["alpha_admin"]), pytest.raises(DatabaseError), transaction.atomic():
        query("SELECT * FROM firm_people()")


# --- the setup link -----------------------------------------------------------------------------------------------


def hours_left(email):
    token = AuthToken.objects.get(email=email, purpose=AuthToken.RESET, used_at__isnull=True)
    return (token.expires_at - timezone.now()).total_seconds() / 3600


def test_the_setup_link_for_a_person_an_administrator_adds_lasts_24_hours(client, firms, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        add(client, "new@alpha.example")

    assert 23.9 < hours_left("new@alpha.example") <= 24
    assert "24 hours" in fake_ports.email.sent[-1]["body"]


def test_the_setup_link_for_a_firms_first_administrator_lasts_24_hours(db, fake_ports):
    call_command("create_firm", "Gamma Group", "boss@gamma.example")

    assert 23.9 < hours_left("boss@gamma.example") <= 24
    assert "24 hours" in fake_ports.email.sent[-1]["body"]


def test_an_ordinary_password_reset_link_still_lasts_an_hour(client, firms, fake_ports, as_production_connection):
    with as_production_connection():
        client.post("/password-reset/", {"email": "admin@alpha.example"})

    assert 0.9 < hours_left("admin@alpha.example") <= 1


# --- an administrator deactivated while waiting cannot go on to act -----------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_an_administrator_deactivated_while_waiting_for_the_firm_lock_cannot_go_on_to_act():
    import threading
    import time

    import psycopg
    from django.db import connection

    alpha = services.create_firm("Alpha Accounting")
    first = services.create_firm_administrator("a@alpha.example", alpha)
    second = services.create_firm_administrator("b@alpha.example", alpha)
    settings = connection.settings_dict
    dsn = f"dbname={settings['NAME']} user={settings['USER']} password={settings['PASSWORD']} host={settings['HOST']} port={settings['PORT'] or 5432}"
    first_session, second_session = psycopg.connect(dsn), psycopg.connect(dsn)
    outcome = {}

    def second_tries_to_add():
        try:
            second_session.execute("SELECT set_config('app.user_id', %s, false)", [str(second.pk)])
            second_session.execute("SELECT create_firm_person('late@alpha.example', 'accountant', 'hash', now() + interval '1 day')")
            second_session.commit()
        except psycopg.Error as error:
            outcome["error"] = error

    try:
        first_session.execute("SELECT set_config('app.user_id', %s, false)", [str(first.pk)])
        first_session.execute("SELECT deactivate_firm_person(%s)", [second.pk])  # holds the firm until the commit below
        waiting = threading.Thread(target=second_tries_to_add)
        waiting.start()
        for _ in range(50):  # wait until the second administrator is queued behind the first
            if first_session.execute("SELECT count(*) FROM pg_locks WHERE NOT granted").fetchone()[0]:
                break
            time.sleep(0.1)
        first_session.commit()
        waiting.join(timeout=10)
    finally:
        first_session.close()
        second_session.close()

    assert isinstance(outcome.get("error"), psycopg.errors.InsufficientPrivilege)
    assert not User.objects.filter(email="late@alpha.example").exists()


# --- a setup email that cannot be sent leaves nobody half-created ---------------------------------------------


def test_a_person_whose_setup_email_cannot_be_sent_is_not_created_and_the_address_can_be_added_again(
    client, firms, fake_ports, as_production_connection
):
    client.raise_request_exception = False
    good_send = fake_ports.email.send

    def refuse(to, subject, body):
        raise RuntimeError("the mail server is down")

    with as_production_connection():
        sign_in(client, firms["alpha_admin"])
        fake_ports.email.send = refuse
        assert add(client, "new@alpha.example").status_code == 500
        fake_ports.email.send = good_send
        assert add(client, "new@alpha.example").status_code == 302

    assert User.objects.filter(email="new@alpha.example").count() == 1
    assert AuthToken.objects.filter(email="new@alpha.example").count() == 1
    assert [mail["to"] for mail in fake_ports.email.sent] == ["new@alpha.example"]
    created = FirmAuditEntry.objects.filter(firm=firms["alpha"], action=AuditEntry.CREATE, target_type="user")
    assert created.filter(actor_id=firms["alpha_admin"].pk).count() == 1
