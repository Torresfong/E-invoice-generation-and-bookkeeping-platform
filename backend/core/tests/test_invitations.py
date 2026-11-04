import re
from datetime import timedelta

import pytest
from django.db import DatabaseError, connection, transaction
from django.test import Client
from django.utils import timezone

from core.models import AuthToken, CompanyAccess, User

from .factories import PASSWORD, make_company, make_member

NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def world(db):
    acme, beta = make_company("Acme Sdn Bhd"), make_company("Beta Trading")
    return {
        "acme": acme,
        "beta": beta,
        "acme_owner": make_member("owner@acme.example", acme, CompanyAccess.OWNER),
        "acme_staff": make_member("staff@acme.example", acme, CompanyAccess.STAFF),
        "beta_owner": make_member("owner@beta.example", beta, CompanyAccess.OWNER),
        "accountant": make_member("accountant@firm.example", acme, CompanyAccess.ACCOUNTANT),
    }


def sign_in(client, user, password=PASSWORD):
    return client.post("/login/", {"email": user.email, "password": password})


def invite(client, company, email, **extra):
    return client.post(f"/companies/{company.pk}/invite/", {"email": email, **extra})


def accept_link(fake_ports):
    body = fake_ports.email.sent[-1]["body"]
    return re.search(r"http://testserver(/accept/[\w-]+/)", body).group(1)


def test_an_owner_can_invite_a_colleague_by_email(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = invite(client, world["acme"], "new.staff@acme.example")

    assert response.status_code == 302
    assert [mail["to"] for mail in fake_ports.email.sent] == ["new.staff@acme.example"]
    assert "/accept/" in fake_ports.email.sent[0]["body"] and "Acme Sdn Bhd" in fake_ports.email.sent[0]["body"]


def test_an_invitation_whose_email_cannot_be_sent_leaves_no_invitation(
    client, world, email_down, as_production_connection
):
    client.raise_request_exception = False
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = invite(client, world["acme"], "new.staff@acme.example")

    assert response.status_code == 500
    assert AuthToken.objects.count() == 0


@pytest.mark.parametrize("who", ["acme_staff", "accountant"])
def test_staff_and_accountants_cannot_invite(client, world, fake_ports, who, as_production_connection):
    with as_production_connection():
        sign_in(client, world[who])
        response = invite(client, world["acme"], "new.staff@acme.example")

    assert response.status_code == 403
    assert fake_ports.email.sent == [] and AuthToken.objects.count() == 0


def test_an_owner_cannot_invite_into_another_company(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        response = invite(client, world["acme"], "new.staff@acme.example")

    assert response.status_code == 404
    assert fake_ports.email.sent == []


def test_an_address_that_already_has_an_account_gets_no_email_and_the_owner_is_not_told(
    client, world, fake_ports, as_production_connection
):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        taken = invite(client, world["acme"], "OWNER@beta.example")
        fresh = invite(client, world["acme"], "fresh@acme.example")

    assert taken.status_code == fresh.status_code == 302 and taken["Location"] == fresh["Location"]
    assert [mail["to"] for mail in fake_ports.email.sent] == ["fresh@acme.example"]


def test_a_malformed_address_is_refused(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = invite(client, world["acme"], "not-an-address")

    assert response.status_code == 200 and fake_ports.email.sent == []


def test_a_company_can_send_twenty_invitations_a_day_and_no_more(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        for number in range(20):
            assert invite(client, world["acme"], f"person{number}@acme.example").status_code == 302
        response = invite(client, world["acme"], "one.too.many@acme.example")

    assert response.status_code == 200 and "Too many invitations" in response.content.decode()
    assert len(fake_ports.email.sent) == 20


def test_links_point_at_the_configured_address_even_if_the_request_names_another_host(
    client, world, fake_ports, settings, as_production_connection
):
    settings.ALLOWED_HOSTS = ["testserver", "evil.example"]
    settings.BASE_URL = "https://app.example"
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        client.post(
            f"/companies/{world['acme'].pk}/invite/", {"email": "new@acme.example"}, headers={"host": "evil.example"}
        )

    body = fake_ports.email.sent[0]["body"]
    assert "https://app.example/accept/" in body and "evil.example" not in body


def test_the_invitee_chooses_a_password_and_joins_as_staff(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], "new.staff@acme.example")
        link = accept_link(fake_ports)
        newcomer = Client()

        assert newcomer.get(link).status_code == 200
        assert newcomer.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 302
        assert newcomer.post("/login/", {"email": "new.staff@acme.example", "password": NEW_PASSWORD}).status_code == 302

        page = newcomer.get("/companies/").content.decode()
        assert "Acme Sdn Bhd" in page and "Client staff" in page and "Beta Trading" not in page

    assert CompanyAccess.objects.get(user__email="new.staff@acme.example").role == CompanyAccess.STAFF


def test_the_role_cannot_be_chosen_in_the_invitation(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], "sneaky@acme.example", role="owner")
        Client().post(accept_link(fake_ports), {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

    assert CompanyAccess.objects.get(user__email="sneaky@acme.example").role == CompanyAccess.STAFF
    assert CompanyAccess.objects.filter(company=world["acme"], role=CompanyAccess.OWNER).count() == 1


def test_an_invitation_link_works_only_once(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], "new.staff@acme.example")
        link = accept_link(fake_ports)
        Client().post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

        assert Client().get(link).status_code == 410
        assert Client().post(link, {"password": "another passphrase", "confirm": "another passphrase"}).status_code == 410

    assert User.objects.filter(email="new.staff@acme.example").count() == 1


def test_an_invitation_expires_after_a_week(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], "new.staff@acme.example")
    link = accept_link(fake_ports)
    stored = AuthToken.objects.get()
    assert timedelta(days=6, hours=23) < stored.expires_at - timezone.now() <= timedelta(days=7)
    AuthToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    with as_production_connection():
        assert Client().get(link).status_code == 410
        assert Client().post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 410

    assert not User.objects.filter(email="new.staff@acme.example").exists()


def test_a_made_up_invitation_link_is_refused(client, world, as_production_connection):
    with as_production_connection():
        assert Client().get("/accept/not-a-real-token/").status_code == 410


def test_the_invitation_token_is_stored_only_as_a_hash(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        invite(client, world["acme"], "new.staff@acme.example")
    token = accept_link(fake_ports).split("/")[2]

    stored = AuthToken.objects.get()
    assert token not in stored.token_hash and len(stored.token_hash) == 64
    assert stored.purpose == AuthToken.INVITE and stored.role == CompanyAccess.STAFF


def test_the_database_refuses_an_invitation_from_someone_who_is_not_the_owner(world, as_user):
    with as_user(world["acme_staff"]), connection.cursor() as cursor:
        with pytest.raises(DatabaseError), transaction.atomic():
            cursor.execute(
                "SELECT create_invitation(%s, %s, %s, now() + interval '1 day')",
                ["f" * 64, "x@acme.example", world["acme"].pk],
            )


def test_the_database_refuses_an_invitation_into_a_company_the_caller_does_not_own(world, as_user):
    with as_user(world["beta_owner"]), connection.cursor() as cursor:
        with pytest.raises(DatabaseError), transaction.atomic():
            cursor.execute(
                "SELECT create_invitation(%s, %s, %s, now() + interval '1 day')",
                ["f" * 64, "x@acme.example", world["acme"].pk],
            )
