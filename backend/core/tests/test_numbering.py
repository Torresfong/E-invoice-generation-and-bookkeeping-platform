"""Gapless invoice numbering per company and document type (D18, D44, D59 (6), D60 (2); ticket 07b, #45).

The numbering service is called by approval, which is not built yet, so these tests call it inside a transaction the
way approval will: the number is taken in the same transaction as the rest of the approval.
"""

import threading

import pytest
from django.db import DatabaseError, connection, transaction

from core import numbering
from core.models import AuditEntry, CompanyAccess, DocumentSequence

from .factories import make_company, make_member

INVOICE = numbering.INVOICE
CREDIT_NOTE = numbering.CREDIT_NOTE


@pytest.fixture
def acme(db):
    company = make_company("Acme Sdn Bhd")
    numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=1)
    return company


def take(company, document_type=INVOICE):
    with transaction.atomic():
        return numbering.allocate_number(company, document_type)


def test_numbers_run_one_after_another_from_the_owners_starting_number(db):
    company = make_company()
    numbering.configure_sequence(company, INVOICE, prefix="ACM", start_number=1043)

    assert [take(company) for _ in range(3)] == ["ACM1043", "ACM1044", "ACM1045"]


def test_numbers_are_padded_with_zeros_to_the_owners_width(db):
    company = make_company()
    numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=123, padding=5)

    assert [take(company) for _ in range(2)] == ["INV-00123", "INV-00124"]


def test_a_number_longer_than_the_padding_is_not_cut(db):
    company = make_company()
    numbering.configure_sequence(company, INVOICE, prefix="I", start_number=99, padding=2)

    assert [take(company) for _ in range(2)] == ["I99", "I100"]


def test_the_padding_is_locked_with_the_prefix_once_a_number_is_issued(acme):
    take(acme)

    with pytest.raises(numbering.SequenceInUse):
        numbering.configure_sequence(acme, INVOICE, prefix="INV-", start_number=1, padding=6)
    with pytest.raises(DatabaseError):  # and the database refuses it for any other writer
        with transaction.atomic():
            DocumentSequence.objects.filter(company=acme).update(padding=6)


def test_the_padding_must_be_between_zero_and_ten(db):
    company = make_company()
    for bad in (-1, 11):
        with pytest.raises(numbering.InvalidConfiguration):
            numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=1, padding=bad)


def test_the_prefix_may_be_empty(db):
    company = make_company()
    numbering.configure_sequence(company, INVOICE, prefix="", start_number=7)

    assert take(company) == "7"


def test_a_company_with_no_sequence_cannot_take_a_number(db):
    with pytest.raises(numbering.SequenceNotConfigured):
        take(make_company())


def test_each_document_type_has_its_own_sequence(acme):
    numbering.configure_sequence(acme, CREDIT_NOTE, prefix="CN-", start_number=1)

    assert [take(acme), take(acme, CREDIT_NOTE), take(acme), take(acme, CREDIT_NOTE)] == [
        "INV-1",
        "CN-1",
        "INV-2",
        "CN-2",
    ]


def test_two_companies_never_interfere(acme):
    beta = make_company("Beta Trading")
    numbering.configure_sequence(beta, INVOICE, prefix="INV-", start_number=500)

    assert [take(acme), take(beta), take(acme), take(beta)] == ["INV-1", "INV-500", "INV-2", "INV-501"]


def test_a_number_is_not_used_up_when_the_approval_that_took_it_rolls_back(acme):
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            assert numbering.allocate_number(acme, INVOICE) == "INV-1"
            raise RuntimeError("approval failed after the number was taken")

    assert take(acme) == "INV-1"  # no gap: the failed approval never used it


def test_the_prefix_and_starting_number_can_be_changed_until_the_first_number_is_issued(acme):
    numbering.configure_sequence(acme, INVOICE, prefix="SI/", start_number=1000)

    assert take(acme) == "SI/1000"


def test_after_the_first_number_is_issued_the_configuration_is_locked(acme):
    take(acme)

    with pytest.raises(numbering.SequenceInUse):
        numbering.configure_sequence(acme, INVOICE, prefix="NEW-", start_number=1)
    assert take(acme) == "INV-2"  # nothing changed, nothing reused


def test_the_starting_number_must_be_a_positive_whole_number_and_the_number_must_fit_the_export(db):
    company = make_company()
    for bad in (0, -5):
        with pytest.raises(numbering.InvalidConfiguration):
            numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=bad)
    with pytest.raises(numbering.InvalidConfiguration):  # D51 (3): the SQL Account template holds 20 characters
        numbering.configure_sequence(company, INVOICE, prefix="X" * 11, start_number=1)
    with pytest.raises(numbering.InvalidConfiguration):
        numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=10**10)
    with pytest.raises(numbering.InvalidConfiguration):
        numbering.configure_sequence(company, "receipt", prefix="R", start_number=1)


def test_the_database_refuses_a_counter_that_moves_back(acme):
    take(acme)

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            DocumentSequence.objects.filter(company=acme).update(next_number=1)


