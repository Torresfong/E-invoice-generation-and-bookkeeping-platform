"""Firms: each company belongs to one firm, and a firm accountant reaches only their own firm's companies (D49)."""

import re

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.urls import get_resolver

from core import services
from core.models import AuditEntry, Company, CompanyAccess, Firm, User

from .factories import PASSWORD
from .test_tenancy import query


@pytest.fixture
def world(db):
    """Two firms, each with an accountant and a company. Passwords are `factories.PASSWORD`."""
    alpha, beta = services.create_firm("Alpha Accounting"), services.create_firm("Beta Books")
    people = {"alpha_accountant": services.create_accountant("acc@alpha.example", alpha)}
    people["beta_accountant"] = services.create_accountant("acc@beta.example", beta)
    for user in people.values():
        user.set_password(PASSWORD)
        user.save()
    acme = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, alpha)
    gamma = services.create_company_with_owner("Gamma Holdings", "owner@gamma.example", PASSWORD, beta)
    return {"alpha": alpha, "beta": beta, "acme": acme, "gamma": gamma, **people}


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def accountant_rows(company):
    return set(
        CompanyAccess.objects.filter(company=company, role=CompanyAccess.ACCOUNTANT).values_list("user__email", flat=True)
    )


# --- one firm for every company and accountant --------------------------------------------------------------


def test_a_company_cannot_exist_without_a_firm(db):
    with pytest.raises(IntegrityError), transaction.atomic():
        Company.objects.create(name="Orphan Bhd")


def test_an_accountant_cannot_exist_without_a_firm(db):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("lost@firm.example", is_accountant=True)


def test_creating_a_company_for_a_firm_gives_access_to_that_firms_accountants_and_no_others(world):
    assert world["acme"].firm == world["alpha"]
    assert accountant_rows(world["acme"]) == {"acc@alpha.example"}
    assert accountant_rows(world["gamma"]) == {"acc@beta.example"}


def test_creating_an_accountant_gives_access_to_that_firms_companies_only(world):
    second = services.create_accountant("second@alpha.example", world["alpha"])

    assert set(CompanyAccess.objects.filter(user=second).values_list("company__name", flat=True)) == {"Acme Sdn Bhd"}
    assert second.firm == world["alpha"] and second.is_accountant


def test_a_firm_with_no_accountant_yet_gets_them_their_access_when_they_are_created(db):
    firm = services.create_firm("New Firm")
    company = services.create_company_with_owner("Delta Bhd", "owner@delta.example", PASSWORD, firm)

    accountant = services.create_accountant("late@newfirm.example", firm)

    assert accountant_rows(company) == {"late@newfirm.example"}


# --- the operator commands ----------------------------------------------------------------------------------


def test_the_operator_creates_a_firm_and_then_an_accountant_in_it(db, fake_ports):
    call_command("create_firm", "Alpha Accounting")
    company = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, Firm.objects.get())

    call_command("create_accountant", "Acc@Alpha.example", "alpha accounting")

    accountant = User.objects.get(email="acc@alpha.example")
    assert accountant.firm.name == "Alpha Accounting" and not accountant.has_usable_password()
    assert accountant_rows(company) == {"acc@alpha.example"}
    assert [mail["to"] for mail in fake_ports.email.sent] == ["acc@alpha.example"]


def test_an_accountant_cannot_be_created_without_a_firm_or_in_a_firm_that_does_not_exist(world, fake_ports):
    with pytest.raises(CommandError):
        call_command("create_accountant", "no-firm@x.example")
    with pytest.raises(CommandError, match="No firm"):
        call_command("create_accountant", "ghost@x.example", "Nowhere Ltd")

    assert not User.objects.filter(email__in=["no-firm@x.example", "ghost@x.example"]).exists()
    assert fake_ports.email.sent == []


def test_two_firms_cannot_share_a_name_even_in_another_case(world):
    with pytest.raises(CommandError):
        call_command("create_firm", "ALPHA accounting")


# --- moving a company ---------------------------------------------------------------------------------------


