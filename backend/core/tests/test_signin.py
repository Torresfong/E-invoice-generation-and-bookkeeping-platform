from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from core import throttle
from core.models import CompanyAccess, LoginFailure

from .factories import PASSWORD, default_firm, make_company, make_member


@pytest.fixture
def acme_owner(db):
    return make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)


def sign_in(client, email, password):
    return client.post("/login/", {"email": email, "password": password})


def test_a_user_signs_in_with_email_and_password_and_reaches_the_home_page(client, acme_owner, as_production_connection):
    with as_production_connection():
        response = sign_in(client, "owner@acme.example", PASSWORD)
        assert response.status_code == 302
        assert client.get("/").status_code == 200


def test_the_email_is_matched_ignoring_letter_case(client, acme_owner, as_production_connection):
    with as_production_connection():
        assert sign_in(client, "Owner@ACME.example", PASSWORD).status_code == 302


def test_a_wrong_password_does_not_sign_in(client, acme_owner, as_production_connection):
    with as_production_connection():
        response = sign_in(client, "owner@acme.example", "not the password")
        assert response.status_code == 200
        assert client.get("/").status_code == 401


def test_an_unknown_email_gets_the_same_answer_as_a_wrong_password(client, acme_owner, as_production_connection):
    with as_production_connection():
        wrong = sign_in(client, "owner@acme.example", "not the password")
        unknown = sign_in(client, "nobody@acme.example", "not the password")

    message = "The email address or password is not correct."
    assert unknown.status_code == wrong.status_code == 200
    assert message in unknown.content.decode() and message in wrong.content.decode()


def test_an_inactive_user_cannot_sign_in(client, acme_owner, as_production_connection):
    acme_owner.is_active = False
    acme_owner.save()

    with as_production_connection():
        assert sign_in(client, "owner@acme.example", PASSWORD).status_code == 200
        assert client.get("/").status_code == 401


def test_signing_out_ends_the_session(client, acme_owner, as_production_connection):
    with as_production_connection():
        sign_in(client, "owner@acme.example", PASSWORD)
        assert client.post("/logout/").status_code == 302
        assert client.get("/").status_code == 401


def test_signing_out_needs_a_post_request(client, acme_owner, as_production_connection):
    with as_production_connection():
        sign_in(client, "owner@acme.example", PASSWORD)
        assert client.get("/logout/").status_code == 405
        assert client.get("/").status_code == 200


def fail_times(client, email, times):
    for _ in range(times):
        sign_in(client, email, "not the password")


def test_five_failed_attempts_lock_sign_in_even_for_the_right_password(client, acme_owner, as_production_connection):
    with as_production_connection():
        fail_times(client, "owner@acme.example", 5)

        response = sign_in(client, "owner@acme.example", PASSWORD)

        assert response.status_code == 429
        assert client.get("/").status_code == 401


def test_four_failed_attempts_do_not_lock_sign_in(client, acme_owner, as_production_connection):
    with as_production_connection():
        fail_times(client, "owner@acme.example", 4)

        assert sign_in(client, "owner@acme.example", PASSWORD).status_code == 302


def test_failed_attempts_on_one_address_do_not_lock_another(client, acme_owner, as_production_connection):
    make_member("staff@acme.example", acme_owner.company_access.get().company, CompanyAccess.STAFF)

    with as_production_connection():
        fail_times(client, "owner@acme.example", 5)

        assert sign_in(client, "staff@acme.example", PASSWORD).status_code == 302


def test_the_lock_ends_after_the_window(client, acme_owner, as_production_connection):
    with as_production_connection():
        fail_times(client, "owner@acme.example", 5)
    LoginFailure.objects.update(at=timezone.now() - timedelta(minutes=16))

    with as_production_connection():
        assert sign_in(client, "owner@acme.example", PASSWORD).status_code == 302


def test_a_successful_sign_in_resets_the_count(client, acme_owner, as_production_connection):
    with as_production_connection():
        fail_times(client, "owner@acme.example", 4)
        sign_in(client, "owner@acme.example", PASSWORD)
        client.post("/logout/")
        fail_times(client, "owner@acme.example", 4)

        assert sign_in(client, "owner@acme.example", PASSWORD).status_code == 302


def test_failed_attempts_are_stored_without_the_email_address(client, acme_owner, as_production_connection):
    with as_production_connection():
        fail_times(client, "Owner@Acme.example", 1)

    stored = LoginFailure.objects.get().email_hash
    assert len(stored) == 64 and "acme" not in stored


def test_throttling_applies_to_addresses_that_have_no_account_too(client, db, as_production_connection):
    with as_production_connection():
        fail_times(client, "nobody@acme.example", 5)

        assert sign_in(client, "nobody@acme.example", PASSWORD).status_code == 429


def test_the_sixth_attempt_for_an_address_is_refused_and_a_clear_resets_the_count(db):
    assert [throttle.allow_attempt("owner@acme.example") for _ in range(6)] == [True] * 5 + [False]

    throttle.clear("owner@acme.example")

    assert throttle.allow_attempt("owner@acme.example")


def test_an_accountant_who_has_not_chosen_a_password_cannot_sign_in(client, db, fake_ports, as_production_connection):
    make_company()
    call_command("create_accountant", "accountant@firm.example", default_firm().name)

    with as_production_connection():
        response = sign_in(client, "accountant@firm.example", "")
        guessed = sign_in(client, "accountant@firm.example", "anything at all")

    assert response.status_code == guessed.status_code == 200
