"""The customer master (ticket #7, D46, D59 (7), (10)).

Each company keeps its buyers with legal name, TIN, ID type and ID value. A TIN is checked against MyInvois through the
gateway before it is saved; a company with e-invoicing on may not save a customer without a TIN and an ID. Staff create
customers; only the owner changes a TIN, legal name or ID. A customer is deactivated, never deleted, and every change is
kept with its values before and after.
"""

from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction

from core import customers, einvoicing
from core.models import AuditEntry, CompanyAccess, Customer, CustomerChange
from core.ports.interfaces import TinCheck

from .factories import make_company, make_member

# Shaped like a real TIN (the fake accepts one or two letters then 8 to 12 digits); not any real person's or company's.
TIN, OTHER_TIN = "C1234567890", "IG12345678901"
NRIC_VALUE, BRN_VALUE = "900101011234", "201901234567"


def make(user, company, **overrides):
    values = dict(legal_name="Buyer Sdn Bhd", tin=TIN, id_type="BRN", id_value=BRN_VALUE)
    return customers.create_customer(user, company, **{**values, **overrides})


def switch_einvoicing_on(owner, company):
    einvoicing.update_setting(
        owner,
        company,
        einvoicing_enabled=True,
        status="voluntary",
        effective_from=date(2020, 1, 1),
        tin="C9999999999",
        brn="200001000001",
        msic_code="82193",
        address_line1="1 Test Street",
        city="Kuala Lumpur",
        postal_zone="50000",
        state_code="14",
        country_code="MYS",
    )


# --- creating, and the TIN check ------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_owner_and_staff_can_create_a_customer(world, who):
    customer = make(world[who], world["acme"])

    assert (customer.legal_name, customer.tin, customer.id_type, customer.id_value, customer.is_active) == (
        "Buyer Sdn Bhd", TIN, "BRN", BRN_VALUE, True,
    )
    assert list(customers.list_customers(world["acme"])) == [customer]


def test_an_accountant_or_another_companys_user_cannot_create_a_customer(world):
    for who in ("accountant", "beta_owner"):
        with pytest.raises(PermissionDenied):
            make(world[who], world["acme"])
    assert list(customers.list_customers(world["acme"])) == []


def test_the_tin_is_checked_through_the_gateway_before_the_customer_is_saved(world, fake_ports):
    make(world["acme_staff"], world["acme"], tin=TIN, id_type="NRIC", id_value=NRIC_VALUE)

    assert fake_ports.gateway.tins_validated == [(TIN, "NRIC", NRIC_VALUE)]


@pytest.mark.parametrize("tin", ["not a tin", "123"])
def test_a_tin_of_the_wrong_format_is_refused_and_nothing_is_saved(world, tin):
    with pytest.raises(customers.InvalidCustomer) as refusal:
        make(world["acme_owner"], world["acme"], tin=tin)

    assert list(customers.list_customers(world["acme"])) == []
    assert tin not in str(refusal.value)


def test_a_tin_that_lhdn_does_not_know_is_refused_and_nothing_is_saved(world, fake_ports):
    fake_ports.gateway.unknown_tins.add(TIN)

    with pytest.raises(customers.InvalidCustomer) as refusal:
        make(world["acme_owner"], world["acme"])

    assert list(customers.list_customers(world["acme"])) == []
    assert TIN not in str(refusal.value) and BRN_VALUE not in str(refusal.value)


def test_the_two_tin_refusals_say_different_things(world, fake_ports):
    fake_ports.gateway.unknown_tins.add(TIN)
    with pytest.raises(customers.InvalidCustomer) as not_found:
        make(world["acme_owner"], world["acme"])
    # The sandbox answers 400 only for an empty ID value, which the form already refuses; the answer is still read.
    fake_ports.gateway.validate_tin = lambda *args: TinCheck.BAD_FORMAT
    with pytest.raises(customers.InvalidCustomer) as bad_format:
        make(world["acme_owner"], world["acme"], tin="bad")

    assert str(not_found.value) != str(bad_format.value)


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_a_customer_needs_a_legal_name_of_reasonable_length(world, name):
    with pytest.raises(customers.InvalidCustomer):
        make(world["acme_owner"], world["acme"], legal_name=name)


