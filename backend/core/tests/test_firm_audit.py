"""The firm audit trail (D50): firm-level events in a second append-only trail, apart from every company's trail."""

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction

from core import audit, jobs
from core.models import AuditEntry, FirmAuditEntry, User

from .factories import PASSWORD
from .test_firms import world  # noqa: F401  (two firms, each with an accountant and a company)
from .test_tenancy import query


def sign_in(client, user, password=PASSWORD):
    return client.post("/login/", {"email": user.email, "password": password})


def firm_trail(firm):
    return FirmAuditEntry.objects.filter(firm=firm)


def sign_in_entries():
    return FirmAuditEntry.objects.filter(action__in=[AuditEntry.LOGIN, AuditEntry.LOGIN_FAILED, AuditEntry.LOGOUT])


# --- sign-ins -----------------------------------------------------------------------------------------------


def test_an_accountants_sign_in_and_sign_out_are_in_their_firms_trail_and_in_no_companys(
    client, world, as_production_connection
):
    accountant = world["alpha_accountant"]

    with as_production_connection():
        sign_in(client, accountant)
        client.post("/logout/")
        sign_in(client, accountant, "not the password")

    entries = sign_in_entries().filter(firm=world["alpha"]).order_by("id")
    assert [(e.action, e.actor_id, e.actor_email) for e in entries] == [
        (AuditEntry.LOGIN, accountant.pk, "acc@alpha.example"),
        (AuditEntry.LOGOUT, accountant.pk, "acc@alpha.example"),
        (AuditEntry.LOGIN_FAILED, accountant.pk, "acc@alpha.example"),
    ]
    assert not sign_in_entries().filter(firm=world["beta"]).exists()
    assert not AuditEntry.objects.filter(actor_id=accountant.pk).exists()


def test_an_owners_sign_in_stays_in_the_company_trail_and_not_in_the_firm_trail(client, world, as_production_connection):
    owner = User.objects.get(email="owner@acme.example")

    with as_production_connection():
        sign_in(client, owner)

    assert AuditEntry.objects.filter(company=world["acme"], action=AuditEntry.LOGIN).count() == 1
    assert not sign_in_entries().exists()


# --- who can read and write the trail -----------------------------------------------------------------------

ADD_ENTRY = (
    "INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields) "
    "VALUES (%s, %s, %s, 'create', '', '', '[]') RETURNING id"
)


@pytest.fixture
def entries(world):  # noqa: F811
    for firm in (world["alpha"], world["beta"]):
        FirmAuditEntry.objects.create(firm=firm, actor_id=1, actor_email="a@x", action="create", target_type="probe")


def test_a_user_reads_only_their_own_firms_entries_and_a_client_reads_none(world, entries, as_user):  # noqa: F811
    with as_user(world["alpha_accountant"]):
        assert query("SELECT DISTINCT firm_id FROM core_firmauditentry") == [(world["alpha"].pk,)]
    with as_user(world["beta_accountant"]):
        assert query("SELECT DISTINCT firm_id FROM core_firmauditentry") == [(world["beta"].pk,)]
    with as_user(User.objects.get(email="owner@acme.example")):
        assert query("SELECT count(*) FROM core_firmauditentry") == [(0,)]


def test_a_user_can_add_an_entry_for_their_own_firm_as_themselves(world, as_user):  # noqa: F811
    accountant = world["alpha_accountant"]
    with as_user(accountant):
        assert query(ADD_ENTRY, [world["alpha"].pk, accountant.pk, accountant.email])


@pytest.mark.parametrize(
    "firm, actor, email",
    [
        ("beta", "alpha_accountant", "acc@alpha.example"),  # another firm's trail
        ("alpha", "beta_accountant", "acc@alpha.example"),  # someone else's id
        ("alpha", "alpha_accountant", "acc@beta.example"),  # someone else's address
        ("alpha", "alpha_accountant", "forged@example.com"),
        ("alpha", None, ""),  # no actor at all
    ],
)
def test_a_user_cannot_add_an_entry_for_another_firm_or_in_someone_elses_name(world, as_user, firm, actor, email):  # noqa: F811
    actor_id = world[actor].pk if actor else None
    with as_user(world["alpha_accountant"]), pytest.raises(DatabaseError), transaction.atomic():
        query(ADD_ENTRY, [world[firm].pk, actor_id, email])


