import re
from datetime import timedelta

import pytest
from django.test import Client
from django.utils import timezone

from core.models import AuthToken, CompanyAccess

from .factories import PASSWORD, make_company, make_member

NEW_PASSWORD = "a brand new passphrase"


@pytest.fixture
def acme_owner(db):
    return make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)


def request_reset(client, email):
    return client.post("/password-reset/", {"email": email})


def reset_link(fake_ports):
    body = fake_ports.email.sent[-1]["body"]
    return re.search(r"http://testserver(/reset/[\w-]+/)", body).group(1)


def sign_in(client, email, password):
    return client.post("/login/", {"email": email, "password": password})


def test_a_known_address_is_emailed_a_reset_link(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        response = request_reset(client, "owner@acme.example")

    assert response.status_code == 200
    assert [mail["to"] for mail in fake_ports.email.sent] == ["owner@acme.example"]
    assert "/reset/" in fake_ports.email.sent[0]["body"]


def test_a_reset_request_whose_email_cannot_be_sent_leaves_no_token(
    client, acme_owner, email_down, as_production_connection
):
    client.raise_request_exception = False
    with as_production_connection():
        response = request_reset(client, "owner@acme.example")

    assert response.status_code == 500
    assert AuthToken.objects.count() == 0


def test_an_unknown_address_gets_the_same_answer_and_no_email(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        known = request_reset(client, "owner@acme.example")
        unknown = request_reset(client, "nobody@acme.example")

    assert unknown.status_code == known.status_code
    assert len(fake_ports.email.sent) == 1


def test_the_link_lets_the_user_choose_a_new_password(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        request_reset(client, "owner@acme.example")
        link = reset_link(fake_ports)

        assert client.get(link).status_code == 200
        response = client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

        assert response.status_code == 302
        assert sign_in(client, "owner@acme.example", PASSWORD).status_code == 200
        assert sign_in(client, "owner@acme.example", NEW_PASSWORD).status_code == 302


def test_a_reset_link_works_only_once(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        request_reset(client, "owner@acme.example")
        link = reset_link(fake_ports)
        client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

        assert client.get(link).status_code == 410
        assert client.post(link, {"password": "another passphrase", "confirm": "another passphrase"}).status_code == 410
        assert sign_in(client, "owner@acme.example", NEW_PASSWORD).status_code == 302


def test_an_expired_reset_link_is_refused(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        request_reset(client, "owner@acme.example")
    link = reset_link(fake_ports)
    AuthToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    with as_production_connection():
        assert client.get(link).status_code == 410
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 410
        assert sign_in(client, "owner@acme.example", PASSWORD).status_code == 302


def test_a_made_up_reset_link_is_refused(client, acme_owner, as_production_connection):
    with as_production_connection():
        assert client.get("/reset/not-a-real-token/").status_code == 410


def test_the_token_is_stored_only_as_a_hash(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        request_reset(client, "owner@acme.example")
    token = reset_link(fake_ports).split("/")[2]

    stored = AuthToken.objects.get()
    assert token not in stored.token_hash and len(stored.token_hash) == 64


def test_a_weak_password_is_refused_and_the_link_still_works(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        request_reset(client, "owner@acme.example")
        link = reset_link(fake_ports)

        assert client.post(link, {"password": "short", "confirm": "short"}).status_code == 200
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": "different"}).status_code == 200
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 302


def test_a_reset_signs_out_sessions_opened_with_the_old_password(
    client, django_assert_num_queries, acme_owner, fake_ports, as_production_connection
):
    other_device = Client()
    with as_production_connection():
        sign_in(other_device, "owner@acme.example", PASSWORD)
        assert other_device.get("/").status_code == 200

        request_reset(client, "owner@acme.example")
        client.post(reset_link(fake_ports), {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD})

        assert other_device.get("/").status_code == 401


def test_reset_requests_for_one_address_are_limited(client, acme_owner, fake_ports, as_production_connection):
    with as_production_connection():
        for _ in range(6):
            request_reset(client, "owner@acme.example")

    assert len(fake_ports.email.sent) == 3


def test_a_link_issued_before_the_account_was_deactivated_no_longer_works(
    client, acme_owner, fake_ports, as_production_connection
):
    with as_production_connection():
        request_reset(client, "owner@acme.example")
    link = reset_link(fake_ports)
    acme_owner.is_active = False
    acme_owner.save()

    with as_production_connection():
        assert client.get(link).status_code == 200  # the link itself is still valid
        assert client.post(link, {"password": NEW_PASSWORD, "confirm": NEW_PASSWORD}).status_code == 410

    acme_owner.refresh_from_db()
    assert acme_owner.check_password(PASSWORD)