def test_a_tin_cannot_be_checked_without_an_id_type_and_value(world, fake_ports):
    for overrides in ({"id_type": "", "id_value": ""}, {"id_value": ""}, {"id_type": ""}, {"id_type": "DRIVING"}):
        with pytest.raises(customers.InvalidCustomer):
            make(world["acme_owner"], world["acme"], **overrides)

    assert fake_ports.gateway.tins_validated == []
    assert list(customers.list_customers(world["acme"])) == []


def test_an_id_type_without_an_id_value_is_refused_even_with_no_tin(world):
    with pytest.raises(customers.InvalidCustomer):
        make(world["acme_owner"], world["acme"], tin="", id_type="BRN", id_value="")


def test_an_id_value_without_a_type_and_a_type_without_a_value_are_told_apart(world):
    with pytest.raises(customers.InvalidCustomer) as no_type:
        make(world["acme_owner"], world["acme"], tin="", id_type="", id_value=BRN_VALUE)
    with pytest.raises(customers.InvalidCustomer) as no_value:
        make(world["acme_owner"], world["acme"], tin="", id_type="BRN", id_value="")

    assert str(no_type.value) != str(no_value.value)


# --- a company with e-invoicing off may save a customer without a TIN (D59 (7), D46) -------------------------------


def test_with_e_invoicing_off_a_customer_may_have_no_tin_and_no_id_and_nothing_is_checked(world, fake_ports):
    customer = make(world["acme_staff"], world["acme"], tin="", id_type="", id_value="")

    assert (customer.tin, customer.id_type, customer.id_value) == ("", "", "")
    assert fake_ports.gateway.tins_validated == []


def test_with_e_invoicing_off_a_tin_that_is_given_is_still_checked(world, fake_ports):
    fake_ports.gateway.unknown_tins.add(TIN)

    with pytest.raises(customers.InvalidCustomer):
        make(world["acme_owner"], world["acme"])


# --- a company with e-invoicing on needs the TIN and the ID (D59 (7)) ----------------------------------------------


@pytest.mark.parametrize("missing", ["tin", "id_type", "id_value"])
def test_with_e_invoicing_on_a_customer_missing_the_tin_or_the_id_is_refused(world, missing):
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    with pytest.raises(customers.InvalidCustomer):
        make(world["acme_owner"], world["acme"], **{missing: ""})

    assert list(customers.list_customers(world["acme"])) == []


def test_with_e_invoicing_on_a_complete_customer_is_saved(world):
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    assert make(world["acme_staff"], world["acme"]).pk


def test_the_switch_of_one_company_does_not_bind_another(world):
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    assert make(world["beta_owner"], world["beta"], tin="", id_type="", id_value="").pk


# --- editing: only the owner changes the TIN, legal name or ID -----------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [{"legal_name": "Renamed Sdn Bhd"}, {"tin": OTHER_TIN}, {"id_type": "NRIC", "id_value": NRIC_VALUE}, {"id_value": "x1"}],
)
def test_staff_cannot_change_the_legal_name_tin_or_id_of_a_customer(world, change):
    customer = make(world["acme_staff"], world["acme"])

    with pytest.raises(PermissionDenied):
        customers.update_customer(world["acme_staff"], customer, **change)

    customer.refresh_from_db()
    assert (customer.legal_name, customer.tin, customer.id_type, customer.id_value) == (
        "Buyer Sdn Bhd", TIN, "BRN", BRN_VALUE,
    )


