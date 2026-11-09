"""The item master (ticket #121, D59 (9), (10)).

Each company keeps the products and services it sells, each with its LHDN classification code, unit code and default
tax type, so that LHDN's per-line data is never typed per invoice. Staff create items and edit the other fields; only
the owner changes the two LHDN codes. Codes are checked against LHDN's published lists. An item is deactivated, never
deleted, and every change is kept with its before and after values.
"""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction

from core import items
from core.models import AuditEntry, CompanyAccess, Item, ItemChange

from .factories import make_company, make_member

# Real entries of LHDN's lists (SDK /codes/): classification 022 "Others", 003 "Computer, smartphone or tablet";
# unit C62 "one", KGM "kilogram"; tax type 01 "Sales Tax", 06 "Not Applicable".
OTHERS, COMPUTER = "022", "003"
ONE, KILOGRAM = "C62", "KGM"
SALES_TAX, NOT_APPLICABLE = "01", "06"


def create(user, company, **overrides):
    values = dict(
        name="Consulting hour",
        classification_code=OTHERS,
        unit_code=ONE,
        default_tax_type=NOT_APPLICABLE,
    )
    return items.create_item(user, company, **{**values, **overrides})


# --- creating, and LHDN's code lists -------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_owner_and_staff_can_create_an_item(world, who):
    item = create(world[who], world["acme"])

    assert (item.name, item.classification_code, item.unit_code, item.default_tax_type, item.is_active) == (
        "Consulting hour", OTHERS, ONE, NOT_APPLICABLE, True,
    )
    assert list(items.list_items(world["acme"])) == [item]


def test_an_accountant_cannot_create_an_item(world):
    with pytest.raises(PermissionDenied):
        create(world["accountant"], world["acme"])
    assert list(items.list_items(world["acme"])) == []


def test_the_user_of_another_company_cannot_create_an_item(world):
    with pytest.raises(PermissionDenied):
        create(world["beta_owner"], world["acme"])


@pytest.mark.parametrize(
    "field, bad",
    [
        ("classification_code", "999"),  # not on LHDN's list
        ("classification_code", ""),
        ("classification_code", "22"),  # the list has "022"
        ("unit_code", "ZZZ"),
        ("unit_code", "c62"),  # codes are case-sensitive in LHDN's list
        ("unit_code", ""),
        ("default_tax_type", "99"),
        ("default_tax_type", ""),
    ],
)
def test_a_code_that_is_not_in_lhdns_list_is_refused(world, field, bad):
    with pytest.raises(items.InvalidItem) as refusal:
        create(world["acme_owner"], world["acme"], **{field: bad})

    assert list(items.list_items(world["acme"])) == []
    assert bad not in str(refusal.value) or bad == ""


def test_the_lists_hold_lhdns_published_entries():
    assert items.classification_codes()["022"] == "Others"
    assert items.unit_codes()["KGM"] == "kilogram"
    assert items.tax_types()["06"] == "Not Applicable" and items.tax_types()["E"]
    assert len(items.classification_codes()) == 45


@pytest.mark.parametrize("name", ["", "   ", "x" * 201])
def test_an_item_needs_a_name_of_reasonable_length(world, name):
    with pytest.raises(items.InvalidItem):
        create(world["acme_owner"], world["acme"], name=name)


# --- editing: staff change the other fields, only the owner changes the LHDN codes ---------------------------------


def test_staff_can_rename_an_item_and_change_its_default_tax_type(world):
    item = create(world["acme_owner"], world["acme"])

    items.update_item(world["acme_staff"], item, name="Consulting day", default_tax_type=SALES_TAX)

    item.refresh_from_db()
    assert (item.name, item.default_tax_type) == ("Consulting day", SALES_TAX)
    assert (item.classification_code, item.unit_code) == (OTHERS, ONE)


@pytest.mark.parametrize("change", [{"classification_code": COMPUTER}, {"unit_code": KILOGRAM}])
def test_staff_cannot_change_an_items_lhdn_codes(world, change):
    item = create(world["acme_staff"], world["acme"])

    with pytest.raises(PermissionDenied):
        items.update_item(world["acme_staff"], item, name="Renamed", **change)

    item.refresh_from_db()
    assert (item.name, item.classification_code, item.unit_code) == ("Consulting hour", OTHERS, ONE)


