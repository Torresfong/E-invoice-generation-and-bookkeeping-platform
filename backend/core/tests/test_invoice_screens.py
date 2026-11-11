"""The invoice draft screens (ticket #44, 07a): create, list, edit and delete a draft over HTTP.

The drafting rules are tested in test_invoices.py and the database guards in test_invoice_database.py; these tests show
the pages apply them, hide what a role may not do, and never show or touch another company's drafts (D11, D44).
"""

from decimal import Decimal

import pytest

from core import customers, invoices, items
from core.models import Invoice, InvoiceLine

from .factories import PASSWORD

OTHERS, ONE, NOT_APPLICABLE = "022", "C62", "06"


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def invoices_url(company, *rest):
    return "/".join([f"/companies/{company.pk}/invoices", *map(str, rest), ""])


def make_customer(user, company, name="Buyer Sdn Bhd"):
    return customers.create_customer(user, company, legal_name=name)


def make_item(user, company, name="Consulting hour"):
    return items.create_item(
        user, company, name=name, classification_code=OTHERS, unit_code=ONE, default_tax_type=NOT_APPLICABLE
    )


def draft_form(customer, *lines):
    """The posted form. Each line is (item, quantity, unit_price); the fields repeat once per line."""
    return {
        "customer": customer.pk,
        "item": [line[0].pk for line in lines],
        "quantity": [line[1] for line in lines],
        "unit_price": [line[2] for line in lines],
    }


@pytest.fixture
def stock(world):
    """A customer and an item in each of Acme and Beta, made by their owners."""
    return {
        "customer": make_customer(world["acme_owner"], world["acme"]),
        "item": make_item(world["acme_owner"], world["acme"]),
        "beta_customer": make_customer(world["beta_owner"], world["beta"], "Beta Buyer"),
        "beta_item": make_item(world["beta_owner"], world["beta"], "Beta service"),
    }


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_owner_and_staff_draft_an_invoice_with_no_number(client, world, stock, as_production_connection, who):
    form = draft_form(stock["customer"], (stock["item"], "2", "10.50"), (stock["item"], "0.5", "100"))
    with as_production_connection():
        sign_in(client, world[who])
        created = client.post(invoices_url(world["acme"], "new"), form)
        page = client.get(invoices_url(world["acme"])).content.decode()

    assert created.status_code == 302 and created["Location"].startswith(invoices_url(world["acme"]))
    invoice = Invoice.objects.get(company=world["acme"])
    assert invoice.status == Invoice.DRAFT and invoice.number is None
    assert invoice.customer == stock["customer"]
    assert [(line.item, line.quantity, line.unit_price) for line in invoice.lines.all()] == [
        (stock["item"], Decimal("2"), Decimal("10.50")),
        (stock["item"], Decimal("0.5"), Decimal("100")),
    ]
    assert "Buyer Sdn Bhd" in page


def make_draft(user, company, stock, customer="customer", item="item"):
    """A one-line draft (quantity 1, unit price 5) made as `user`, named by the `stock` keys of its customer and item."""
    return invoices.create_draft(
        user, company, customer_id=stock[customer].pk, lines=[{"item": stock[item].pk, "quantity": "1", "unit_price": "5"}]
    )


def test_the_owner_edits_a_draft_and_its_lines_are_replaced(client, world, stock, as_production_connection):
    draft = make_draft(world["acme_owner"], world["acme"], stock)
    other = make_customer(world["acme_owner"], world["acme"], "Second Buyer")
    other_item = make_item(world["acme_owner"], world["acme"], "Second service")
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        edited = client.post(invoices_url(world["acme"], draft.pk), draft_form(other, (other_item, "3", "7.25")))
        page = client.get(invoices_url(world["acme"], draft.pk)).content.decode()

    assert edited.status_code == 302
    draft.refresh_from_db()
    assert draft.customer == other and draft.number is None
    assert [(line.item, line.quantity, line.unit_price) for line in draft.lines.all()] == [
        (other_item, Decimal("3"), Decimal("7.25"))
    ]
    assert "Second Buyer" in page


def test_the_owner_and_staff_delete_a_draft_with_its_lines(client, world, stock, as_production_connection):
    first = make_draft(world["acme_owner"], world["acme"], stock)
    second = make_draft(world["acme_staff"], world["acme"], stock)
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        deleted = client.post(invoices_url(world["acme"], first.pk, "delete"))
        sign_in_again = client.post(invoices_url(world["acme"], second.pk, "delete"))

    assert deleted.status_code == 302 and sign_in_again.status_code == 302
    assert not Invoice.objects.filter(company=world["acme"]).exists()
    assert not InvoiceLine.objects.filter(company=world["acme"]).exists()


