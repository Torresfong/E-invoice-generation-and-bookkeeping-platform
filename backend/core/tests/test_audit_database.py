"""The audit trail's guarantees at the database seam: who can write, change and read what."""

import pytest
from django.db import DatabaseError, connection, transaction

from core import services
from core.audit import audit_trigger_sql
from core.models import AuditEntry, FirmAuditEntry
from core.rls import tenant_policy_sql

from .factories import make_user
from .test_tenancy import query


@pytest.fixture
def entries(world):
    acme_entry = AuditEntry.objects.create(company=world["acme"], actor_id=1, actor_email="a@x", action="create")
    beta_entry = AuditEntry.objects.create(company=world["beta"], actor_id=2, actor_email="b@x", action="create")
    return acme_entry, beta_entry


def test_the_web_role_can_read_only_its_own_companies_entries(as_user, world, entries):
    with as_user(world["acme_owner"]):
        assert query("SELECT count(*) FROM core_auditentry") == [(1,)]
    with as_user(world["accountant"]):
        assert query("SELECT count(*) FROM core_auditentry") == [(2,)]


ADD_ENTRY = (
    "INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields) "
    "VALUES (%s, %s, %s, 'create', '', '', '[]') RETURNING id"
)


def test_the_web_role_can_add_an_entry_for_its_own_company_as_itself(as_user, world):
    owner = world["acme_owner"]
    with as_user(owner):
        assert query(ADD_ENTRY, [world["acme"].pk, owner.pk, owner.email])


def test_the_web_role_cannot_add_an_entry_for_another_company_or_none(as_user, world):
    owner = world["acme_owner"]
    with as_user(owner):
        for company in [world["beta"].pk, None]:
            with pytest.raises(DatabaseError), transaction.atomic():
                query(ADD_ENTRY, [company, owner.pk, owner.email])


@pytest.mark.parametrize(
    "actor, email",
    [("staff", "owner@acme.example"), ("owner", "staff@acme.example"), ("owner", "forged@example.com"), (None, "")],
)
def test_the_web_role_cannot_write_an_entry_in_someone_elses_name(as_user, world, actor, email):
    staff, owner = world["acme_staff"], world["acme_owner"]
    actor_id = {"staff": owner.pk, "owner": owner.pk, None: None}[actor]
    with as_user(staff), pytest.raises(DatabaseError), transaction.atomic():
        query(ADD_ENTRY, [world["acme"].pk, actor_id, email])


def test_an_entry_cannot_be_back_dated(as_user, world):
    owner = world["acme_owner"]
    with as_user(owner):
        query(
            "INSERT INTO core_auditentry (company_id, actor_id, actor_email, action, target_type, target_id, changed_fields, occurred_at) "
            "VALUES (%s, %s, %s, 'create', '', '', '[]', '2001-01-01') RETURNING id",
            [world["acme"].pk, owner.pk, owner.email],
        )

    assert AuditEntry.objects.get().occurred_at.year >= 2026


def test_a_signed_in_user_cannot_record_a_sign_in_event_in_another_accounts_name(as_user, world):
    with as_user(world["acme_staff"]):
        with pytest.raises(DatabaseError), transaction.atomic():
            query("SELECT record_auth_event('login_failed', %s)", [world["accountant"].email])
        with pytest.raises(DatabaseError), transaction.atomic():
            query("SELECT record_auth_event('login', 'nobody@nowhere.example')")

    assert AuditEntry.objects.count() == 0


# A sign-in event is written without a signed-in user only for a failed sign-in, and only against a counted attempt (D54).

SIGN_IN_ADDRESSES = ["owner@acme.example", "staff@acme.example", "accountant@firm.example"]


@pytest.mark.parametrize("email", SIGN_IN_ADDRESSES)
def test_a_bare_call_cannot_record_a_failed_sign_in_for_an_address(world, email):
    query("SELECT record_auth_event('login_failed', %s)", [email])

    assert AuditEntry.objects.count() == 0 and FirmAuditEntry.objects.count() == 0


@pytest.mark.parametrize("action", ["login", "logout"])
@pytest.mark.parametrize("email", SIGN_IN_ADDRESSES)
def test_a_bare_call_cannot_record_a_sign_in_or_sign_out_for_an_address(world, action, email):
    query("SELECT start_login_attempt(%s, interval '15 minutes', 5)", [email])
    query("SELECT record_auth_event(%s, %s)", [action, email])

    assert AuditEntry.objects.count() == 0 and FirmAuditEntry.objects.count() == 0


@pytest.mark.parametrize("action", ["login", "login_failed", "logout"])
def test_a_bare_call_cannot_record_an_event_for_a_firm_administrator(world, action):
    administrator = services.create_firm_administrator("admin@firm.example", world["accountant"].firm)
    query("SELECT start_login_attempt(%s, interval '15 minutes', 5)", [administrator.email])
    if action == "login_failed":
        query("SELECT clear_login_failures(%s)", [administrator.email])
    query("SELECT record_auth_event(%s, %s)", [action, administrator.email])

    assert not FirmAuditEntry.objects.filter(action__in=["login", "login_failed", "logout"]).exists()