def test_staff_can_save_an_item_with_its_codes_unchanged(world):
    item = create(world["acme_owner"], world["acme"])

    items.update_item(
        world["acme_staff"], item, name="Renamed", classification_code=OTHERS, unit_code=ONE, default_tax_type=SALES_TAX
    )

    item.refresh_from_db()
    assert item.name == "Renamed"


def test_the_owner_can_change_an_items_lhdn_codes(world):
    item = create(world["acme_staff"], world["acme"])

    items.update_item(world["acme_owner"], item, classification_code=COMPUTER, unit_code=KILOGRAM)

    item.refresh_from_db()
    assert (item.classification_code, item.unit_code) == (COMPUTER, KILOGRAM)


def test_a_changed_code_must_still_be_on_lhdns_list(world):
    item = create(world["acme_owner"], world["acme"])

    with pytest.raises(items.InvalidItem):
        items.update_item(world["acme_owner"], item, classification_code="999")
    with pytest.raises(items.InvalidItem):
        items.update_item(world["acme_owner"], item, unit_code="ZZZ")
    with pytest.raises(items.InvalidItem):
        items.update_item(world["acme_owner"], item, name=" ")

    item.refresh_from_db()
    assert (item.name, item.classification_code, item.unit_code) == ("Consulting hour", OTHERS, ONE)


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_a_code_that_lhdn_has_since_retired_does_not_block_other_edits(world, monkeypatch, who):
    item = create(world["acme_owner"], world["acme"])
    # LHDN refreshes its list and "022" is gone; the item keeps working and its saved codes are not re-judged.
    monkeypatch.setattr(items, "classification_codes", lambda: {COMPUTER: "Computer, smartphone or tablet"})

    items.update_item(world[who], item, name="Renamed", default_tax_type=SALES_TAX)

    item.refresh_from_db()
    assert (item.name, item.classification_code) == ("Renamed", OTHERS)
    # but a new item, or a changed code, is still checked against the list held today
    with pytest.raises(items.InvalidItem):
        create(world["acme_owner"], world["acme"])
    with pytest.raises(items.InvalidItem):
        items.update_item(world["acme_owner"], item, classification_code=OTHERS, unit_code="ZZZ")


@pytest.mark.parametrize("who", ["accountant", "beta_owner"])
def test_an_accountant_or_another_companys_owner_cannot_edit_an_item(world, who):
    item = create(world["acme_owner"], world["acme"])

    with pytest.raises(PermissionDenied):
        items.update_item(world[who], item, name="Renamed")
    with pytest.raises(PermissionDenied):
        items.deactivate_item(world[who], item)


# --- deactivating --------------------------------------------------------------------------------------------------


def test_a_deactivated_item_cannot_be_chosen_for_a_new_draft(world):
    item = create(world["acme_owner"], world["acme"])
    assert items.item_for_new_draft(world["acme"], item.pk) == item

    items.deactivate_item(world["acme_staff"], item)

    with pytest.raises(items.ItemNotAvailable):
        items.item_for_new_draft(world["acme"], item.pk)
    assert list(items.list_items(world["acme"], active_only=True)) == []


def test_a_deactivated_item_is_still_there_for_the_invoices_it_is_on(world):
    item = create(world["acme_owner"], world["acme"])
    items.deactivate_item(world["acme_owner"], item)

    assert list(items.list_items(world["acme"])) == [item]
    kept = items.list_items(world["acme"]).get()
    assert (kept.name, kept.classification_code, kept.unit_code, kept.default_tax_type) == (
        "Consulting hour", OTHERS, ONE, NOT_APPLICABLE,
    )


def test_an_item_can_be_activated_again(world):
    item = create(world["acme_owner"], world["acme"])
    items.deactivate_item(world["acme_owner"], item)

    items.reactivate_item(world["acme_owner"], item)

    assert items.item_for_new_draft(world["acme"], item.pk) == item


