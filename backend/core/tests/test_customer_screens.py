"""The customer screens (ticket #7): list and create, edit, deactivate and activate, over HTTP.

The rules themselves are tested in test_customers.py; these tests show the screens apply them, hide what a role may not
do, refuse another company's user, and read in Chinese.
"""

import pytest

from core import customers
from core.models import AuditEntry, Customer

from .factories import PASSWORD

TIN, OTHER_TIN = "C1234567890", "IG12345678901"
BRN_VALUE = "201901234567"

CHINESE = {"accept-language": "zh-hans"}


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def form(**overrides):
    return {"legal_name": "Buyer Sdn Bhd", "tin": TIN, "id_type": "BRN", "id_value": BRN_VALUE, **overrides}


def url(company, *rest):
    return "/".join([f"/companies/{company.pk}/customers", *map(str, rest), ""])


def make(user, company, **overrides):
    return customers.create_customer(user, company, **form(**overrides))


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_owner_and_staff_create_and_list_customers_from_the_screen(client, world, as_production_connection, who):
    with as_production_connection():
        sign_in(client, world[who])
        created = client.post(url(world["acme"]), form(legal_name="Widget Buyer"))
        page = client.get(url(world["acme"])).content.decode()

    assert created.status_code == 302 and created["Location"].startswith(url(world["acme"]))
    assert "Widget Buyer" in page and TIN in page
    assert Customer.objects.get(company=world["acme"]).legal_name == "Widget Buyer"


def test_an_accountant_sees_the_list_without_a_form_and_cannot_post(client, world, as_production_connection):
    customer = make(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["accountant"])
        page = client.get(url(world["acme"])).content.decode()
        refused = client.post(url(world["acme"]), form(legal_name="Planted"))
        edit_refused = client.post(url(world["acme"], customer.pk), form(legal_name="Planted"))
        off_refused = client.post(url(world["acme"], customer.pk, "deactivate"))

    assert customer.legal_name in page and 'name="legal_name"' not in page
    assert (refused.status_code, edit_refused.status_code, off_refused.status_code) == (403, 403, 403)
    customer.refresh_from_db()
    assert customer.legal_name == "Buyer Sdn Bhd" and customer.is_active and Customer.objects.count() == 1


def test_a_tin_that_lhdn_refuses_shows_an_error_keeps_what_was_typed_and_saves_nothing(
    client, world, as_production_connection, fake_ports
):
    fake_ports.gateway.unknown_tins.add(TIN)
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = client.post(url(world["acme"]), form(legal_name="Typed name"))

    body = response.content.decode()
    assert response.status_code == 200 and "no taxpayer with this TIN" in body and "Typed name" in body
    assert Customer.objects.count() == 0


def test_staff_see_the_identity_but_cannot_change_it_from_the_edit_screen(client, world, as_production_connection):
    customer = make(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        page = client.get(url(world["acme"], customer.pk)).content.decode()
        forged = client.post(url(world["acme"], customer.pk), form(tin=OTHER_TIN))
        unchanged = client.post(url(world["acme"], customer.pk), form())

    assert TIN in page and 'name="tin"' not in page and 'name="legal_name"' not in page
    assert forged.status_code == 403 and unchanged.status_code == 302
    customer.refresh_from_db()
    assert customer.tin == TIN


def test_the_owner_edits_a_customer_and_sees_the_history(client, world, as_production_connection, as_user):
    with as_user(world["acme_staff"]):
        customer = make(world["acme_staff"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        saved = client.post(url(world["acme"], customer.pk), form(legal_name="Renamed Sdn Bhd"))
        page = client.get(url(world["acme"], customer.pk)).content.decode()

    assert saved.status_code == 302
    assert "Renamed Sdn Bhd" in page and "staff@acme.example" in page and "owner@acme.example" in page
    customer.refresh_from_db()
    assert customer.legal_name == "Renamed Sdn Bhd"


def test_staff_and_the_owner_deactivate_and_activate(client, world, as_production_connection):
    customer = make(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        off = client.post(url(world["acme"], customer.pk, "deactivate"))
        listed_off = client.get(url(world["acme"])).content.decode()
        on = client.post(url(world["acme"], customer.pk, "activate"))
        listed_on = client.get(url(world["acme"])).content.decode()
        get_refused = client.get(url(world["acme"], customer.pk, "deactivate"))

    assert (off.status_code, on.status_code, get_refused.status_code) == (302, 302, 405)
    assert "Inactive" in listed_off and "Inactive" not in listed_on


def test_another_companys_user_is_refused_every_screen(client, world, as_production_connection):
    customer = make(world["acme_owner"], world["acme"])
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        statuses = [
            client.get(url(world["acme"])).status_code,
            client.post(url(world["acme"]), form(legal_name="Planted")).status_code,
            client.get(url(world["acme"], customer.pk)).status_code,
            client.post(url(world["acme"], customer.pk, "deactivate")).status_code,
        ]

    assert all(status in (403, 404) for status in statuses)
    assert Customer.objects.filter(company=world["acme"]).count() == 1


def test_the_screens_read_in_chinese_with_the_refusal_too(client, world, as_production_connection, fake_ports):
    customer = make(world["acme_owner"], world["acme"])
    fake_ports.gateway.unknown_tins.add(OTHER_TIN)
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        listing = client.get(url(world["acme"]), headers=CHINESE).content.decode()
        edit = client.get(url(world["acme"], customer.pk), headers=CHINESE).content.decode()
        refused = client.post(url(world["acme"]), form(tin=OTHER_TIN), headers=CHINESE).content.decode()

    assert "客户" in listing and "税务识别号" in listing and "停用" in listing
    assert "法定名称" in edit and "保存" in edit
    assert "没有使用该税务识别号" in refused


def test_an_accountants_view_of_the_customers_is_in_the_audit_trail_and_a_clients_is_not(
    client, world, as_production_connection
):
    customer = make(world["acme_owner"], world["acme"])
    with as_production_connection():
        for who in ("accountant", "acme_owner"):
            sign_in(client, world[who])
            client.get(url(world["acme"]))
            client.get(url(world["acme"], customer.pk))
            client.post("/logout/")

    viewed = AuditEntry.objects.filter(
        company=world["acme"], action=AuditEntry.VIEWED, target_type__in=("customers", "customer")
    )
    assert {(e.target_type, e.actor_email) for e in viewed} == {
        ("customers", "accountant@firm.example"), ("customer", "accountant@firm.example"),
    }


def test_the_company_page_links_to_the_customers(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        page = client.get(f"/companies/{world['acme'].pk}/").content.decode()

    assert f'href="{url(world["acme"])}"' in page
