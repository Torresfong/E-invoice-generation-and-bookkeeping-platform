import pytest
from django.contrib.auth import get_user_model
from django.db import DatabaseError, IntegrityError, connection, transaction

from core.models import CompanyAccess

from .factories import make_company

User = get_user_model()


def test_a_user_is_identified_by_email_and_the_password_is_stored_as_argon2(db):
    user = User.objects.create_user("Owner@Example.com", password="correct horse battery")

    assert user.email == "owner@example.com"
    assert user.password.startswith("argon2$")
    assert user.check_password("correct horse battery")


def test_two_users_cannot_share_an_email_address_in_any_letter_case(db):
    User.objects.create_user("owner@example.com", password="correct horse battery")

    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("OWNER@example.com", password="another long password")


def make_company_with(*memberships):
    company = make_company("Acme Sdn Bhd")
    users = []
    for email, role in memberships:
        user = User.objects.create_user(email, password="correct horse battery")
        CompanyAccess.objects.create(user=user, company=company, role=role)
        users.append(user)
    return company, users


def test_the_database_rejects_a_second_owner_for_the_same_company(db):
    company, _ = make_company_with(("owner@example.com", CompanyAccess.OWNER))
    other = User.objects.create_user("other@example.com", password="correct horse battery")

    with pytest.raises(IntegrityError), transaction.atomic():
        CompanyAccess.objects.create(user=other, company=company, role=CompanyAccess.OWNER)


def test_a_company_can_have_many_staff_and_the_same_person_can_own_only_once_per_company(db):
    company, _ = make_company_with(
        ("owner@example.com", CompanyAccess.OWNER),
        ("staff1@example.com", CompanyAccess.STAFF),
        ("staff2@example.com", CompanyAccess.STAFF),
    )

    assert company.access.count() == 3


def as_web_query(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall(), [column.name for column in cursor.description]


def test_the_web_role_sees_only_its_own_user_row(as_user, db):
    _, (owner, staff) = make_company_with(
        ("owner@example.com", CompanyAccess.OWNER), ("staff@example.com", CompanyAccess.STAFF)
    )

    with as_user(staff):
        rows, _ = as_web_query("SELECT email FROM core_user")

    assert rows == [("staff@example.com",)]


def test_the_web_role_cannot_create_change_or_delete_users(as_user, db):
    _, (owner,) = make_company_with(("owner@example.com", CompanyAccess.OWNER))

    with as_user(owner):
        for statement in [
            "UPDATE core_user SET is_superuser = true",
            "DELETE FROM core_user",
            "INSERT INTO core_user (password, is_superuser, email, is_active, date_joined) "
            "VALUES ('x', true, 'evil@example.com', true, now())",
        ]:
            with pytest.raises(DatabaseError), transaction.atomic():
                as_web_query(statement)


@pytest.mark.parametrize("table", ["core_authtoken", "core_loginfailure"])
def test_the_web_role_cannot_read_the_token_or_throttle_tables_directly(as_user, db, table):
    _, (owner,) = make_company_with(("owner@example.com", CompanyAccess.OWNER))

    with as_user(owner), pytest.raises(DatabaseError), transaction.atomic():
        as_web_query(f"SELECT * FROM {table}")


def test_the_sign_in_lookup_returns_only_what_sign_in_needs_for_an_exact_match(as_user, db):
    make_company_with(("owner@example.com", CompanyAccess.OWNER))

    with as_user(User.objects.get()):
        found, columns = as_web_query("SELECT * FROM auth_lookup(%s)", ["Owner@Example.com"])
        guessed, _ = as_web_query("SELECT * FROM auth_lookup(%s)", ["owner@%"])
        unknown, _ = as_web_query("SELECT * FROM auth_lookup(%s)", ["nobody@example.com"])

    assert columns == ["id", "password", "is_active"] and len(found) == 1
    assert guessed == [] and unknown == []


def test_the_database_rejects_a_role_that_is_not_one_of_the_three(db):
    company = make_company("Acme Sdn Bhd")
    user = User.objects.create_user("admin@example.com", password="correct horse battery")

    with pytest.raises(IntegrityError), transaction.atomic():
        CompanyAccess.objects.create(user=user, company=company, role="admin")
