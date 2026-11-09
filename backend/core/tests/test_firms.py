"""Firms: a company has at most one firm, and accountants reach only their firm's companies (D49, D67)."""

import re
from importlib import import_module

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, IntegrityError, connection, models, transaction
from django.urls import get_resolver

from core import services
from core.models import AuditEntry, Company, CompanyAccess, Firm, User

from .factories import PASSWORD
from .test_tenancy import query


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def accountant_rows(company):
    return set(
        CompanyAccess.objects.filter(company=company, role=CompanyAccess.ACCOUNTANT).values_list("user__email", flat=True)
    )


# --- a company may have no firm; every accountant has one ---------------------------------------------------


def test_a_company_without_a_firm_is_created_for_its_owner_only(firms_world):
    company = services.create_company_with_owner("Independent Bhd", "owner@independent.example", PASSWORD)

    company.refresh_from_db()
    assert company.firm_id is None
    assert set(CompanyAccess.objects.filter(company=company).values_list("user__email", "role")) == {
        ("owner@independent.example", CompanyAccess.OWNER)
    }
    entry = AuditEntry.objects.get(company=company, action=AuditEntry.CREATE, target_type="company")
    assert entry.changed_fields == []


def test_the_owner_can_open_a_firmless_company_and_no_firms_accountant_can(
    client, firms_world, as_production_connection
):
    company = services.create_company_with_owner("Independent Bhd", "owner@independent.example", PASSWORD)
    owner = User.objects.get(email="owner@independent.example")
    page = f"/companies/{company.pk}/"

    with as_production_connection():
        sign_in(client, owner)
        owner_status = client.get(page).status_code
        client.post("/logout/")
        accountant_statuses = []
        for accountant in (firms_world["alpha_accountant"], firms_world["beta_accountant"]):
            sign_in(client, accountant)
            accountant_statuses.append(client.get(page).status_code)
            client.post("/logout/")

    assert owner_status == 200
    assert accountant_statuses == [404, 404]


def test_an_accountant_cannot_exist_without_a_firm(db):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("lost@firm.example", is_accountant=True)


def test_creating_a_company_for_a_firm_gives_access_to_that_firms_accountants_and_no_others(firms_world):
    assert firms_world["acme"].firm == firms_world["alpha"]
    assert accountant_rows(firms_world["acme"]) == {"acc@alpha.example"}
    assert accountant_rows(firms_world["gamma"]) == {"acc@beta.example"}


def test_creating_an_accountant_gives_access_to_that_firms_companies_only(firms_world):
    second = services.create_accountant("second@alpha.example", firms_world["alpha"])

    assert set(CompanyAccess.objects.filter(user=second).values_list("company__name", flat=True)) == {"Acme Sdn Bhd"}
    assert second.firm == firms_world["alpha"] and second.is_accountant


def test_a_firm_with_no_accountant_yet_gets_them_their_access_when_they_are_created(db):
    firm = services.create_firm("New Firm")
    company = services.create_company_with_owner("Delta Bhd", "owner@delta.example", PASSWORD, firm)

    services.create_accountant("late@newfirm.example", firm)

    assert accountant_rows(company) == {"late@newfirm.example"}


# --- the operator commands ----------------------------------------------------------------------------------


def test_the_operator_creates_a_firm_and_then_an_accountant_in_it(db, fake_ports):
    call_command("create_firm", "Alpha Accounting", "boss@alpha.example")
    company = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, Firm.objects.get())

    call_command("create_accountant", "Acc@Alpha.example", "alpha accounting")

    accountant = User.objects.get(email="acc@alpha.example")
    assert accountant.firm.name == "Alpha Accounting" and not accountant.has_usable_password()
    assert accountant_rows(company) == {"acc@alpha.example"}
    assert [mail["to"] for mail in fake_ports.email.sent] == ["boss@alpha.example", "acc@alpha.example"]