def test_the_owner_can_change_them_and_the_new_tin_is_checked(world, fake_ports):
    customer = make(world["acme_staff"], world["acme"])
    fake_ports.gateway.tins_validated.clear()

    customers.update_customer(
        world["acme_owner"], customer, legal_name="Renamed Sdn Bhd", tin=OTHER_TIN, id_type="NRIC", id_value=NRIC_VALUE
    )

    customer.refresh_from_db()
    assert (customer.legal_name, customer.tin, customer.id_type, customer.id_value) == (
        "Renamed Sdn Bhd", OTHER_TIN, "NRIC", NRIC_VALUE,
    )
    assert fake_ports.gateway.tins_validated == [(OTHER_TIN, "NRIC", NRIC_VALUE)]


def test_a_changed_tin_that_lhdn_refuses_leaves_the_customer_as_it_was(world, fake_ports):
    customer = make(world["acme_owner"], world["acme"])
    fake_ports.gateway.unknown_tins.add(OTHER_TIN)

    with pytest.raises(customers.InvalidCustomer):
        customers.update_customer(world["acme_owner"], customer, tin=OTHER_TIN)
    with pytest.raises(customers.InvalidCustomer):
        customers.update_customer(world["acme_owner"], customer, legal_name="  ")

    customer.refresh_from_db()
    assert (customer.legal_name, customer.tin) == ("Buyer Sdn Bhd", TIN)


def test_a_customer_can_be_saved_with_nothing_changed_by_staff_without_a_new_check_or_history(world, fake_ports):
    customer = make(world["acme_owner"], world["acme"])
    fake_ports.gateway.tins_validated.clear()
    fake_ports.gateway.unknown_tins.add(TIN)  # LHDN has since lost it; an unchanged save must not re-judge it

    customers.update_customer(
        world["acme_staff"], customer, legal_name="Buyer Sdn Bhd", tin=TIN, id_type="BRN", id_value=BRN_VALUE
    )

    assert fake_ports.gateway.tins_validated == [] and len(customers.history(customer)) == 1


def test_an_edit_does_not_overwrite_a_change_saved_while_the_tin_was_being_checked(world, fake_ports, monkeypatch):
    customer = make(world["acme_owner"], world["acme"])
    real_check = fake_ports.gateway.validate_tin

    def check_while_someone_else_saves(tin, id_type, id_value):
        Customer.objects.filter(pk=customer.pk).update(legal_name="Saved by someone else")
        return real_check(tin, id_type, id_value)

    monkeypatch.setattr(fake_ports.gateway, "validate_tin", check_while_someone_else_saves)

    with pytest.raises(customers.InvalidCustomer):
        customers.update_customer(world["acme_owner"], customer, tin=OTHER_TIN)

    customer.refresh_from_db()
    assert (customer.legal_name, customer.tin) == ("Saved by someone else", TIN)


def test_with_e_invoicing_on_the_owner_cannot_blank_the_tin(world):
    customer = make(world["acme_owner"], world["acme"])
    switch_einvoicing_on(world["acme_owner"], world["acme"])

    with pytest.raises(customers.InvalidCustomer):
        customers.update_customer(world["acme_owner"], customer, tin="", id_type="", id_value="")


@pytest.mark.parametrize("who", ["accountant", "beta_owner"])
def test_an_accountant_or_another_companys_owner_cannot_edit_or_deactivate(world, who):
    customer = make(world["acme_owner"], world["acme"])

    with pytest.raises(PermissionDenied):
        customers.update_customer(world[who], customer, legal_name="Renamed")
    with pytest.raises(PermissionDenied):
        customers.deactivate_customer(world[who], customer)


# --- deactivating (D59 (10)) ----------------------------------------------------------------------------------------