def test_one_counted_attempt_yields_one_failed_sign_in_entry(world):
    query("SELECT start_login_attempt(%s, interval '15 minutes', 5)", ["staff@acme.example"])
    query("SELECT record_auth_event('login_failed', 'staff@acme.example')")
    query("SELECT record_auth_event('login_failed', 'staff@acme.example')")

    assert AuditEntry.objects.filter(action=AuditEntry.LOGIN_FAILED).count() == 1


def test_a_counted_attempt_for_one_address_is_no_proof_for_another(world):
    query("SELECT start_login_attempt(%s, interval '15 minutes', 5)", ["staff@acme.example"])
    query("SELECT record_auth_event('login_failed', 'owner@acme.example')")

    assert AuditEntry.objects.count() == 0


def test_a_signed_in_user_can_still_record_their_own_sign_out(as_user, world):
    with as_user(world["acme_staff"]):
        query("SELECT record_auth_event('logout', NULL)")

    assert AuditEntry.objects.get().actor_email == "staff@acme.example"


def test_enable_audit_refuses_a_table_without_a_company_column(db):
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE no_company (id bigint PRIMARY KEY, body text)")
        with pytest.raises(DatabaseError, match="needs id and company_id"), transaction.atomic():
            cursor.execute(audit_trigger_sql("no_company")[0])


def test_a_row_moved_to_another_company_leaves_a_trace_in_both(world):
    with connection.cursor() as cursor:
        cursor.execute(
            "CREATE TABLE probe_move (id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
            "company_id bigint NOT NULL REFERENCES core_company(id), body text)"
        )
        for statement in [*tenant_policy_sql("probe_move"), *audit_trigger_sql("probe_move")]:
            cursor.execute(statement)
        cursor.execute("INSERT INTO probe_move (company_id, body) VALUES (%s, 'x') RETURNING id", [world["acme"].pk])
        cursor.execute("UPDATE probe_move SET company_id = %s", [world["beta"].pk])

    updates = AuditEntry.objects.filter(target_type="probe_move", action="update")
    assert sorted(updates.values_list("company_id", flat=True)) == sorted([world["acme"].pk, world["beta"].pk])


@pytest.mark.parametrize("statement", ["UPDATE core_auditentry SET action = 'login'", "DELETE FROM core_auditentry", "TRUNCATE core_auditentry"])
def test_the_web_role_cannot_change_delete_or_truncate_entries(as_user, world, entries, statement):
    with as_user(world["acme_owner"]), pytest.raises(DatabaseError), transaction.atomic():
        query(statement)


def test_even_the_owner_connection_cannot_rewrite_or_delete_an_entry(world, entries):
    with pytest.raises(DatabaseError, match="cannot be changed or deleted"), transaction.atomic():
        AuditEntry.objects.update(action="login")
    with pytest.raises(DatabaseError, match="cannot be changed or deleted"), transaction.atomic():
        AuditEntry.objects.all().delete()

    assert AuditEntry.objects.count() == 2


def test_only_sign_in_events_can_be_recorded_through_the_sign_in_function(as_user, world):
    with as_user(world["acme_owner"]), pytest.raises(DatabaseError), transaction.atomic():
        query("SELECT record_auth_event('create', NULL)")


def test_the_sign_in_function_writes_an_accountants_event_once_into_the_firm_trail_and_into_no_company(as_user, world):
    with as_user(world["accountant"]):
        query("SELECT record_auth_event('login', NULL)")

    assert AuditEntry.objects.count() == 0
    assert list(FirmAuditEntry.objects.values_list("firm_id", flat=True)) == [world["accountant"].firm_id]


def test_the_sign_in_function_writes_one_entry_per_company_for_a_client(as_user, world):
    with as_user(world["acme_owner"]):
        query("SELECT record_auth_event('login', NULL)")

    assert list(AuditEntry.objects.values_list("company_id", flat=True)) == [world["acme"].pk]


def test_an_unknown_address_writes_nothing(world):
    query("SELECT record_auth_event('login_failed', 'nobody@nowhere.example')")

    assert AuditEntry.objects.count() == 0


def test_an_account_with_no_company_writes_nothing(db):
    make_user("loner@example.com")
    query("SELECT record_auth_event('login', 'loner@example.com')")

    assert AuditEntry.objects.count() == 0


def test_a_staff_session_at_the_database_can_read_the_whole_company_trail_but_nothing_more(as_user, world):
    """Known limit, pinned on purpose: 'staff read only their own activity' is the permission service.

    Row-level security isolates companies, not roles, so SQL run as `web` in a staff session sees every
    entry of that company (D40 (3)). If a role rule is ever added to the policy, this test is the one to change.
    """
    owner, staff = world["acme_owner"], world["acme_staff"]
    AuditEntry.objects.create(company=world["acme"], actor_id=owner.pk, actor_email=owner.email, action="create")
    AuditEntry.objects.create(company=world["acme"], actor_id=staff.pk, actor_email=staff.email, action="create")
    AuditEntry.objects.create(company=world["beta"], actor_id=9, actor_email="b@x", action="create")

    with as_user(staff):
        seen = {actor for (actor,) in query("SELECT actor_email FROM core_auditentry")}
        with pytest.raises(DatabaseError), transaction.atomic():
            query("UPDATE core_auditentry SET action = 'login'")

    assert seen == {owner.email, staff.email}
