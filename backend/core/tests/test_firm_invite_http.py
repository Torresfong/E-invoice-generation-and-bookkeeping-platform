"""The HTTP side of ticket #144 (D67 (3)): the owner invites by email and sees status; the firm admin accepts.

Mirrors the style of test_invitations.py, over the test client with as_production_connection, so the full
request path (permissions, RLS, redirects, templates) is proven end to end.
"""

import re

import pytest
from django.test import Client

from core.models import CompanyAccess, Firm, User

from .factories import PASSWORD, make_company, make_member

FIRM_EMAIL = "lim@limpartners.example"


@pytest.fixture
def world(db):
    acme = make_company("Acme Sdn Bhd", firm=False)
    beta = make_company("Beta Trading")  # already has the default test firm
    return {
        "acme": acme,
        "beta": beta,
        "acme_owner": make_member("owner@acme.example", acme, CompanyAccess.OWNER),
        "acme_staff": make_member("staff@acme.example", acme, CompanyAccess.STAFF),
        "beta_owner": make_member("owner@beta.example", beta, CompanyAccess.OWNER),
    }


def sign_in(client, user, password=PASSWORD):
    return client.post("/login/", {"email": user.email, "password": password})


def invite(client, company, email):
    return client.post(f"/companies/{company.pk}/invite-firm/", {"email": email})


def invitation_link(fake_ports):
    body = fake_ports.email.sent[-1]["body"]
    return re.search(r"(/firm-invite/[\w-]+/)", body).group(1)


def firm_admin_user(email, firm_name="Lim & Partners"):
    firm = Firm.objects.create(name=firm_name)
    admin = User.objects.create_user(email, password=PASSWORD, is_firm_admin=True, firm=firm)
    return admin


def test_an_owner_can_invite_a_firm_and_sees_it_pending(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = invite(client, world["acme"], FIRM_EMAIL)
        page = client.get(f"/companies/{world['acme'].pk}/").content.decode()

    assert response.status_code == 302
    assert [mail["to"] for mail in fake_ports.email.sent] == [FIRM_EMAIL]
    assert "Sent, waiting for the firm" in page


def test_client_staff_cannot_invite_a_firm(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        response = invite(client, world["acme"], FIRM_EMAIL)

    assert response.status_code == 403
    assert fake_ports.email.sent == []


def test_an_owner_cannot_invite_a_firm_for_another_company(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        response = invite(client, world["acme"], FIRM_EMAIL)

    assert response.status_code == 404
    assert fake_ports.email.sent == []


def test_a_company_that_already_has_a_firm_cannot_send_an_invitation_over_http(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        response = invite(client, world["beta"], FIRM_EMAIL)

    assert response.status_code == 200 and "already has a firm" in response.content.decode()
    assert fake_ports.email.sent == []


def test_a_firm_administrator_accepts_and_no_companyaccess_row_is_created(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], FIRM_EMAIL)
    link = invitation_link(fake_ports)
    admin = firm_admin_user(FIRM_EMAIL)

    with as_production_connection():
        accepting = Client()
        sign_in(accepting, admin)
        get_response = accepting.get(link)
        post_response = accepting.post(link)

    assert "Accept" in get_response.content.decode()
    assert post_response.status_code == 302 and post_response["Location"] == "/firm/?accepted=1"
    world["acme"].refresh_from_db()
    assert world["acme"].firm is None  # the operator's review sets this, not acceptance (D67 (3))
    assert not CompanyAccess.objects.filter(company=world["acme"], user=admin).exists()


def test_a_signed_out_visitor_is_offered_sign_in_or_registration(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], FIRM_EMAIL)
    link = invitation_link(fake_ports)

    with as_production_connection():
        response = Client().get(link)

    assert response.status_code == 200
    body = response.content.decode()
    assert f"/login/?next=/firm-invite/{link.split('/')[-2]}/" in body
    assert "/apply/" in body and "<form" not in body


def test_a_signed_in_non_firm_admin_is_refused_and_offered_registration(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], FIRM_EMAIL)
    link = invitation_link(fake_ports)

    with as_production_connection():
        wrong = Client()
        sign_in(wrong, world["beta_owner"])
        get_response = wrong.get(link)
        post_response = wrong.post(link)

    assert "administrator" in get_response.content.decode()
    assert 'action="/logout/"' in get_response.content.decode()
    assert post_response.status_code == 200 and "administrator" in post_response.content.decode()
    world["acme"].refresh_from_db()
    assert world["acme"].firm is None


def test_a_made_up_firm_invite_link_is_refused(client, world, as_production_connection):
    admin = firm_admin_user(FIRM_EMAIL)
    with as_production_connection():
        accepting = Client()
        sign_in(accepting, admin)
        response = accepting.post("/firm-invite/not-a-real-token/")

    assert response.status_code == 410


def test_the_firm_side_invalid_link_page_stays_english_for_a_chinese_browser(client, world, as_production_connection):
    """Firm-side pages are English only (D49), unlike the shared `link_invalid.html` the client side uses."""
    admin = firm_admin_user(FIRM_EMAIL)
    with as_production_connection():
        accepting = Client()
        sign_in(accepting, admin)
        response = accepting.post("/firm-invite/not-a-real-token/", headers={"accept-language": "zh-hans"})

    body = response.content.decode()
    assert response.status_code == 410 and "This link no longer works" in body