def test_a_deactivated_customer_cannot_be_chosen_for_a_new_draft_but_is_still_listed(world):
    customer = make(world["acme_owner"], world["acme"])
    assert customers.customer_for_new_draft(world["acme"], customer.pk) == customer

    customers.deactivate_customer(world["acme_staff"], customer)

    with pytest.raises(customers.CustomerNotAvailable):
        customers.customer_for_new_draft(world["acme"], customer.pk)
    assert list(customers.list_customers(world["acme"])) == [customer]
    assert list(customers.list_customers(world["acme"], active_only=True)) == []

    customers.reactivate_customer(world["acme_owner"], customer)
    assert customers.customer_for_new_draft(world["acme"], customer.pk) == customer


def test_a_customer_of_another_company_cannot_be_chosen(world):
    other = make(world["beta_owner"], world["beta"])

    with pytest.raises(customers.CustomerNotAvailable):
        customers.customer_for_new_draft(world["acme"], other.pk)


# --- the history keeps the values before and after -----------------------------------------------------------------


def test_every_change_is_kept_with_its_values_before_and_after_and_who_made_it(world, as_user):
    with as_user(world["acme_staff"]):
        customer = make(world["acme_staff"], world["acme"])
    with as_user(world["acme_owner"]):
        customers.update_customer(world["acme_owner"], customer, legal_name="Renamed Sdn Bhd")
    with as_user(world["acme_staff"]):
        customers.deactivate_customer(world["acme_staff"], customer)

    changes = customers.history(customer)

    assert [(c.changed_by_email, c.before is None) for c in changes] == [
        ("staff@acme.example", True), ("owner@acme.example", False), ("staff@acme.example", False),
    ]
    assert changes[0].after == {
        "legal_name": "Buyer Sdn Bhd", "tin": TIN, "id_type": "BRN", "id_value": BRN_VALUE, "is_active": True,
    }
    assert (changes[1].before["legal_name"], changes[1].after["legal_name"]) == ("Buyer Sdn Bhd", "Renamed Sdn Bhd")
    assert (changes[2].before["is_active"], changes[2].after["is_active"]) == (True, False)


def test_every_customer_change_is_in_the_audit_trail_without_the_values_of_the_tin(world, as_user):
    with as_user(world["acme_staff"]):
        customer = make(world["acme_staff"], world["acme"])
    with as_user(world["acme_owner"]):
        customers.update_customer(world["acme_owner"], customer, legal_name="Renamed Sdn Bhd")

    entries = AuditEntry.objects.filter(company=world["acme"], target_type="core_customer", target_id=str(customer.pk))
    assert [(e.action, e.actor_email) for e in entries.order_by("id")] == [
        ("create", "staff@acme.example"), ("update", "owner@acme.example"),
    ]
    assert entries.order_by("id").last().changed_fields == ["legal_name"]


# --- the database holds the same rules, whoever writes -------------------------------------------------------------