def test_an_accountant_cannot_be_created_without_a_firm_or_in_a_firm_that_does_not_exist(firms_world, fake_ports):
    with pytest.raises(CommandError):
        call_command("create_accountant", "no-firm@x.example")
    with pytest.raises(CommandError, match="No firm"):
        call_command("create_accountant", "ghost@x.example", "Nowhere Ltd")

    assert not User.objects.filter(email__in=["no-firm@x.example", "ghost@x.example"]).exists()
    assert fake_ports.email.sent == []


def test_a_firm_needs_a_name(db):
    with pytest.raises(CommandError):
        call_command("create_firm", "   ", "boss@x.example")
    assert Firm.objects.count() == 0


def test_two_firms_cannot_share_a_name_even_in_another_case(firms_world):
    with pytest.raises(CommandError):
        call_command("create_firm", "ALPHA accounting", "boss@x.example")


# --- moving a company ---------------------------------------------------------------------------------------


def test_binding_a_firmless_company_gives_access_to_exactly_the_new_firms_accountants(firms_world):
    company = services.create_company_with_owner("Independent Bhd", "owner@independent.example", PASSWORD)

    call_command("move_company", company.pk, "Alpha Accounting")

    company.refresh_from_db()
    assert company.firm == firms_world["alpha"]
    assert accountant_rows(company) == {"acc@alpha.example"}


def test_ending_a_link_removes_only_the_old_firms_accountants(firms_world):
    staff = User.objects.create_user("staff@acme.example")
    CompanyAccess.objects.create(user=staff, company=firms_world["acme"], role=CompanyAccess.STAFF)
    client_rows = set(
        CompanyAccess.objects.filter(company=firms_world["acme"])
        .exclude(role=CompanyAccess.ACCOUNTANT)
        .values_list("user_id", "role")
    )
    untouched_rows = set(
        CompanyAccess.objects.filter(company=firms_world["gamma"]).values_list("user_id", "role")
    )

    call_command("unlink_company", firms_world["acme"].pk)

    firms_world["acme"].refresh_from_db()
    assert firms_world["acme"].firm_id is None
    assert accountant_rows(firms_world["acme"]) == set()
    assert set(
        CompanyAccess.objects.filter(company=firms_world["acme"])
        .exclude(role=CompanyAccess.ACCOUNTANT)
        .values_list("user_id", "role")
    ) == client_rows
    assert set(CompanyAccess.objects.filter(company=firms_world["gamma"]).values_list("user_id", "role")) == untouched_rows


def test_moving_a_company_swaps_the_accountant_access_in_both_directions(firms_world):
    owner_rows = set(CompanyAccess.objects.filter(company=firms_world["acme"]).exclude(role="accountant").values_list("user_id", "role"))

    call_command("move_company", str(firms_world["acme"].pk), "Beta Books")

    firms_world["acme"].refresh_from_db()
    assert firms_world["acme"].firm == firms_world["beta"]
    assert accountant_rows(firms_world["acme"]) == {"acc@beta.example"}
    assert set(CompanyAccess.objects.filter(company=firms_world["acme"]).exclude(role="accountant").values_list("user_id", "role")) == owner_rows
    assert accountant_rows(firms_world["gamma"]) == {"acc@beta.example"}  # an untouched company


def test_after_a_move_the_old_firm_gets_404_and_the_new_firm_gets_the_page(client, firms_world, as_production_connection):
    services.move_company(firms_world["acme"], firms_world["beta"])
    page = f"/companies/{firms_world['acme'].pk}/"

    with as_production_connection():
        sign_in(client, firms_world["alpha_accountant"])
        old_firm = client.get(page).status_code
        client.post("/logout/")
        sign_in(client, firms_world["beta_accountant"])
        new_firm = client.get(page).status_code

    assert (old_firm, new_firm) == (404, 200)


def test_moving_a_company_to_the_firm_it_is_already_in_changes_nothing(firms_world):
    services.move_company(firms_world["acme"], firms_world["alpha"])

    assert accountant_rows(firms_world["acme"]) == {"acc@alpha.example"}
    assert AuditEntry.objects.filter(company=firms_world["acme"], changed_fields=["firm"]).count() == 1  # only the assignment


