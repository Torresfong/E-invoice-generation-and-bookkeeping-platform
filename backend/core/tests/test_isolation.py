"""Cross-role isolation and exposure (ticket 02e): no role reaches a company it has no access to, and the schema
cannot grow a business table that skips the company id or row-level security.

Three guards keep this suite honest as the product grows:
- every route that carries a company id must be in `ENDPOINTS` below, so a new screen cannot be left untested;
- every table that is not listed as Django's own or as a platform table must carry a company id and forced RLS;
- the guards themselves are shown to fail on a bad route list and on bad tables, so they cannot pass by default.
"""

import pytest
from django.db import DatabaseError, connection
from django.test import Client
from django.urls import get_resolver

from core.models import AuditEntry, AuthToken, CompanyAccess, User

from .factories import PASSWORD, make_member
from .probe import create_probe_note
from .test_tenancy import query


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


@pytest.fixture
def people(firms_world):
    """`firms_world` plus a staff member in each company. Returns every actor by name."""
    return {
        **firms_world,
        "acme_owner": User.objects.get(email="owner@acme.example"),
        "gamma_owner": User.objects.get(email="owner@gamma.example"),
        "acme_staff": make_member("staff@acme.example", firms_world["acme"], CompanyAccess.STAFF),
        "gamma_staff": make_member("staff@gamma.example", firms_world["gamma"], CompanyAccess.STAFF),
    }


# --- every company endpoint, for every role that lacks access to the company ---------------------------------------

# Every route that carries a company id. `GET` reads; the invitation is the one write there is on a company today.
ENDPOINTS = [
    ("GET", "/companies/{pk}/", "companies/<int:pk>/"),
    ("GET", "/companies/{pk}/audit/", "companies/<int:pk>/audit/"),
    ("GET", "/companies/{pk}/activity/", "companies/<int:pk>/activity/"),
    ("POST", "/companies/{pk}/einvoicing/", "companies/<int:pk>/einvoicing/"),
    ("POST", "/companies/{pk}/invite/", "companies/<int:pk>/invite/"),
]

# (who asks, the company they have no access to). An accountant lacks access to the other firm's companies (D49).
OUTSIDERS = [
    ("acme_owner", "gamma"),
    ("acme_staff", "gamma"),
    ("alpha_accountant", "gamma"),
    ("gamma_owner", "acme"),
    ("gamma_staff", "acme"),
    ("beta_accountant", "acme"),
]


@pytest.mark.parametrize("method, path", [(method, path) for method, path, _ in ENDPOINTS])
@pytest.mark.parametrize("who, target", OUTSIDERS)
def test_a_company_the_role_has_no_access_to_cannot_be_read_or_written_through_any_company_endpoint(
    people, fake_ports, as_production_connection, who, target, method, path
):
    company, outsider = people[target], people[who]
    tokens_before = AuthToken.objects.count()

    with as_production_connection():
        client = Client()
        sign_in(client, outsider)
        response = client.generic(method, path.format(pk=company.pk), {"email": "new@x.example"} if method == "POST" else "")

    body = response.content.decode()
    assert response.status_code == 404
    assert company.name not in body and "owner@" + target not in body
    assert fake_ports.email.sent == [] and AuthToken.objects.count() == tokens_before
    assert not AuditEntry.objects.filter(company=company, actor_id=outsider.pk).exists()


def company_routes(patterns=None, prefix=""):
    for pattern in patterns if patterns is not None else get_resolver("config.urls").url_patterns:
        route = prefix + str(pattern.pattern)
        if hasattr(pattern, "url_patterns"):
            yield from company_routes(pattern.url_patterns, route)
        elif route.startswith("companies/") and "<int:pk>" in route:
            yield route


def missing_from(endpoints, routes):
    return sorted(set(routes) - {route for _, _, route in endpoints})


def test_every_company_route_is_in_the_isolation_matrix_and_nothing_in_it_is_stale():
    """A company id in the path under /companies/. A company id passed in a body is not visible to this walk; those
    are covered where they are written (the job queue in test_job_tenancy.py, the probe table below)."""
    assert sorted(route for _, _, route in ENDPOINTS) == sorted(company_routes())


def test_the_route_guard_fails_when_a_screen_is_left_out_of_the_matrix():
    assert missing_from(ENDPOINTS[:-1], company_routes()) == ["companies/<int:pk>/invite/"]


# --- create, update and delete on a business table, over HTTP -------------------------------------------------------


@pytest.fixture
def notes(people, settings):
    """The probe table and its test-only URLs stand in for the business tables later tickets add."""
    settings.ROOT_URLCONF = "core.tests.urls_probe"
    with connection.cursor() as cursor:
        create_probe_note(cursor)
        cursor.execute(
            "INSERT INTO probe_note (company_id, body) VALUES (%s, 'acme note'), (%s, 'gamma note') RETURNING id",
            [people["acme"].pk, people["gamma"].pk],
        )
    ids = dict(query("SELECT body, id FROM probe_note"))
    return {"acme": ids["acme note"], "gamma": ids["gamma note"]}


