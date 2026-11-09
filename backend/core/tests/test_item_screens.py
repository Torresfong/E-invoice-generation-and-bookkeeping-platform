"""The item screens (ticket #121): list and create, edit, deactivate and activate, over HTTP.

The rules themselves are tested in test_items.py; these tests show the screens apply them, hide what a role may not
do, refuse another company's user, and read in Chinese.
"""

import pytest

from core import items
from core.models import AuditEntry, Item

from .factories import PASSWORD

OTHERS, COMPUTER = "022", "003"
ONE, KILOGRAM = "C62", "KGM"
SALES_TAX, NOT_APPLICABLE = "01", "06"

CHINESE = {"accept-language": "zh-hans"}


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def item_form(**overrides):
    return {
        "name": "Consulting hour", "classification_code": OTHERS, "unit_code": ONE, "default_tax_type": NOT_APPLICABLE,
        **overrides,
    }


def items_url(company, *rest):
    return "/".join([f"/companies/{company.pk}/items", *map(str, rest), ""])


def make_item(user, company, **overrides):
    return items.create_item(user, company, **item_form(**overrides))


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_owner_and_staff_create_and_list_items_from_the_screen(client, world, as_production_connection, who):
    with as_production_connection():
        sign_in(client, world[who])
        created = client.post(items_url(world["acme"]), item_form(name="Widget"))
        page = client.get(items_url(world["acme"])).content.decode()

    assert created.status_code == 302 and created["Location"].startswith(items_url(world["acme"]))
    assert "Widget" in page and OTHERS in page and ONE in page
    assert Item.objects.get(company=world["acme"]).name == "Widget"


def test_an_accountant_sees_the_list_without_a_form_and_cannot_post(client, world, as_production_connection):
    item = make_item(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["accountant"])
        page = client.get(items_url(world["acme"])).content.decode()
        refused = client.post(items_url(world["acme"]), item_form(name="Planted"))
        edit_refused = client.post(items_url(world["acme"], item.pk), item_form(name="Planted"))
        off_refused = client.post(items_url(world["acme"], item.pk, "deactivate"))

    assert item.name in page and 'name="name"' not in page
    assert (refused.status_code, edit_refused.status_code, off_refused.status_code) == (403, 403, 403)
    item.refresh_from_db()
    assert item.name == "Consulting hour" and item.is_active and Item.objects.count() == 1


def test_a_bad_code_shows_an_error_and_keeps_what_was_typed(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = client.post(items_url(world["acme"]), item_form(name="Typed name", classification_code="999"))

    body = response.content.decode()
    assert response.status_code == 200 and "Check the classification code." in body and "Typed name" in body
    assert Item.objects.count() == 0


def test_staff_see_the_codes_but_cannot_change_them_from_the_edit_screen(client, world, as_production_connection):
    item = make_item(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        page = client.get(items_url(world["acme"], item.pk)).content.decode()
        # a hand-made request that posts a code anyway
        forged = client.post(items_url(world["acme"], item.pk), item_form(name="Renamed", classification_code=COMPUTER))
        allowed = client.post(items_url(world["acme"], item.pk), {"name": "Renamed", "default_tax_type": SALES_TAX})

    assert OTHERS in page and 'name="classification_code"' not in page and 'name="unit_code"' not in page
    assert forged.status_code == 403 and allowed.status_code == 302
    item.refresh_from_db()
    assert (item.name, item.classification_code, item.default_tax_type) == ("Renamed", OTHERS, SALES_TAX)


def test_the_owner_changes_the_codes_from_the_edit_screen(client, world, as_production_connection):
    item = make_item(world["acme_staff"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        page = client.get(items_url(world["acme"], item.pk)).content.decode()
        saved = client.post(items_url(world["acme"], item.pk), item_form(classification_code=COMPUTER, unit_code=KILOGRAM))

    assert 'name="classification_code"' in page and 'name="unit_code"' in page
    assert saved.status_code == 302
    item.refresh_from_db()
    assert (item.classification_code, item.unit_code) == (COMPUTER, KILOGRAM)


def test_the_edit_screen_keeps_a_code_that_lhdn_has_since_retired_selected(
    client, world, as_production_connection, monkeypatch
):
    item = make_item(world["acme_owner"], world["acme"])
    monkeypatch.setattr(items, "classification_codes", lambda: {COMPUTER: "Computer, smartphone or tablet"})
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        page = client.get(items_url(world["acme"], item.pk)).content.decode()

    # otherwise the browser would submit the first option and re-code the item when the owner only meant to rename it
    assert f'<option value="{OTHERS}" selected>' in page


def test_an_item_is_deactivated_and_activated_again_from_the_screen(client, world, as_production_connection):
    item = make_item(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        off = client.post(items_url(world["acme"], item.pk, "deactivate"))
        page = client.get(items_url(world["acme"])).content.decode()
    item.refresh_from_db()
    was_active = item.is_active
    with as_production_connection():
        on = client.post(items_url(world["acme"], item.pk, "activate"))

    assert off.status_code == 302 and was_active is False and item.name in page and "Inactive" in page
    item.refresh_from_db()
    assert on.status_code == 302 and item.is_active


def test_there_is_no_way_to_delete_an_item_from_the_screens(client, world, as_production_connection):
    item = make_item(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        responses = [
            client.delete(items_url(world["acme"], item.pk)),
            client.post(items_url(world["acme"], item.pk, "delete")),
        ]

    assert [r.status_code for r in responses] == [405, 404]
    assert Item.objects.filter(pk=item.pk).exists()


def test_another_companys_item_screens_are_not_found(client, world, as_production_connection):
    mine = make_item(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        responses = [
            client.get(items_url(world["acme"])),
            client.post(items_url(world["acme"]), item_form()),
            client.get(items_url(world["acme"], mine.pk)),
            client.post(items_url(world["acme"], mine.pk), item_form(name="Hijacked")),
            # the company in the path is Beta's, the item is Acme's
            client.get(items_url(world["beta"], mine.pk)),
            client.post(items_url(world["beta"], mine.pk, "deactivate")),
        ]

    assert [r.status_code for r in responses] == [404] * 6
    mine.refresh_from_db()
    assert mine.name == "Consulting hour" and mine.is_active


def test_the_item_screens_are_available_in_chinese(client, world, as_production_connection):
    item = make_item(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        listing = client.get(items_url(world["acme"]), headers=CHINESE).content.decode()
        edit = client.get(items_url(world["acme"], item.pk), headers=CHINESE).content.decode()
        invalid = client.post(items_url(world["acme"]), item_form(unit_code="ZZZ"), headers=CHINESE).content.decode()

    assert "商品和服务" in listing and "LHDN 分类代码" in listing and "停用" in listing
    assert "LHDN 计量单位代码" in edit and "保存" in edit
    assert "请检查计量单位代码。" in invalid


def test_an_accountants_view_of_the_items_is_in_the_audit_trail_and_a_clients_is_not(
    client, world, as_production_connection
):
    with as_production_connection():
        for who in ("accountant", "acme_owner"):
            sign_in(client, world[who])
            client.get(items_url(world["acme"]))
            client.post("/logout/")

    viewed = AuditEntry.objects.filter(company=world["acme"], action=AuditEntry.VIEWED, target_type="items")
    assert [e.actor_email for e in viewed] == ["accountant@firm.example"]


def test_the_company_page_links_to_the_items(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        page = client.get(f"/companies/{world['acme'].pk}/").content.decode()

    assert f'href="{items_url(world["acme"])}"' in page