def test_an_entry_cannot_be_changed_or_deleted_by_the_web_role_or_anyone_else(world, entries, as_user):  # noqa: F811
    before = FirmAuditEntry.objects.count()
    with as_user(world["alpha_accountant"]):
        for statement in ["UPDATE core_firmauditentry SET action = 'delete'", "DELETE FROM core_firmauditentry"]:
            with pytest.raises(DatabaseError), transaction.atomic():
                query(statement)
    with pytest.raises(DatabaseError, match="cannot be changed or deleted"), transaction.atomic():
        FirmAuditEntry.objects.all().delete()
    with pytest.raises(DatabaseError, match="cannot be changed or deleted"), transaction.atomic():
        FirmAuditEntry.objects.update(action="login")

    assert FirmAuditEntry.objects.count() == before


def test_the_time_of_an_entry_is_the_databases_not_the_callers(world, as_user):  # noqa: F811
    accountant = world["alpha_accountant"]
    with as_user(accountant):
        query(
            "INSERT INTO core_firmauditentry (firm_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at) "
            "VALUES (%s, %s, %s, 'create', '', '', '[]', '2001-01-01') RETURNING id",
            [world["alpha"].pk, accountant.pk, accountant.email],
        )

    assert FirmAuditEntry.objects.get(actor_id=accountant.pk).occurred_at.year >= 2026


# --- operator commands --------------------------------------------------------------------------------------


def test_creating_a_firm_and_an_accountant_from_the_operator_commands_writes_firm_entries_by_the_operator(db, fake_ports):
    call_command("create_firm", "Gamma Group", "boss@gamma.example")
    call_command("create_accountant", "new@gamma.example", "Gamma Group")

    entries = list(FirmAuditEntry.objects.order_by("id"))
    assert [(e.action, e.target_type, e.actor_id, e.actor_email, e.changed_fields) for e in entries] == [
        (AuditEntry.CREATE, "firm", None, "operator", ["name"]),
        (AuditEntry.CREATE, "user", None, "operator", ["email", "firm"]),  # the first firm administrator
        (AuditEntry.CREATE, "user", None, "operator", ["email", "firm"]),
    ]
    assert {e.firm.name for e in entries} == {"Gamma Group"}
    assert entries[0].target_id == str(entries[0].firm_id)
    assert entries[2].target_id == str(User.objects.get(email="new@gamma.example").pk)


def test_moving_a_company_writes_the_company_trail_and_both_firms_trails_with_one_correlation_id(world):  # noqa: F811
    before = FirmAuditEntry.objects.count()

    call_command("move_company", world["acme"].pk, "Beta Books")

    moved = FirmAuditEntry.objects.order_by("id")[before:]
    assert sorted(e.firm_id for e in moved) == sorted([world["alpha"].pk, world["beta"].pk])
    for entry in moved:
        assert (entry.action, entry.target_type, entry.target_id, entry.actor_email, entry.changed_fields) == (
            AuditEntry.UPDATE, "company", str(world["acme"].pk), "operator", ["firm"],
        )
    assert len({e.correlation_id for e in moved}) == 1
    assert AuditEntry.objects.filter(company=world["acme"], action=AuditEntry.UPDATE, changed_fields=["firm"]).count() == 1


def test_moving_a_company_to_its_own_firm_writes_no_firm_entry(world):  # noqa: F811
    before = FirmAuditEntry.objects.count()

    call_command("move_company", world["acme"].pk, "Alpha Accounting")

    assert FirmAuditEntry.objects.count() == before


def test_two_moves_have_different_correlation_ids(world):  # noqa: F811
    call_command("move_company", world["acme"].pk, "Beta Books")
    call_command("move_company", world["acme"].pk, "Alpha Accounting")

    ids = FirmAuditEntry.objects.filter(target_type="company").values_list("correlation_id", flat=True)
    assert len(set(ids)) == 2 and len(ids) == 4