def test_moving_to_a_firm_that_does_not_exist_is_refused_and_changes_nothing(firms_world):
    with pytest.raises(CommandError, match="No firm"):
        call_command("move_company", str(firms_world["acme"].pk), "Nowhere Ltd")
    with pytest.raises(CommandError, match="No company"):
        call_command("move_company", "999999", "Beta Books")

    firms_world["acme"].refresh_from_db()
    assert firms_world["acme"].firm == firms_world["alpha"]


def test_an_accountants_access_rows_always_match_their_firm_whatever_the_operator_does(firms_world):
    """The database does not tie the two together, so every service call must leave them consistent."""

    def mismatched():
        return CompanyAccess.objects.filter(role=CompanyAccess.ACCOUNTANT).exclude(user__firm=models.F("company__firm"))

    assert not mismatched().exists()
    services.create_accountant("second@alpha.example", firms_world["alpha"])
    services.create_company_with_owner("Delta Bhd", "owner@delta.example", PASSWORD, firms_world["beta"])
    services.move_company(firms_world["acme"], firms_world["beta"])
    services.move_company(firms_world["gamma"], firms_world["alpha"])
    services.create_accountant("third@beta.example", firms_world["beta"])
    services.move_company(firms_world["acme"], firms_world["alpha"])

    assert not mismatched().exists()
    # Alpha (two accountants) now has Acme and Gamma; Beta (two accountants) has Delta.
    assert CompanyAccess.objects.filter(role=CompanyAccess.ACCOUNTANT).count() == 2 * 2 + 2 * 1


# --- the audit trail ----------------------------------------------------------------------------------------


def test_the_assignment_and_a_move_are_in_the_trail_of_the_company_concerned_and_no_other(firms_world):
    services.move_company(firms_world["acme"], firms_world["beta"])

    entries = list(AuditEntry.objects.filter(company=firms_world["acme"], changed_fields=["firm"]).order_by("id"))
    assert [entry.action for entry in entries] == [AuditEntry.CREATE, AuditEntry.UPDATE]
    for entry in entries:
        assert (entry.target_type, entry.target_id, entry.actor_email) == ("company", str(firms_world["acme"].pk), "operator")
        assert entry.occurred_at is not None
    assert AuditEntry.objects.filter(company=firms_world["gamma"], changed_fields=["firm"]).count() == 1


def test_the_owner_reads_the_firm_entries_in_the_companys_trail_without_any_firm_name(
    client, firms_world, as_production_connection
):
    services.move_company(firms_world["acme"], firms_world["beta"])
    owner = User.objects.get(email="owner@acme.example")

    with as_production_connection():
        sign_in(client, owner)
        page = client.get(f"/companies/{firms_world['acme'].pk}/audit/").content.decode()

    assert "company" in page and "firm" in page
    assert "Alpha Accounting" not in page and "Beta Books" not in page


# --- an accountant of one firm cannot reach another firm's companies ----------------------------------------


def test_each_accountant_lists_only_their_own_firms_companies(client, firms_world, as_production_connection):
    with as_production_connection():
        sign_in(client, firms_world["alpha_accountant"])
        alpha_list = client.get("/companies/").content.decode()
        client.post("/logout/")
        sign_in(client, firms_world["beta_accountant"])
        beta_list = client.get("/companies/").content.decode()

    assert "Acme Sdn Bhd" in alpha_list and "Gamma Holdings" not in alpha_list
    assert "Gamma Holdings" in beta_list and "Acme Sdn Bhd" not in beta_list


@pytest.mark.parametrize("path", ["/companies/{pk}/", "/companies/{pk}/audit/", "/companies/{pk}/activity/"])
def test_an_accountant_cannot_open_another_firms_company_pages(client, firms_world, path, as_production_connection):
    with as_production_connection():
        sign_in(client, firms_world["alpha_accountant"])
        response = client.get(path.format(pk=firms_world["gamma"].pk))
        own = client.get(path.format(pk=firms_world["acme"].pk))

    assert response.status_code == 404 and "Gamma Holdings" not in response.content.decode()
    assert own.status_code == 200