def test_moving_a_company_swaps_the_accountant_access_in_both_directions(world):
    owner_rows = set(CompanyAccess.objects.filter(company=world["acme"]).exclude(role="accountant").values_list("user_id", "role"))

    call_command("move_company", str(world["acme"].pk), "Beta Books")

    world["acme"].refresh_from_db()
    assert world["acme"].firm == world["beta"]
    assert accountant_rows(world["acme"]) == {"acc@beta.example"}
    assert set(CompanyAccess.objects.filter(company=world["acme"]).exclude(role="accountant").values_list("user_id", "role")) == owner_rows
    assert accountant_rows(world["gamma"]) == {"acc@beta.example"}  # an untouched company


def test_after_a_move_the_old_firm_gets_404_and_the_new_firm_gets_the_page(client, world, as_production_connection):
    services.move_company(world["acme"], world["beta"])
    page = f"/companies/{world['acme'].pk}/"

    with as_production_connection():
        sign_in(client, world["alpha_accountant"])
        old_firm = client.get(page).status_code
        client.post("/logout/")
        sign_in(client, world["beta_accountant"])
        new_firm = client.get(page).status_code

    assert (old_firm, new_firm) == (404, 200)


def test_moving_a_company_to_the_firm_it_is_already_in_changes_nothing(world):
    services.move_company(world["acme"], world["alpha"])

    assert accountant_rows(world["acme"]) == {"acc@alpha.example"}
    assert AuditEntry.objects.filter(company=world["acme"], changed_fields=["firm"]).count() == 1  # only the assignment


def test_moving_to_a_firm_that_does_not_exist_is_refused_and_changes_nothing(world):
    with pytest.raises(CommandError, match="No firm"):
        call_command("move_company", str(world["acme"].pk), "Nowhere Ltd")
    with pytest.raises(CommandError, match="No company"):
        call_command("move_company", "999999", "Beta Books")

    world["acme"].refresh_from_db()
    assert world["acme"].firm == world["alpha"]


# --- the audit trail ----------------------------------------------------------------------------------------


def test_the_assignment_and_a_move_are_in_the_trail_of_the_company_concerned_and_no_other(world):
    services.move_company(world["acme"], world["beta"])

    entries = list(AuditEntry.objects.filter(company=world["acme"], changed_fields=["firm"]).order_by("id"))
    assert [entry.action for entry in entries] == [AuditEntry.CREATE, AuditEntry.UPDATE]
    for entry in entries:
        assert (entry.target_type, entry.target_id, entry.actor_email) == ("company", str(world["acme"].pk), "operator")
        assert entry.occurred_at is not None
    assert AuditEntry.objects.filter(company=world["gamma"], changed_fields=["firm"]).count() == 1


def test_the_owner_reads_the_firm_entries_in_the_companys_trail_without_any_firm_name(
    client, world, as_production_connection
):
    services.move_company(world["acme"], world["beta"])
    owner = User.objects.get(email="owner@acme.example")

    with as_production_connection():
        sign_in(client, owner)
        page = client.get(f"/companies/{world['acme'].pk}/audit/").content.decode()

    assert "company" in page and "firm" in page
    assert "Alpha Accounting" not in page and "Beta Books" not in page


# --- an accountant of one firm cannot reach another firm's companies ----------------------------------------


def test_each_accountant_lists_only_their_own_firms_companies(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["alpha_accountant"])
        alpha_list = client.get("/companies/").content.decode()
        client.post("/logout/")
        sign_in(client, world["beta_accountant"])
        beta_list = client.get("/companies/").content.decode()

    assert "Acme Sdn Bhd" in alpha_list and "Gamma Holdings" not in alpha_list
    assert "Gamma Holdings" in beta_list and "Acme Sdn Bhd" not in beta_list


