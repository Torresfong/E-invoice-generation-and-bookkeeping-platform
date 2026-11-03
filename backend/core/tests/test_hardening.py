"""Tests added after the independent security review of PR #68: they pin the database-level guarantees."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from core import services, throttle
from core.models import AuthToken, CompanyAccess

from .factories import make_company, make_member, make_user
from .test_tenancy import query, role_migration

ARGON2 = "argon2$argon2id$v=19$m=102400,t=2,p=8$c29tZXNhbHQ$aGFzaGhhc2hoYXNoaGFzaA"
HASH = "f" * 64


def database_now():
    """The database's own clock: the functions cap expiry against it, and it can differ slightly from this machine's."""
    return query("SELECT now()")[0][0]


def test_every_spelling_the_lookup_treats_as_one_address_shares_one_lock(db):
    make_user("acc@firm.test")
    candidates = ["acc@firm.test", "ACC@FIRM.TEST", "Acc@Firm.Test", "acc@fİrm.test", "acc@fırm.test"]
    same_account = [c for c in candidates if query("SELECT count(*) FROM auth_lookup(%s)", [c]) == [(1,)]]
    assert len(same_account) >= 3

    allowed = sum(throttle.allow_attempt(same_account[n % len(same_account)]) for n in range(12))

    assert allowed == 5


def test_an_accountant_created_before_any_company_gets_access_to_companies_created_later(db, fake_ports):
    firm = services.create_firm("Alpha Accounting")
    call_command("create_accountant", "accountant@firm.example", "Alpha Accounting")

    services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", "correct horse battery", firm)
    services.create_company_with_owner("Beta Trading", "owner@beta.example", "correct horse battery", firm)

    accountant_rows = CompanyAccess.objects.filter(user__email="accountant@firm.example")
    assert accountant_rows.count() == 2
    assert set(accountant_rows.values_list("role", flat=True)) == {CompanyAccess.ACCOUNTANT}


def test_nobody_is_marked_as_an_accountant_by_being_a_member_or_owner(db):
    owner = make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)
    make_member("staff@acme.example", owner.company_access.get().company, CompanyAccess.STAFF)

    assert not owner.is_accountant
    assert query("SELECT count(*) FROM core_user WHERE is_accountant") == [(0,)]


def test_every_security_definer_function_pins_a_search_path_that_starts_with_pg_catalog(db):
    rows = query(
        "SELECT p.proname, p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.prosecdef"
    )

    assert len(rows) == 15  # a new definer function must be added here on purpose
    assert {tuple(config) for _, config in rows} == {("search_path=pg_catalog, public, pg_temp",)}


def test_no_security_definer_function_can_be_executed_by_public(db):
    open_to_public = query(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.prosecdef AND (p.proacl IS NULL OR EXISTS "
        "(SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0))"
    )

    assert open_to_public == []


def test_a_reset_link_cannot_be_given_a_longer_life_than_an_hour(db):
    make_user("owner@acme.example")

    query("SELECT create_password_reset(%s, %s, now() + interval '100 years')", [HASH, "owner@acme.example"])

    assert AuthToken.objects.get().expires_at <= database_now() + timedelta(hours=1)


def test_an_invitation_cannot_be_given_a_longer_life_than_a_week(as_user, db):
    owner = make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)
    company = owner.company_access.get().company

    with as_user(owner):
        query("SELECT create_invitation(%s, %s, %s, now() + interval '100 years')", [HASH, "x@acme.example", company.pk])

    assert AuthToken.objects.get().expires_at <= database_now() + timedelta(days=7)


@pytest.mark.parametrize(
    "call",
    [
        "SELECT complete_password_reset(%s, 'pbkdf2_sha256$1$salt$hash')",
        "SELECT accept_invitation(%s, 'plaintext-password')",
    ],
)
def test_a_password_that_is_not_an_argon2_hash_is_refused_and_the_link_survives(db, call):
    owner = make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)
    purpose = AuthToken.INVITE if "accept" in call else AuthToken.RESET
    AuthToken.objects.create(
        purpose=purpose, token_hash=HASH, email="x@acme.example", company=owner.company_access.get().company,
        role=CompanyAccess.STAFF if purpose == AuthToken.INVITE else "", user=owner if purpose == AuthToken.RESET else None,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    with pytest.raises(DatabaseError, match="Argon2"), transaction.atomic():
        query(call, [HASH])

    assert AuthToken.objects.get().used_at is None


def test_a_good_argon2_hash_is_accepted_by_the_reset_function(db):
    owner = make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)
    AuthToken.objects.create(
        purpose=AuthToken.RESET, token_hash=HASH, email=owner.email, user=owner,
        expires_at=timezone.now() + timedelta(hours=1),
    )

    assert query("SELECT complete_password_reset(%s, %s)", [HASH, ARGON2]) == [(owner.pk,)]


def test_old_expired_tokens_are_removed_when_a_new_one_is_created(db):
    owner = make_user("owner@acme.example")
    for number in range(3):
        AuthToken.objects.create(
            purpose=AuthToken.RESET, token_hash=f"{number}" * 64, email="old@acme.example", user=owner,
            expires_at=timezone.now() - timedelta(days=40),
        )
    recent = AuthToken.objects.create(
        purpose=AuthToken.RESET, token_hash="a" * 64, email="recent@acme.example", user=owner,
        expires_at=timezone.now() - timedelta(days=2),
    )

    query("SELECT create_password_reset(%s, %s, now() + interval '1 hour')", ["b" * 64, owner.email])

    assert set(AuthToken.objects.values_list("token_hash", flat=True)) == {recent.token_hash, "b" * 64}


def test_the_web_role_must_not_be_a_member_of_any_other_role(db):
    with connection.cursor() as cursor:
        cursor.execute("CREATE ROLE secrev_parent NOLOGIN")
        cursor.execute("GRANT secrev_parent TO web")
        with pytest.raises(DatabaseError, match="member of other roles"), transaction.atomic():
            cursor.execute(role_migration.VERIFY_ROLE)