def test_an_accountant_cannot_invite_into_another_firms_company(client, firms_world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, firms_world["alpha_accountant"])
        response = client.post(f"/companies/{firms_world['gamma'].pk}/invite/", {"email": "new@x.example"})

    assert response.status_code == 404 and fake_ports.email.sent == []


def test_what_an_accountant_does_in_one_firm_leaves_no_trace_in_another_firms_trail(client, firms_world, as_production_connection):
    with as_production_connection():
        sign_in(client, firms_world["alpha_accountant"])
        client.get(f"/companies/{firms_world['acme'].pk}/")
        client.get(f"/companies/{firms_world['gamma'].pk}/")

    assert AuditEntry.objects.filter(company=firms_world["gamma"], actor_email="acc@alpha.example").exists() is False


# --- the web has no way to make or change firms ------------------------------------------------------------


def test_a_user_sees_only_their_own_firm_and_a_client_sees_none(firms_world, as_user):
    owner = User.objects.get(email="owner@acme.example")

    with as_user(firms_world["alpha_accountant"]):
        assert query("SELECT name FROM core_firm") == [("Alpha Accounting",)]
    with as_user(firms_world["beta_accountant"]):
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
def test_the_web_role_cannot_create_or_change_firms_firm_links_or_accountants(firms_world, as_user, statement):
    with as_user(firms_world["alpha_accountant"]), pytest.raises(DatabaseError), transaction.atomic():
        query(statement)


def test_the_only_web_routes_about_firms_or_accountants_are_the_firm_administrators_pages():
    """Firms are created only by the operator and accountants only by a firm administrator (D49): no other route may.

    The one public route is the firm's application (D67 (1)), which stores a request for the operator and creates nothing.
    """

    def routes(patterns, prefix=""):
        for pattern in patterns:
            text = prefix + str(pattern.pattern)
            if hasattr(pattern, "url_patterns"):
                yield from routes(pattern.url_patterns, text)
            else:
                yield f"{text} {getattr(pattern.callback, '__name__', '')}"

    named = re.compile(r"\b(firms?|accountants?)\b")
    found = sorted(route for route in routes(get_resolver("config.urls").url_patterns) if named.search(route.lower().replace("_", " ")))

    assert found == [
        "apply/ apply_as_firm",
        "firm/ firm",
        "firm/<int:pk>/deactivate/ deactivate_firm_person",
        "firm/add/ add_firm_person",
        "firm/audit/ firm_audit_trail",
    ]


def test_the_worker_role_cannot_read_firms(firms_world):
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
    from django.db.migrations.executor import MigrationExecutor

    latest = MigrationExecutor(connection).loader.graph.leaf_nodes("core")
    MigrationExecutor(connection).migrate([("core", "0019_firm")])
    try:
        MigrationExecutor(connection).migrate(latest)
        assert Firm.objects.count() == 0
    finally:
        MigrationExecutor(connection).migrate(latest)


def company_firm_is_nullable():
    return query(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'core_company' AND column_name = 'firm_id'"
    )


def firm_company_access_counts():
    return query(
        "SELECT (SELECT count(*) FROM core_firm), (SELECT count(*) FROM core_company), "
        "(SELECT count(*) FROM core_companyaccess)"
    )


@pytest.mark.django_db(transaction=True)
def test_the_nullable_firm_migration_preserves_existing_data_and_reverses(firms_world):
    from django.db.migrations.executor import MigrationExecutor

    latest = MigrationExecutor(connection).loader.graph.leaf_nodes("core")
    before = firm_company_access_counts()
    try:
        MigrationExecutor(connection).migrate([("core", "0036_item_master")])
        assert company_firm_is_nullable() == [("NO",)]

        MigrationExecutor(connection).migrate(latest)
        assert company_firm_is_nullable() == [("YES",)]
        assert firm_company_access_counts() == before

        MigrationExecutor(connection).migrate([("core", "0036_item_master")])
        assert company_firm_is_nullable() == [("NO",)]
    finally:
        MigrationExecutor(connection).migrate(latest)