@pytest.mark.parametrize("path", ["/companies/{pk}/", "/companies/{pk}/audit/", "/companies/{pk}/activity/"])
def test_an_accountant_cannot_open_another_firms_company_pages(client, world, path, as_production_connection):
    with as_production_connection():
        sign_in(client, world["alpha_accountant"])
        response = client.get(path.format(pk=world["gamma"].pk))
        own = client.get(path.format(pk=world["acme"].pk))

    assert response.status_code == 404 and "Gamma Holdings" not in response.content.decode()
    assert own.status_code == 200


def test_an_accountant_cannot_invite_into_another_firms_company(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["alpha_accountant"])
        response = client.post(f"/companies/{world['gamma'].pk}/invite/", {"email": "new@x.example"})

    assert response.status_code == 404 and fake_ports.email.sent == []


def test_what_an_accountant_does_in_one_firm_leaves_no_trace_in_another_firms_trail(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["alpha_accountant"])
        client.get(f"/companies/{world['acme'].pk}/")
        client.get(f"/companies/{world['gamma'].pk}/")

    assert AuditEntry.objects.filter(company=world["gamma"], actor_email="acc@alpha.example").exists() is False


# --- the web has no way to make or change firms ------------------------------------------------------------


def test_a_user_sees_only_their_own_firm_and_a_client_sees_none(world, as_user):
    owner = User.objects.get(email="owner@acme.example")

    with as_user(world["alpha_accountant"]):
        assert query("SELECT name FROM core_firm") == [("Alpha Accounting",)]
    with as_user(world["beta_accountant"]):
        assert query("SELECT name FROM core_firm") == [("Beta Books",)]
    with as_user(owner):
        assert query("SELECT name FROM core_firm") == []


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO core_firm (name, created_at) VALUES ('Mine', now())",
        "UPDATE core_firm SET name = 'Renamed'",
        "DELETE FROM core_firm",
        "UPDATE core_company SET firm_id = firm_id + 1",
        "UPDATE core_user SET firm_id = NULL, is_accountant = false",
        "INSERT INTO core_companyaccess (user_id, company_id, role, created_at) VALUES (1, 1, 'accountant', now())",
    ],
)
def test_the_web_role_cannot_create_or_change_firms_firm_links_or_accountants(world, as_user, statement):
    with as_user(world["alpha_accountant"]), pytest.raises(DatabaseError), transaction.atomic():
        query(statement)


def test_no_web_route_creates_a_firm_or_an_accountant():
    def routes(patterns, prefix=""):
        for pattern in patterns:
            text = prefix + str(pattern.pattern)
            if hasattr(pattern, "url_patterns"):
                yield from routes(pattern.url_patterns, text)
            else:
                yield f"{text} {getattr(pattern.callback, '__name__', '')}"

    named = re.compile(r"(firms?|accountants?)")
    assert not [route for route in routes(get_resolver("config.urls").url_patterns) if named.search(route.lower().replace("_", " "))]


def test_the_worker_role_cannot_read_firms(world):
    with connection.cursor() as cursor:
        cursor.execute("SELECT has_table_privilege('worker', 'core_firm', 'SELECT')")
        assert cursor.fetchone() == (False,)


# --- data from before firms existed -------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_companies_and_accountants_from_before_firms_existed_move_to_one_default_firm():
    from django.db.migrations.executor import MigrationExecutor

    latest = MigrationExecutor(connection).loader.graph.leaf_nodes("core")
    executor = MigrationExecutor(connection)
    executor.migrate([("core", "0019_firm")])  # firms exist, nothing requires one yet
    try:
        with connection.cursor() as cursor:
            cursor.execute("INSERT INTO core_company (name, created_at) VALUES ('Old Co', now()) RETURNING id")
            cursor.execute(
                "INSERT INTO core_user (email, password, is_active, is_accountant, is_superuser, date_joined) "
                "VALUES ('old@firm.example', '', true, true, false, now())"
            )
        MigrationExecutor(connection).migrate(latest)

        firm = Firm.objects.get()
        assert firm.name == "Default firm"
        assert Company.objects.get().firm == firm and User.objects.get().firm == firm
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_a_database_with_no_data_gets_no_default_firm():
    assert Firm.objects.count() == 0