def test_an_item_of_another_company_cannot_be_chosen(world):
    other = create(world["beta_owner"], world["beta"])

    with pytest.raises(items.ItemNotAvailable):
        items.item_for_new_draft(world["acme"], other.pk)


# --- the history keeps the values before and after -----------------------------------------------------------------


def test_every_change_is_kept_with_its_values_before_and_after_and_who_made_it(world, as_user):
    # Who made a change is the signed-in identity the database holds, as in a request, so each change runs under it.
    with as_user(world["acme_staff"]):
        item = create(world["acme_staff"], world["acme"])
    with as_user(world["acme_owner"]):
        items.update_item(world["acme_owner"], item, classification_code=COMPUTER)
    with as_user(world["acme_staff"]):
        items.update_item(world["acme_staff"], item, name="Consulting day")
    with as_user(world["acme_owner"]):
        items.deactivate_item(world["acme_owner"], item)

    changes = list(items.history(item))

    assert [(c.changed_by.email, c.before is None) for c in changes] == [
        ("staff@acme.example", True),
        ("owner@acme.example", False),
        ("staff@acme.example", False),
        ("owner@acme.example", False),
    ]
    assert changes[0].after == {
        "name": "Consulting hour", "classification_code": OTHERS, "unit_code": ONE,
        "default_tax_type": NOT_APPLICABLE, "is_active": True,
    }
    assert (changes[1].before["classification_code"], changes[1].after["classification_code"]) == (OTHERS, COMPUTER)
    assert (changes[2].before["name"], changes[2].after["name"]) == ("Consulting hour", "Consulting day")
    assert (changes[3].before["is_active"], changes[3].after["is_active"]) == (True, False)


def test_saving_an_item_without_changing_it_adds_nothing_to_the_history(world):
    item = create(world["acme_owner"], world["acme"])

    items.update_item(world["acme_owner"], item, name="Consulting hour")

    assert len(items.history(item)) == 1


# --- the database holds the same rules, whoever writes -------------------------------------------------------------

def attempt(statement_block):
    """Run a write that the database must refuse, inside a savepoint so the test can go on."""
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            statement_block()


def test_another_companys_items_are_invisible_and_unwritable_for_every_role(world, as_user):
    acme_item = create(world["acme_owner"], world["acme"])
    create(world["beta_owner"], world["beta"], name="Beta thing")
    gamma = make_company("Gamma Staff Co")
    outsiders = [
        world["beta_owner"],
        make_member("staff@beta.example", world["beta"], CompanyAccess.STAFF),
        make_member("staff@gamma.example", gamma, CompanyAccess.STAFF),
    ]

    for outsider in outsiders:
        with as_user(outsider):
            assert not Item.objects.filter(company=world["acme"]).exists()
            assert not ItemChange.objects.filter(company=world["acme"]).exists()
            assert Item.objects.filter(pk=acme_item.pk).update(name="Hijacked") == 0
            attempt(lambda: Item.objects.create(company=world["acme"], name="Planted", classification_code=OTHERS,
                                                unit_code=ONE, default_tax_type=NOT_APPLICABLE))

    acme_item.refresh_from_db()
    assert acme_item.name == "Consulting hour" and Item.objects.filter(company=world["acme"]).count() == 1


def test_a_firm_accountant_reads_items_of_their_companies_but_the_database_refuses_their_writes(world, as_user):
    item = create(world["acme_owner"], world["acme"])

    with as_user(world["accountant"]):
        assert Item.objects.filter(pk=item.pk).exists()
        attempt(lambda: Item.objects.filter(pk=item.pk).update(name="Changed"))
        attempt(lambda: Item.objects.create(company=world["acme"], name="Planted", classification_code=OTHERS,
                                            unit_code=ONE, default_tax_type=NOT_APPLICABLE))

    item.refresh_from_db()
    assert item.name == "Consulting hour"