# --- no mixing with a company's trail -----------------------------------------------------------------------


def test_no_firm_entry_appears_in_a_companys_trail_page(client, world, as_production_connection):  # noqa: F811
    owner = User.objects.get(email="owner@acme.example")
    other = type(client)()
    with as_production_connection():
        sign_in(other, world["alpha_accountant"])
        sign_in(client, owner)
        page = client.get(f"/companies/{world['acme'].pk}/audit/").content.decode()

    assert firm_trail(world["alpha"]).filter(actor_email="acc@alpha.example").exists()
    assert "acc@alpha.example" not in page


# --- correlation ids ----------------------------------------------------------------------------------------


def test_one_request_has_one_correlation_id_for_all_its_firm_entries_and_the_next_request_another(
    client, world, settings, as_production_connection  # noqa: F811
):
    settings.ROOT_URLCONF = "core.tests.urls_probe"
    accountant = world["alpha_accountant"]
    with as_production_connection():
        sign_in(client, accountant)
        client.post("/firm-events/")
        client.post("/firm-events/")

    ids = [e.correlation_id for e in FirmAuditEntry.objects.filter(target_type="probe").order_by("id")]
    assert len(ids) == 4 and ids[0] == ids[1] and ids[2] == ids[3] and ids[1] != ids[2]


def test_a_job_has_one_correlation_id_for_everything_it_writes_and_the_next_job_another(db):
    seen = []

    @jobs.register("probe.correlation")
    def handler(**_):
        seen.append((audit.current_correlation_id(), audit.current_correlation_id()))

    try:
        jobs.enqueue("probe.correlation")
        jobs.enqueue("probe.correlation")
        jobs.run_pending()
    finally:
        jobs.HANDLERS.pop("probe.correlation")

    (first, first_again), (second, second_again) = seen
    assert first == first_again and second == second_again and first != second


# --- tables that carry a firm id ----------------------------------------------------------------------------


def test_a_table_with_a_firm_id_records_changes_as_field_names_never_values(world):  # noqa: F811
    secret = "SST registration 12-3456-7890"
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE probe_firm_note (id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
            "firm_id bigint NOT NULL REFERENCES core_firm(id), body text NOT NULL)"
        )
        for statement in audit.firm_audit_trigger_sql("probe_firm_note"):
            cursor.execute(statement)
        cursor.execute("INSERT INTO probe_firm_note (firm_id, body) VALUES (%s, 'first') RETURNING id", [world["alpha"].pk])
        note_id = cursor.fetchone()[0]
        cursor.execute("UPDATE probe_firm_note SET body = %s WHERE id = %s", [secret, note_id])
        cursor.execute("DELETE FROM probe_firm_note WHERE id = %s", [note_id])

    entries = list(firm_trail(world["alpha"]).filter(target_type="probe_firm_note").order_by("id"))
    assert [(e.action, e.target_id, e.changed_fields) for e in entries] == [
        (AuditEntry.CREATE, str(note_id), []),
        (AuditEntry.UPDATE, str(note_id), ["body"]),
        (AuditEntry.DELETE, str(note_id), []),
    ]
    assert secret not in repr(list(FirmAuditEntry.objects.values()))


def test_enable_firm_audit_refuses_a_table_without_a_firm_column(db):
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE no_firm (id bigint PRIMARY KEY, body text)")
        with pytest.raises(DatabaseError, match="needs id and firm_id"), transaction.atomic():
            cursor.execute(audit.firm_audit_trigger_sql("no_firm")[0])


def test_a_user_who_sets_a_bad_correlation_id_can_still_write_their_entries(world, as_user):  # noqa: F811
    accountant = world["alpha_accountant"]
    with as_user(accountant):
        query("SELECT set_config('app.correlation_id', 'not-a-uuid', false)")
        assert query(ADD_ENTRY, [world["alpha"].pk, accountant.pk, accountant.email])
        query("SELECT set_config('app.correlation_id', '', false)")