def note_bodies():
    return sorted(body for (body,) in query("SELECT body FROM probe_note"))


@pytest.mark.parametrize("who, target", OUTSIDERS)
def test_a_role_cannot_create_update_or_delete_another_companys_records(people, notes, as_production_connection, who, target):
    outsider, other = people[who], people[target]
    own = "acme" if target == "gamma" else "gamma"
    client = Client()

    with as_production_connection():
        sign_in(client, outsider)
        # The same two calls on the outsider's own company's note succeed, so the URLs do work and the refusals below
        # are the database's, not a broken route.
        assert client.post(f"/notes/{notes[own]}/update/", {"body": "mine, rewritten"}).status_code == 200
        with pytest.raises(DatabaseError):
            client.post("/notes/", {"company": other.pk, "body": "planted"})
        client.post(f"/notes/{notes[target]}/update/", {"body": "rewritten"})
        client.post(f"/notes/{notes[target]}/delete/")

    # Read as the table owner (outside the production connection), so every row is visible.
    assert note_bodies() == sorted([f"{target} note", "mine, rewritten"])


@pytest.mark.parametrize("who, own", [("acme_owner", "acme"), ("acme_staff", "acme"), ("beta_accountant", "gamma")])
def test_a_role_reads_its_own_companys_records_and_not_another_companys(people, notes, as_production_connection, who, own):
    client = Client()
    with as_production_connection():
        sign_in(client, people[who])
        listing = client.get("/notes/all/").json()

    assert listing == {"bodies": [f"{own} note"]}


# --- the schema cannot grow a business table without a company id and row-level security -------------------------------

# Tables Django creates for itself. The web and worker roles are granted none of them, except the session store (the
# test in test_tenancy.py holds that line); none holds business data.
DJANGO_OWN = {
    "auth_group", "auth_group_permissions", "auth_permission", "core_user_groups", "core_user_user_permissions",
    "django_content_type", "django_migrations", "django_session",
}
# Platform tables with no company id, each kept under forced RLS by its own policies (see tenancy.md).
PLATFORM = {
    "core_company": "the tenant itself",
    "core_firm": "an accounting firm; a user reads only their own",
    "core_firmauditentry": "the firm trail, keyed by firm (D50)",
    "core_loginfailure": "throttling by hashed address; reachable through functions only",
    "core_user": "an account; a user reads only their own row",
}


def business_table_problems():
    """Every table that is not Django's own or a platform table, and what it lacks: a company id, or forced RLS."""
    problems = {}
    rows = query(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid), "
        "EXISTS (SELECT 1 FROM information_schema.columns k WHERE k.table_schema = 'public' "
        "AND k.table_name = c.relname AND k.column_name = 'company_id') "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
    )
    for table, enabled, forced, policies, has_company in rows:
        if table in DJANGO_OWN:
            continue
        checks = [("row-level security", enabled), ("FORCE row-level security", forced), ("a policy", policies > 0)]
        if table not in PLATFORM:  # a platform table has no company id but keeps the rest
            checks.insert(0, ("a company_id column", has_company))
        missing = [what for what, ok in checks if not ok]
        if missing:
            problems[table] = missing
    return problems


def test_every_business_table_has_a_company_id_and_forced_row_level_security(db):
    assert business_table_problems() == {}


def test_the_lists_of_exempt_tables_name_only_tables_that_exist(db):
    existing = {table for (table,) in query("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}

    assert (DJANGO_OWN | set(PLATFORM)) - existing == set()


@pytest.mark.parametrize(
    "create, lacks",
    [
        ("CREATE TABLE bad_t (id bigint PRIMARY KEY, body text)", ["a company_id column", "row-level security", "FORCE row-level security", "a policy"]),
        ("CREATE TABLE bad_t (id bigint PRIMARY KEY, company_id bigint)", ["row-level security", "FORCE row-level security", "a policy"]),
    ],
)
def test_the_table_guard_flags_a_table_that_lacks_a_company_id_or_row_level_security(db, create, lacks):
    with connection.cursor() as cursor:
        cursor.execute(create)

    assert business_table_problems() == {"bad_t": lacks}


def test_the_table_guard_flags_row_level_security_that_is_not_forced_or_has_no_policy(db):
    with connection.cursor() as cursor:
        cursor.execute("CREATE TABLE bad_t (id bigint PRIMARY KEY, company_id bigint)")
        cursor.execute("ALTER TABLE bad_t ENABLE ROW LEVEL SECURITY")
    assert business_table_problems() == {"bad_t": ["FORCE row-level security", "a policy"]}

    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE bad_t FORCE ROW LEVEL SECURITY")
    assert business_table_problems() == {"bad_t": ["a policy"]}

    with connection.cursor() as cursor:
        cursor.execute("CREATE POLICY p ON bad_t USING (true)")
    assert business_table_problems() == {}