def test_a_bad_draft_shows_an_error_keeps_what_was_typed_and_saves_nothing(client, world, stock, as_production_connection):
    form = draft_form(stock["customer"], (stock["item"], "0", "10"))
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = client.post(invoices_url(world["acme"], "new"), form)

    page = response.content.decode()
    assert response.status_code == 200 and 'role="alert"' in page and "quantity" in page
    assert 'value="10"' in page
    assert not Invoice.objects.exists()


def test_an_accountant_reads_drafts_but_cannot_create_edit_or_delete(client, world, stock, as_production_connection):
    draft = make_draft(world["acme_owner"], world["acme"], stock)
    form = draft_form(stock["customer"], (stock["item"], "9", "9"))
    with as_production_connection():
        sign_in(client, world["accountant"])
        listing = client.get(invoices_url(world["acme"]))
        detail = client.get(invoices_url(world["acme"], draft.pk))
        refused = [
            client.get(invoices_url(world["acme"], "new")),
            client.post(invoices_url(world["acme"], "new"), form),
            client.post(invoices_url(world["acme"], draft.pk), form),
            client.post(invoices_url(world["acme"], draft.pk, "delete")),
        ]

    assert listing.status_code == 200 and detail.status_code == 200
    assert b"Delete draft" not in detail.content and b"Draft an invoice" not in listing.content
    assert [r.status_code for r in refused] == [403, 403, 403, 403]
    draft.refresh_from_db()
    assert draft.lines.get().quantity == Decimal("1") and Invoice.objects.count() == 1


def test_nobody_signed_out_sees_or_changes_drafts(client, world, stock, db):
    draft = make_draft(world["acme_owner"], world["acme"], stock)

    statuses = [
        client.get(invoices_url(world["acme"])).status_code,
        client.get(invoices_url(world["acme"], draft.pk)).status_code,
        client.post(invoices_url(world["acme"], draft.pk, "delete")).status_code,
    ]

    assert statuses == [401, 401, 401] and Invoice.objects.count() == 1


@pytest.mark.parametrize(
    "who, mine, theirs",
    [("acme_owner", "acme", "beta"), ("acme_staff", "acme", "beta"), ("beta_owner", "beta", "acme")],
)
def test_another_companys_drafts_are_invisible_and_unwritable_for_every_client_role(
    client, world, stock, as_production_connection, who, mine, theirs
):
    drafts = {
        "acme": make_draft(world["acme_owner"], world["acme"], stock),
        "beta": make_draft(world["beta_owner"], world["beta"], stock, customer="beta_customer", item="beta_item"),
    }
    their_company, their_draft = world[theirs], drafts[theirs]
    sneaky = draft_form(stock["customer"], (stock["item"], "9", "9"))
    with as_production_connection():
        sign_in(client, world[who])
        attempts = [
            client.get(invoices_url(their_company)),
            client.get(invoices_url(their_company, their_draft.pk)),
            client.post(invoices_url(their_company, their_draft.pk), sneaky),
            client.post(invoices_url(their_company, their_draft.pk, "delete")),
            client.post(invoices_url(their_company, "new"), sneaky),
            # Their draft's id under my own company is just as unreachable.
            client.get(invoices_url(world[mine], their_draft.pk)),
            client.post(invoices_url(world[mine], their_draft.pk), sneaky),
            client.post(invoices_url(world[mine], their_draft.pk, "delete")),
        ]
        own_page = client.get(invoices_url(world[mine])).content.decode()

    assert [r.status_code for r in attempts] == [404] * 8
    assert their_draft.customer.legal_name not in own_page
    assert Invoice.objects.count() == 2
    their_draft.refresh_from_db()
    assert their_draft.lines.get().quantity == Decimal("1") and their_draft.customer.company == their_company


def test_a_draft_cannot_name_another_companys_customer_or_item(client, world, stock, as_production_connection):
    mixed = [
        draft_form(stock["beta_customer"], (stock["item"], "1", "1")),
        draft_form(stock["customer"], (stock["beta_item"], "1", "1")),
    ]
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        responses = [client.post(invoices_url(world["acme"], "new"), form) for form in mixed]

    assert [r.status_code for r in responses] == [200, 200]
    assert not Invoice.objects.exists()
    assert "Beta" not in "".join(r.content.decode() for r in responses)


def test_the_draft_pages_and_their_errors_read_in_chinese_on_request(client, world, stock, as_production_connection):
    chinese = {"accept-language": "zh-hans"}
    bad = draft_form(stock["customer"], (stock["item"], "0", "10"))
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        listing = client.get(invoices_url(world["acme"]), headers=chinese).content.decode()
        form = client.get(invoices_url(world["acme"], "new"), headers=chinese).content.decode()
        refused = client.post(invoices_url(world["acme"], "new"), bad, headers=chinese).content.decode()

    assert "发票" in listing and "开具发票草稿" in listing
    assert "保存草稿" in form and "单价" in form
    assert "请检查第 1 行的数量。" in refused