@pytest.mark.django_db(transaction=True)
def test_concurrent_approvals_get_distinct_consecutive_numbers():
    company = make_company("Acme Sdn Bhd")
    numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=1)
    taken, errors = [], []
    start = threading.Barrier(8)

    def approve():
        try:
            start.wait()
            taken.append(take(company))
        except Exception as error:  # reported on the main thread
            errors.append(error)
        finally:
            connection.close()

    threads = [threading.Thread(target=approve) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    assert sorted(taken, key=lambda n: int(n.removeprefix("INV-"))) == [f"INV-{n}" for n in range(1, 9)]


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_approval_does_not_leave_a_gap_for_the_one_waiting_behind_it():
    company = make_company("Acme Sdn Bhd")
    numbering.configure_sequence(company, INVOICE, prefix="INV-", start_number=1)
    outcome = {}

    def second_approval():
        try:
            outcome["number"] = take(company)
        except Exception as error:
            outcome["error"] = error
        finally:
            connection.close()

    with pytest.raises(RuntimeError):
        with transaction.atomic():
            assert numbering.allocate_number(company, INVOICE) == "INV-1"
            waiter = threading.Thread(target=second_approval)
            waiter.start()
            for _ in range(50):  # wait until the second approval is queued behind this one's lock
                with connection.cursor() as cursor:
                    cursor.execute("SELECT count(*) FROM pg_locks WHERE NOT granted")
                    if cursor.fetchone()[0]:
                        break
                threading.Event().wait(0.1)
            raise RuntimeError("first approval fails")
    waiter.join(timeout=30)

    assert outcome == {"number": "INV-1"}


# --- tenancy: the sequence is a tenant table under row-level security --------------------------------------------


def query(sql, params=()):
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.description else cursor.rowcount


def test_a_user_sees_only_their_own_companys_sequence(acme, as_user):
    beta = make_company("Beta Trading")
    numbering.configure_sequence(beta, INVOICE, prefix="BETA-", start_number=1)
    owner = make_member("owner@acme.example", acme, CompanyAccess.OWNER)

    with as_user(owner):
        assert query("SELECT prefix FROM core_documentsequence") == [("INV-",)]


def test_a_user_cannot_change_or_take_numbers_from_another_companys_sequence(acme, as_user):
    beta = make_company("Beta Trading")
    numbering.configure_sequence(beta, INVOICE, prefix="BETA-", start_number=1)
    owner = make_member("owner@acme.example", acme, CompanyAccess.OWNER)

    with as_user(owner):
        assert query("UPDATE core_documentsequence SET next_number = next_number + 1 WHERE company_id = %s", [beta.pk]) == 0
        with pytest.raises(numbering.SequenceNotConfigured):
            with transaction.atomic():
                numbering.allocate_number(beta, INVOICE)
    assert take(beta) == "BETA-1"


def test_the_signed_in_owner_can_take_a_number_through_the_web_role(acme, as_user):
    owner = make_member("owner@acme.example", acme, CompanyAccess.OWNER)

    with as_user(owner):
        assert take(acme) == "INV-1"
    assert take(acme) == "INV-2"


def test_taking_a_number_is_audited_without_the_number_itself(acme):
    take(acme)

    entry = AuditEntry.objects.filter(company=acme, target_type="core_documentsequence", action="update").latest("id")
    assert entry.changed_fields == ["next_number"]


# --- the table's own policy, grants and isolation (reviewed for #45) ----------------------------------------------


def test_the_sequence_table_is_under_forced_row_level_security_with_one_web_policy_and_no_worker_access(db):
    rows = query("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'core_documentsequence'")
    assert rows == [(True, True)]
    policies = query("SELECT policyname, roles FROM pg_policies WHERE tablename = 'core_documentsequence'")
    assert [(name, set(roles)) for name, roles in policies if name != "definer_functions"] == [
        ("tenant_isolation", {"web"})
    ]
    privileges = {
        role: query(
            "SELECT string_agg(privilege_type, ',' ORDER BY privilege_type) FROM information_schema.role_table_grants "
            "WHERE table_name = 'core_documentsequence' AND grantee = %s",
            [role],
        )[0][0]
        for role in ("web", "worker", "PUBLIC")
    }
    assert privileges == {"web": "DELETE,INSERT,SELECT,UPDATE", "worker": None, "PUBLIC": None}


def test_a_user_cannot_create_a_sequence_for_another_company_or_move_one(acme, as_user):
    beta = make_company("Beta Trading")
    owner = make_member("owner@acme.example", acme, CompanyAccess.OWNER)

    with as_user(owner):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                query(
                    "INSERT INTO core_documentsequence (company_id, document_type, prefix, start_number, next_number) "
                    "VALUES (%s, 'credit_note', 'X', 1, 1)",
                    [beta.pk],
                )
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                query("UPDATE core_documentsequence SET company_id = %s WHERE company_id = %s", [beta.pk, acme.pk])


def test_a_user_with_no_membership_sees_no_sequence_and_the_worker_role_sees_none(acme, as_user):
    stranger = make_member("stranger@other.example", make_company("Other Co"), CompanyAccess.OWNER)

    with as_user(stranger):
        assert query("SELECT count(*) FROM core_documentsequence") == [(0,)]
    with connection.cursor() as cursor:
        cursor.execute("SET ROLE worker")
        try:
            with pytest.raises(DatabaseError):
                with transaction.atomic():
                    cursor.execute("SELECT count(*) FROM core_documentsequence")
        finally:
            cursor.execute("RESET ROLE")