def test_the_database_refuses_staff_changing_a_code_even_when_the_service_is_skipped(world, as_user):
    item = create(world["acme_owner"], world["acme"])

    with as_user(world["acme_staff"]):
        attempt(lambda: Item.objects.filter(pk=item.pk).update(classification_code=COMPUTER))
        attempt(lambda: Item.objects.filter(pk=item.pk).update(unit_code=KILOGRAM))
        # the other fields stay open to staff
        assert Item.objects.filter(pk=item.pk).update(name="Renamed", default_tax_type=SALES_TAX) == 1
    with as_user(world["acme_owner"]):
        assert Item.objects.filter(pk=item.pk).update(classification_code=COMPUTER) == 1

    item.refresh_from_db()
    assert (item.name, item.classification_code, item.unit_code) == ("Renamed", COMPUTER, ONE)


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_an_item_cannot_be_deleted_by_anyone(world, as_user, who):
    item = create(world["acme_owner"], world["acme"])

    with as_user(world[who]):
        attempt(lambda: Item.objects.filter(pk=item.pk).delete())
    # nor by the table owner, who is not stopped by privileges: the trigger refuses
    attempt(lambda: Item.objects.filter(pk=item.pk).delete())

    assert Item.objects.filter(pk=item.pk).exists()


def test_an_item_cannot_move_to_another_company(world):
    item = create(world["acme_owner"], world["acme"])

    attempt(lambda: Item.objects.filter(pk=item.pk).update(company=world["beta"]))


def test_the_history_cannot_be_forged_edited_or_deleted(world, as_user):
    item = create(world["acme_owner"], world["acme"])
    entry = ItemChange.objects.get(item=item)

    def forge():
        ItemChange.objects.create(company=world["acme"], item=item, before=None, after={"name": "Forged"})

    for who in (None, "acme_owner", "acme_staff"):
        if who is None:
            attempt(forge)
            attempt(lambda: ItemChange.objects.filter(pk=entry.pk).update(after={}))
            attempt(lambda: ItemChange.objects.filter(pk=entry.pk).delete())
        else:
            with as_user(world[who]):
                attempt(forge)
                attempt(lambda: ItemChange.objects.filter(pk=entry.pk).update(after={}))
                attempt(lambda: ItemChange.objects.filter(pk=entry.pk).delete())

    assert ItemChange.objects.filter(item=item).count() == 1


def test_a_change_made_with_a_raw_update_is_still_in_the_history(world, as_user):
    item = create(world["acme_owner"], world["acme"])

    with as_user(world["acme_staff"]):
        Item.objects.filter(pk=item.pk).update(name="Raw rename")

    last = ItemChange.objects.filter(item=item).order_by("id").last()
    assert (last.before["name"], last.after["name"], last.changed_by) == ("Consulting hour", "Raw rename", world["acme_staff"])


def test_every_item_change_is_in_the_audit_trail(world, as_user):
    with as_user(world["acme_staff"]):
        item = create(world["acme_staff"], world["acme"])
    with as_user(world["acme_owner"]):
        items.update_item(world["acme_owner"], item, classification_code=COMPUTER)

    entries = AuditEntry.objects.filter(company=world["acme"], target_type="core_item", target_id=str(item.pk))
    assert [(e.action, e.actor_email) for e in entries.order_by("id")] == [
        ("create", "staff@acme.example"), ("update", "owner@acme.example"),
    ]
    assert entries.order_by("id").last().changed_fields == ["classification_code"]


def test_job_handlers_read_items_of_their_company_only_and_never_write(world, as_worker_connection):
    acme_item = create(world["acme_owner"], world["acme"])
    create(world["beta_owner"], world["beta"], name="Beta thing")

    with as_worker_connection():
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.company_id', %s, false)", [str(world["acme"].pk)])
            cursor.execute("SELECT name FROM core_item")
            assert cursor.fetchall() == [("Consulting hour",)]
        attempt(lambda: Item.objects.filter(pk=acme_item.pk).update(name="Worker edit"))
        attempt(lambda: Item.objects.create(company=world["acme"], name="Worker made", classification_code=OTHERS,
                                            unit_code=ONE, default_tax_type=NOT_APPLICABLE))