@pytest.mark.django_db(transaction=True)
def test_the_nullable_firm_migration_cannot_reverse_while_a_company_has_no_firm(firms_world):
    from django.db.migrations.executor import MigrationExecutor

    latest = MigrationExecutor(connection).loader.graph.leaf_nodes("core")
    company = services.create_company_with_owner("Independent Bhd", "owner@independent.example", PASSWORD)
    try:
        with pytest.raises(DatabaseError):
            MigrationExecutor(connection).migrate([("core", "0036_item_master")])
        assert company_firm_is_nullable() == [("YES",)]  # the refused reversal changed nothing

        services.move_company(company, firms_world["alpha"])  # resolve the firm-less row, as the PR's rollback note says
        MigrationExecutor(connection).migrate([("core", "0036_item_master")])
        assert company_firm_is_nullable() == [("NO",)]
    finally:
        MigrationExecutor(connection).migrate(latest)


# --- the database keeps an accountant's access rows in their firm -------------------------------------------

accountant_triggers = import_module("core.migrations.0022_accountant_rows_match_firm")


def check_now():
    """Run the deferred checks at each statement, so a violation raises here instead of at commit."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_an_accountant_row_for_a_company_of_another_firm_is_refused(firms_world):
    check_now()

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.create(user=firms_world["alpha_accountant"], company=firms_world["gamma"], role=CompanyAccess.ACCOUNTANT)


def test_an_accountant_row_for_a_company_without_a_firm_is_refused(firms_world):
    company = services.create_company_with_owner("Independent Bhd", "owner@independent.example", PASSWORD)
    accountant = User.objects.create_user(
        "second@alpha.example", is_accountant=True, firm=firms_world["alpha"]
    )
    check_now()

    CompanyAccess.objects.create(
        user=accountant, company=firms_world["acme"], role=CompanyAccess.ACCOUNTANT
    )
    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.create(
            user=accountant, company=company, role=CompanyAccess.ACCOUNTANT
        )


def test_an_accountant_row_for_someone_who_is_not_an_accountant_is_refused(firms_world):
    owner = User.objects.get(email="owner@acme.example")
    check_now()

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.create(user=owner, company=firms_world["gamma"], role=CompanyAccess.ACCOUNTANT)


def test_moving_a_company_without_going_through_the_service_is_refused_while_accountant_rows_remain(firms_world):
    check_now()

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        Company.objects.filter(pk=firms_world["acme"].pk).update(firm=firms_world["beta"])


@pytest.mark.parametrize("change", [{"firm": "beta"}, {"is_accountant": False}])
def test_changing_an_accountants_firm_or_status_under_their_access_rows_is_refused(firms_world, change):
    check_now()
    change = {key: firms_world[value] if isinstance(value, str) else value for key, value in change.items()}

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        User.objects.filter(pk=firms_world["alpha_accountant"].pk).update(**change)


def test_the_service_operations_pass_the_check_at_every_statement(firms_world):
    check_now()

    services.create_accountant("second@alpha.example", firms_world["alpha"])
    services.create_company_with_owner("Delta Bhd", "owner@delta.example", PASSWORD, firms_world["beta"])
    services.move_company(firms_world["acme"], firms_world["beta"])


def test_owner_and_staff_rows_are_not_tied_to_a_firm(firms_world):
    staff = User.objects.create_user("staff@x.example")
    check_now()

    CompanyAccess.objects.create(user=staff, company=firms_world["gamma"], role=CompanyAccess.STAFF)
    CompanyAccess.objects.create(user=staff, company=firms_world["acme"], role=CompanyAccess.STAFF)


@pytest.mark.django_db(transaction=True)
def test_a_wrong_row_is_refused_at_commit_even_without_asking_for_an_early_check():
    alpha, beta = services.create_firm("Alpha Accounting"), services.create_firm("Beta Books")
    accountant = services.create_accountant("acc@alpha.example", alpha)
    gamma = services.create_company_with_owner("Gamma Holdings", "owner@gamma.example", PASSWORD, beta)

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.create(user=accountant, company=gamma, role=CompanyAccess.ACCOUNTANT)

    assert not CompanyAccess.objects.filter(user=accountant, company=gamma).exists()


def test_the_checks_are_deferred_constraint_triggers_so_a_move_can_take_its_steps_in_any_order(db):
    rows = query(
        "SELECT t.tgname, c.relname, t.tgdeferrable, t.tginitdeferred FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
        "WHERE t.tgname LIKE 'accountant_rows_match_firm_%%' ORDER BY c.relname"
    )

    assert rows == [
        ("accountant_rows_match_firm_company", "core_company", True, True),
        ("accountant_rows_match_firm_access", "core_companyaccess", True, True),
        ("accountant_rows_match_firm_user", "core_user", True, True),
    ]


def test_the_check_is_a_closed_definer_function(db):
    assert query("SELECT has_function_privilege('web', 'accountant_rows_match_firm()', 'EXECUTE')") == [(False,)]


def test_the_migration_refuses_existing_rows_that_break_the_rule(firms_world):
    with connection.cursor() as cursor:
        cursor.execute("SET session_replication_role = replica")  # triggers do not fire: how a bad row could already exist
        CompanyAccess.objects.create(user=firms_world["alpha_accountant"], company=firms_world["gamma"], role=CompanyAccess.ACCOUNTANT)
        cursor.execute("SET session_replication_role = origin")
        with pytest.raises(DatabaseError, match="existing"), transaction.atomic():
            cursor.execute(accountant_triggers.CHECK_EXISTING_ROWS)
    CompanyAccess.objects.filter(user=firms_world["alpha_accountant"], company=firms_world["gamma"]).delete()  # the test's own mess


def test_the_migration_reverses_and_applies_again(firms_world):
    with connection.cursor() as cursor:
        for statement in accountant_triggers.REVERSE:
            cursor.execute(statement)
        assert query("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'accountant_rows_match_firm_%%'") == [(0,)]
        for statement in accountant_triggers.FORWARD:
            cursor.execute(statement)

    assert query("SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'accountant_rows_match_firm_%%'") == [(3,)]


def test_an_accountant_row_for_a_user_whose_firm_matches_but_who_is_not_an_accountant_is_refused(firms_world):
    colleague = User.objects.create_user("clerk@alpha.example", firm=firms_world["alpha"])  # same firm, not an accountant
    check_now()

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.create(user=colleague, company=firms_world["acme"], role=CompanyAccess.ACCOUNTANT)


def test_turning_a_staff_row_into_an_accountant_row_or_repointing_one_is_checked(firms_world):
    staff = User.objects.create_user("staff@x.example", firm=firms_world["beta"])
    row = CompanyAccess.objects.create(user=staff, company=firms_world["acme"], role=CompanyAccess.STAFF)
    check_now()

    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.filter(pk=row.pk).update(role=CompanyAccess.ACCOUNTANT)
    own = CompanyAccess.objects.get(user=firms_world["alpha_accountant"], company=firms_world["acme"])
    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.filter(pk=own.pk).update(user=firms_world["beta_accountant"])
    with pytest.raises(DatabaseError, match="firm"), transaction.atomic():
        CompanyAccess.objects.filter(pk=own.pk).update(company=firms_world["gamma"])


@pytest.mark.django_db(transaction=True)
def test_a_check_in_progress_holds_the_rows_so_a_concurrent_move_cannot_slip_past_it():
    import psycopg

    alpha, beta = services.create_firm("Alpha Accounting"), services.create_firm("Beta Books")
    accountant = services.create_accountant("acc@alpha.example", alpha)
    company = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, beta)
    services.move_company(company, alpha)  # a valid row now exists, so remove it to write it again below
    CompanyAccess.objects.filter(user=accountant, company=company).delete()
    settings = connection.settings_dict
    dsn = f"dbname={settings['NAME']} user={settings['USER']} password={settings['PASSWORD']} host={settings['HOST']} port={settings['PORT'] or 5432}"

    writer, mover = psycopg.connect(dsn), psycopg.connect(dsn)
    try:
        writer.execute("SET CONSTRAINTS ALL IMMEDIATE")  # the check runs as the row is written, and holds what it read
        writer.execute(
            "INSERT INTO core_companyaccess (user_id, company_id, role, created_at) VALUES (%s, %s, 'accountant', now())",
            [accountant.pk, company.pk],
        )
        mover.execute("SET lock_timeout = '500ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            mover.execute("UPDATE core_company SET firm_id = %s WHERE id = %s", [beta.pk, company.pk])
        mover.rollback()
        writer.commit()

        # Once the row is committed, the same move is refused by the check instead of slipping past it.
        mover.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(psycopg.errors.RaiseException, match="firm"):
            mover.execute("UPDATE core_company SET firm_id = %s WHERE id = %s", [beta.pk, company.pk])
    finally:
        writer.close()
        mover.close()


@pytest.mark.django_db(transaction=True)
def test_creating_an_accountant_while_their_firms_company_moves_waits_its_turn_and_never_deadlocks():
    import threading
    import time

    alpha, beta = services.create_firm("Alpha Accounting"), services.create_firm("Beta Books")
    company = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, alpha)
    outcome = {}

    def move():
        try:
            services.move_company(company, beta)
        except Exception as error:  # reported on the main thread
            outcome["error"] = error
        finally:
            connection.close()

    with transaction.atomic():
        services.create_accountant("new@alpha.example", alpha)  # holds Alpha's row; its check runs at commit
        mover = threading.Thread(target=move)
        mover.start()
        for _ in range(50):  # wait until the move is queued behind us
            waiting = query("SELECT count(*) FROM pg_locks WHERE NOT granted")  # live, unlike pg_stat_activity inside a transaction
            if waiting[0][0]:
                break
            time.sleep(0.1)
        assert waiting[0][0], f"the move should be waiting for the firm, not running ahead of us: {outcome}"
    mover.join(timeout=20)  # the commit above ran the check and let the move go ahead

    assert not mover.is_alive() and "error" not in outcome
    company.refresh_from_db()
    assert company.firm == beta
    assert accountant_rows(company) == set()  # Beta has no accountant yet; Alpha's rows are gone


@pytest.mark.django_db(transaction=True)
def test_two_moves_of_the_same_company_queued_together_apply_one_and_report_the_other():
    import threading
    import time

    alpha, beta, gamma = (services.create_firm(name) for name in ["Alpha Accounting", "Beta Books", "Gamma Group"])
    company = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, alpha)
    outcomes = []

    def move(target):
        try:
            services.move_company(company, target)
            outcomes.append(("moved", target.name))
        except services.CompanyChanged:
            outcomes.append(("changed", target.name))
        finally:
            connection.close()

    with transaction.atomic():
        services.create_accountant("new@alpha.example", alpha)  # holds Alpha, so both moves queue behind it
        movers = [threading.Thread(target=move, args=(target,)) for target in (beta, gamma)]
        for mover in movers:
            mover.start()
        for _ in range(50):
            if query("SELECT count(*) FROM pg_locks WHERE NOT granted")[0][0] >= 2:
                break
            time.sleep(0.1)
    for mover in movers:
        mover.join(timeout=20)

    assert sorted(kind for kind, _ in outcomes) == ["changed", "moved"]
    [(_, winner)] = [outcome for outcome in outcomes if outcome[0] == "moved"]
    company.refresh_from_db()
    assert company.firm.name == winner