def attempt(write):
    """Run a write that the database must refuse, inside a savepoint so the test can go on."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            write()


def plant(company, **overrides):
    return lambda: Customer.objects.create(
        company=company, **{"legal_name": "Planted", "tin": TIN, "id_type": "BRN", "id_value": BRN_VALUE, **overrides}
    )


def test_another_companys_customers_are_invisible_and_unwritable_for_every_role(world, as_user):
    customer = make(world["acme_owner"], world["acme"])
    make(world["beta_owner"], world["beta"], legal_name="Beta buyer")
    gamma = make_company("Gamma Staff Co")
    outsiders = [
        world["beta_owner"],
        make_member("staff@beta.example", world["beta"], CompanyAccess.STAFF),
        make_member("staff@gamma.example", gamma, CompanyAccess.STAFF),
    ]

    for outsider in outsiders:
        with as_user(outsider):
            assert not Customer.objects.filter(company=world["acme"]).exists()
            assert not CustomerChange.objects.filter(company=world["acme"]).exists()
            assert Customer.objects.filter(pk=customer.pk).update(legal_name="Hijacked") == 0
            attempt(plant(world["acme"]))

    customer.refresh_from_db()
    assert customer.legal_name == "Buyer Sdn Bhd" and Customer.objects.filter(company=world["acme"]).count() == 1


def test_a_firm_accountant_reads_customers_but_the_database_refuses_their_writes(world, as_user):
    customer = make(world["acme_owner"], world["acme"])

    with as_user(world["accountant"]):
        assert Customer.objects.filter(pk=customer.pk).exists()
        attempt(lambda: Customer.objects.filter(pk=customer.pk).update(legal_name="Changed"))
        attempt(plant(world["acme"]))

    customer.refresh_from_db()
    assert customer.legal_name == "Buyer Sdn Bhd"


def test_the_database_refuses_staff_changing_identity_fields_even_when_the_service_is_skipped(world, as_user):
    customer = make(world["acme_owner"], world["acme"])

    with as_user(world["acme_staff"]):
        for change in ({"legal_name": "X"}, {"tin": OTHER_TIN}, {"id_type": "NRIC"}, {"id_value": "1"}):
            attempt(lambda change=change: Customer.objects.filter(pk=customer.pk).update(**change))
        assert Customer.objects.filter(pk=customer.pk).update(is_active=False) == 1
    with as_user(world["acme_owner"]):
        assert Customer.objects.filter(pk=customer.pk).update(tin=OTHER_TIN) == 1

    customer.refresh_from_db()
    assert (customer.legal_name, customer.tin, customer.is_active) == ("Buyer Sdn Bhd", OTHER_TIN, False)


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_a_customer_cannot_be_deleted_by_anyone(world, as_user, who):
    customer = make(world["acme_owner"], world["acme"])

    with as_user(world[who]):
        attempt(lambda: Customer.objects.filter(pk=customer.pk).delete())
    attempt(lambda: Customer.objects.filter(pk=customer.pk).delete())  # nor the table owner: the trigger refuses

    assert Customer.objects.filter(pk=customer.pk).exists()


def test_a_customer_cannot_move_to_another_company(world):
    customer = make(world["acme_owner"], world["acme"])

    attempt(lambda: Customer.objects.filter(pk=customer.pk).update(company=world["beta"]))


def test_the_history_cannot_be_forged_edited_or_deleted(world, as_user):
    customer = make(world["acme_owner"], world["acme"])
    entry = CustomerChange.objects.get(customer=customer)

    def forge():
        CustomerChange.objects.create(company=world["acme"], customer=customer, before=None, after={"legal_name": "Forged"})

    for who in (None, "acme_owner", "acme_staff"):
        def run():
            attempt(forge)
            attempt(lambda: CustomerChange.objects.filter(pk=entry.pk).update(after={}))
            attempt(lambda: CustomerChange.objects.filter(pk=entry.pk).delete())

        if who is None:
            run()
        else:
            with as_user(world[who]):
                run()

    assert CustomerChange.objects.filter(customer=customer).count() == 1


def test_a_change_made_with_a_raw_update_is_still_in_the_history(world, as_user):
    customer = make(world["acme_owner"], world["acme"])

    with as_user(world["acme_owner"]):
        Customer.objects.filter(pk=customer.pk).update(legal_name="Raw rename")

    last = CustomerChange.objects.filter(customer=customer).order_by("id").last()
    assert (last.before["legal_name"], last.after["legal_name"], last.changed_by) == (
        "Buyer Sdn Bhd", "Raw rename", world["acme_owner"],
    )


def test_job_handlers_read_customers_of_their_company_only_and_never_write(world, as_worker_connection):
    from django.db import connection

    customer = make(world["acme_owner"], world["acme"])
    make(world["beta_owner"], world["beta"], legal_name="Beta buyer")

    with as_worker_connection():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, false)", [str(world["acme"].pk)])
            cursor.execute("SELECT legal_name FROM core_customer")
            assert cursor.fetchall() == [("Buyer Sdn Bhd",)]
        attempt(lambda: Customer.objects.filter(pk=customer.pk).update(legal_name="Worker edit"))
        attempt(plant(world["acme"]))
