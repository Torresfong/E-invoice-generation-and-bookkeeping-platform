"""Owner self-registration through a firm's sign-up link (ticket 02f; D48, D49 (4)).

Everything goes over HTTP with the email port faked. The sign-up setting is off by default, so tests that exercise
the flow turn it on with the `signup_open` fixture.
"""

import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from django.test import Client
from django.utils import timezone

from core.models import AuditEntry, AuthToken, Company, CompanyAccess, FirmAuditEntry, User

from .factories import PASSWORD, make_member


@pytest.fixture
def signup_open(settings):
    settings.SIGNUP_ENABLED = True


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def new_link(firm_accountant):
    """The sign-up path a firm accountant gets from the links page."""
    client = Client()
    sign_in(client, firm_accountant)
    response = client.post("/signup-links/")
    assert response.status_code == 200
    return re.search(r"http://testserver(/signup/[\w-]+/)", response.content.decode()).group(1)


# --- the firm's sign-up link -------------------------------------------------------------------------------------


def test_an_accountant_creates_a_sign_up_link_for_their_own_firm(firms_world, as_production_connection):
    with as_production_connection():
        client = Client()
        sign_in(client, firms_world["alpha_accountant"])
        response = client.post("/signup-links/")

    body = response.content.decode()
    path = re.search(r"http://testserver(/signup/[\w-]+/)", body).group(1)
    stored = AuthToken.objects.get(purpose="signup")
    assert stored.firm == firms_world["alpha"] and stored.used_at is None
    assert path.split("/")[2] not in stored.token_hash and len(stored.token_hash) == 64  # only the hash is kept
    assert FirmAuditEntry.objects.filter(firm=firms_world["alpha"], target_type="signup_link", action="create").count() == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("who", ["owner@acme.example", "staff@acme.example"])
def test_owners_and_staff_cannot_create_sign_up_links(firms_world, as_production_connection, who, method):
    make_member("staff@acme.example", firms_world["acme"], CompanyAccess.STAFF)
    with as_production_connection():
        client = Client()
        assert client.post("/login/", {"email": who, "password": PASSWORD}).status_code == 302
        response = client.generic(method, "/signup-links/")

    assert response.status_code == 403 and AuthToken.objects.count() == 0


def test_the_links_page_needs_a_signed_in_user(firms_world, as_production_connection):
    with as_production_connection():
        response = Client().post("/signup-links/")

    assert response.status_code == 401 and AuthToken.objects.count() == 0


# --- the sign-up form: a pending registration, nothing else ------------------------------------------------------


def submit(client, path, email="new.owner@client.example", name="Client Sdn Bhd"):
    return client.post(path, {"email": email, "company_name": name})


def register_path(fake_ports):
    return re.search(r"http://testserver(/register/[\w-]+/)", fake_ports.email.sent[-1]["body"]).group(1)


def counts():
    return Company.objects.count(), User.objects.count(), CompanyAccess.objects.count()


def test_submitting_the_form_creates_no_company_or_user_and_emails_a_verification_link(
    firms_world, signup_open, fake_ports, as_production_connection
):
    path = new_link(firms_world["alpha_accountant"])
    before = counts()

    with as_production_connection():
        form = Client().get(path)
        response = submit(Client(), path)

    assert form.status_code == 200 and response.status_code == 200
    assert counts() == before
    assert [mail["to"] for mail in fake_ports.email.sent] == ["new.owner@client.example"]
    raw = register_path(fake_ports).split("/")[2]
    pending = AuthToken.objects.get(purpose="register")
    assert pending.firm == firms_world["alpha"] and pending.company_name == "Client Sdn Bhd"
    assert pending.token_hash != raw and raw not in pending.email + pending.company_name  # only the hash is kept


@pytest.mark.parametrize("path", ["/signup/not-a-real-link/", "/signup/" + "a" * 43 + "/"])
def test_a_sign_up_without_a_valid_firm_link_is_refused(firms_world, signup_open, fake_ports, as_production_connection, path):
    before = counts()
    with as_production_connection():
        form = Client().get(path)
        response = submit(Client(), path)

    assert form.status_code == response.status_code == 410
    assert counts() == before and fake_ports.email.sent == [] and not AuthToken.objects.filter(purpose="register").exists()


def test_a_firm_link_works_once(firms_world, signup_open, fake_ports, as_production_connection):
    path = new_link(firms_world["alpha_accountant"])
    with as_production_connection():
        first = submit(Client(), path)
        second = submit(Client(), path, email="another@client.example")

    assert first.status_code == 200 and second.status_code == 410
    assert [mail["to"] for mail in fake_ports.email.sent] == ["new.owner@client.example"]


def test_an_expired_firm_link_is_refused(firms_world, signup_open, fake_ports, as_production_connection):
    path = new_link(firms_world["alpha_accountant"])
    AuthToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))

    with as_production_connection():
        response = submit(Client(), path)

    assert response.status_code == 410 and fake_ports.email.sent == []


def test_an_address_that_already_has_an_account_gets_the_same_answer_and_no_email(
    firms_world, signup_open, fake_ports, as_production_connection
):
    taken_path, fresh_path = new_link(firms_world["alpha_accountant"]), new_link(firms_world["alpha_accountant"])
    with as_production_connection():
        taken = submit(Client(), taken_path, email="OWNER@gamma.example")
        fresh = submit(Client(), fresh_path)

    assert taken.status_code == fresh.status_code == 200
    assert taken.content == fresh.content
    assert [mail["to"] for mail in fake_ports.email.sent] == ["new.owner@client.example"]
    assert AuthToken.objects.filter(purpose="register").count() == 1


@pytest.mark.parametrize("email, name", [("not-an-email", "Client Sdn Bhd"), ("new.owner@client.example", "   ")])
def test_the_form_asks_again_for_an_invalid_address_or_a_blank_company_name(
    firms_world, signup_open, fake_ports, as_production_connection, email, name
):
    path = new_link(firms_world["alpha_accountant"])
    with as_production_connection():
        client = Client()
        refused = submit(client, path, email=email, name=name)
        retry = submit(client, path)

    assert refused.status_code == 200 and b'role="alert"' in refused.content
    assert retry.status_code == 200 and [mail["to"] for mail in fake_ports.email.sent] == ["new.owner@client.example"]


def test_with_sign_up_switched_off_the_screen_says_registration_is_closed(firms_world, settings, fake_ports, as_production_connection):
    assert settings.SIGNUP_ENABLED is False  # the default
    path = new_link(firms_world["alpha_accountant"])
    before = counts()

    with as_production_connection():
        form = Client().get(path)
        response = submit(Client(), path)

    assert form.status_code == response.status_code == 403 and b"closed" in response.content
    assert counts() == before and fake_ports.email.sent == [] and not AuthToken.objects.filter(purpose="register").exists()
    assert AuthToken.objects.get(purpose="signup").used_at is None  # the link was not spent


# --- the verification link: the company, the owner and the membership appear together --------------------------------

OWNER_PASSWORD = "a long enough passphrase"


def pending_registration(firms_world, fake_ports, **form):
    """Run the form for the alpha firm and return the verification path from the email."""
    submit(Client(), new_link(firms_world["alpha_accountant"]), **form)
    return register_path(fake_ports)


def verify(client, path, password=OWNER_PASSWORD, confirm=None):
    return client.post(path, {"password": password, "confirm": password if confirm is None else confirm})


def test_opening_the_link_with_a_valid_password_creates_the_company_and_the_owner_and_signs_them_in(
    firms_world, signup_open, fake_ports, as_production_connection
):
    path = pending_registration(firms_world, fake_ports)
    before = counts()

    with as_production_connection():
        client = Client()
        form = client.get(path)
        response = verify(client, path)
        listing = client.get("/companies/")

    assert form.status_code == 200 and response.status_code == 302
    company = Company.objects.get(name="Client Sdn Bhd")
    owner = User.objects.get(email="new.owner@client.example")
    assert counts() == (before[0] + 1, before[1] + 1, before[2] + 2)  # the owner's row and the alpha accountant's row
    assert company.firm == firms_world["alpha"]
    assert CompanyAccess.objects.get(user=owner, company=company).role == CompanyAccess.OWNER
    assert owner.check_password(OWNER_PASSWORD) and not owner.is_accountant and owner.firm is None
    assert listing.status_code == 200 and "Client Sdn Bhd" in listing.content.decode()  # signed in by the same step as sign-in


def test_only_the_links_firm_gets_access_to_the_new_company(firms_world, signup_open, fake_ports, as_production_connection):
    path = pending_registration(firms_world, fake_ports)
    with as_production_connection():
        verify(Client(), path)

    company = Company.objects.get(name="Client Sdn Bhd")
    accountants = set(User.objects.filter(company_access__company=company, company_access__role="accountant"))
    assert accountants == {firms_world["alpha_accountant"]}
    assert not CompanyAccess.objects.filter(user=firms_world["beta_accountant"], company=company).exists()


def test_the_new_owner_sees_only_their_own_company(firms_world, signup_open, fake_ports, as_production_connection):
    path = pending_registration(firms_world, fake_ports)

    client = Client()
    with as_production_connection():
        verify(client, path)
    own_pk = Company.objects.get(name="Client Sdn Bhd").pk  # read as the table owner, outside the production connection

    with as_production_connection():
        own = client.get(f"/companies/{own_pk}/")
        others = [client.get(f"/companies/{firms_world[name].pk}/") for name in ("acme", "gamma")]
        listing = client.get("/companies/").content.decode()

    assert own.status_code == 200
    assert [response.status_code for response in others] == [404, 404]
    assert "Acme Sdn Bhd" not in listing and "Gamma Holdings" not in listing


def test_registration_is_recorded_in_the_company_and_firm_trails_without_values(
    firms_world, signup_open, fake_ports, as_production_connection
):
    path = pending_registration(firms_world, fake_ports)
    with as_production_connection():
        verify(Client(), path)

    company = Company.objects.get(name="Client Sdn Bhd")
    created = AuditEntry.objects.get(company=company, action="create", target_type="company")
    assert created.actor_email == "new.owner@client.example" and created.changed_fields == ["name", "firm"]
    firm_events = FirmAuditEntry.objects.filter(firm=firms_world["alpha"]).values_list("target_type", "action")
    assert {("signup_link", "create"), ("registration", "create"), ("company", "create")} <= set(firm_events)
    everything = " ".join(str(fields) for fields in FirmAuditEntry.objects.values_list("changed_fields", flat=True))
    assert "new.owner@client.example" not in everything and "Client Sdn Bhd" not in everything


def test_a_verification_link_works_once(firms_world, signup_open, fake_ports, as_production_connection):
    path = pending_registration(firms_world, fake_ports)
    with as_production_connection():
        first = verify(Client(), path)
        second_form, second = Client().get(path), verify(Client(), path, "another long passphrase")

    assert first.status_code == 302 and second_form.status_code == second.status_code == 410
    assert Company.objects.filter(name="Client Sdn Bhd").count() == 1


def test_an_expired_verification_link_is_refused_and_creates_nothing(firms_world, signup_open, fake_ports, as_production_connection):
    path = pending_registration(firms_world, fake_ports)
    AuthToken.objects.filter(purpose="register").update(expires_at=timezone.now() - timedelta(minutes=1))
    before = counts()

    with as_production_connection():
        form, response = Client().get(path), verify(Client(), path)

    assert form.status_code == response.status_code == 410 and counts() == before


@pytest.mark.parametrize(
    "tamper",
    [lambda raw: raw[:-1] + ("a" if raw[-1] != "a" else "b"), lambda raw: raw + "x", lambda raw: "x" * len(raw)],
)
def test_a_tampered_verification_link_is_refused_and_creates_nothing(
    firms_world, signup_open, fake_ports, as_production_connection, tamper
):
    path = pending_registration(firms_world, fake_ports)
    raw = path.split("/")[2]
    before = counts()

    with as_production_connection():
        response = verify(Client(), f"/register/{tamper(raw)}/")

    assert response.status_code == 410 and counts() == before


def test_a_link_only_works_for_its_own_purpose(firms_world, signup_open, fake_ports, as_production_connection):
    signup_raw = new_link(firms_world["alpha_accountant"]).split("/")[2]
    register_raw = pending_registration(firms_world, fake_ports).split("/")[2]
    before = counts()

    with as_production_connection():
        # A firm sign-up link is not a verification link, and a verification link is not a firm sign-up link.
        wrong = [verify(Client(), f"/register/{signup_raw}/"), submit(Client(), f"/signup/{register_raw}/")]

    assert [response.status_code for response in wrong] == [410, 410] and counts() == before


@pytest.mark.parametrize(
    "password, confirm",
    [("a long enough passphrase", "a different passphrase"), ("short", "short"), ("1234567890123", "1234567890123")],
)
def test_a_password_that_does_not_match_or_is_too_weak_creates_nothing_and_keeps_the_link(
    firms_world, signup_open, fake_ports, as_production_connection, password, confirm
):
    path = pending_registration(firms_world, fake_ports)
    before = counts()

    with as_production_connection():
        refused = verify(Client(), path, password, confirm)
    assert counts() == before
    with as_production_connection():
        retry = verify(Client(), path)

    assert refused.status_code == 200 and b'role="alert"' in refused.content
    assert retry.status_code == 302 and Company.objects.filter(name="Client Sdn Bhd").count() == 1


def test_an_address_registered_while_the_link_was_waiting_creates_nothing(firms_world, signup_open, fake_ports, as_production_connection):
    path = pending_registration(firms_world, fake_ports)
    make_member("new.owner@client.example", firms_world["gamma"], CompanyAccess.STAFF)  # joined by invitation meanwhile
    before = counts()

    with as_production_connection():
        response = verify(Client(), path)

    assert response.status_code == 410 and counts() == before


def test_with_sign_up_switched_off_a_pending_registration_cannot_be_completed(
    firms_world, settings, signup_open, fake_ports, as_production_connection
):
    path = pending_registration(firms_world, fake_ports)
    settings.SIGNUP_ENABLED = False
    before = counts()

    with as_production_connection():
        form, response = Client().get(path), verify(Client(), path)

    assert form.status_code == response.status_code == 403 and counts() == before


# --- throttling: per address and per client address ------------------------------------------------------------------


def test_repeated_sign_ups_for_one_address_are_throttled(firms_world, signup_open, settings, fake_ports, as_production_connection):
    settings.SIGNUP_MAX_PER_EMAIL = 3
    paths = [new_link(firms_world["alpha_accountant"]) for _ in range(5)]

    with as_production_connection():
        # Spellings of one address count as one; each attempt has its own client address, so only the address is limited.
        answers = [
            submit(Client(REMOTE_ADDR=f"10.0.0.{n}"), path, email=email)
            for n, (path, email) in enumerate(zip(paths, ["a@client.example", "A@client.example", " a@client.example ", "a@client.example"]))
        ]
        other = submit(Client(REMOTE_ADDR="10.0.1.1"), paths[4], email="b@client.example")

    assert [response.status_code for response in answers] == [200, 200, 200, 429]
    assert other.status_code == 200
    assert sorted(mail["to"].lower() for mail in fake_ports.email.sent) == ["a@client.example"] * 3 + ["b@client.example"]


def test_repeated_sign_ups_from_one_client_address_are_throttled(firms_world, signup_open, settings, fake_ports, as_production_connection):
    settings.SIGNUP_MAX_PER_CLIENT = 3
    path = new_link(firms_world["alpha_accountant"])

    with as_production_connection():
        answers = [submit(Client(REMOTE_ADDR="10.0.0.9"), "/signup/not-a-real-link/", email=f"u{n}@client.example") for n in range(4)]
        elsewhere = submit(Client(REMOTE_ADDR="10.0.0.10"), path)

    assert [response.status_code for response in answers] == [410, 410, 410, 429]
    assert elsewhere.status_code == 200


def test_a_throttled_sign_up_sends_nothing_and_does_not_spend_the_link(firms_world, signup_open, settings, fake_ports, as_production_connection):
    settings.SIGNUP_MAX_PER_CLIENT = 1
    path = new_link(firms_world["alpha_accountant"])

    with as_production_connection():
        submit(Client(REMOTE_ADDR="10.0.0.9"), "/signup/not-a-real-link/")
        blocked = submit(Client(REMOTE_ADDR="10.0.0.9"), path)

    assert blocked.status_code == 429 and fake_ports.email.sent == []
    assert AuthToken.objects.get(purpose="signup").used_at is None


def test_sign_up_failures_are_logged_without_addresses_or_tokens(firms_world, signup_open, settings, caplog, as_production_connection):
    settings.SIGNUP_MAX_PER_CLIENT = 1
    with caplog.at_level("INFO", logger="core.views"), as_production_connection():
        submit(Client(REMOTE_ADDR="10.0.0.9"), "/signup/secret-token-value/", email="private@client.example")
        submit(Client(REMOTE_ADDR="10.0.0.9"), "/signup/secret-token-value/", email="private@client.example")

    # Only this module's own records: Django's request logger prints the URL path of any 4xx, tokens included.
    logged = " ".join(record.getMessage() for record in caplog.records if record.name == "core.views")
    assert "signup refused" in logged
    assert "private@client.example" not in logged and "secret-token-value" not in logged and "10.0.0.9" not in logged


# --- Chinese ---------------------------------------------------------------------------------------------------------

CHINESE = {"accept-language": "zh-hans"}


def test_the_sign_up_screens_and_the_verification_email_are_in_chinese(
    firms_world, signup_open, settings, fake_ports, as_production_connection
):
    path = new_link(firms_world["alpha_accountant"])
    with as_production_connection():
        form = Client().get(path, headers=CHINESE).content.decode()
        bad = Client().post(path, {"email": "nope", "company_name": "X"}, headers=CHINESE).content.decode()
        Client().post(path, {"email": "new.owner@client.example", "company_name": "Client Sdn Bhd"}, headers=CHINESE)
        sent = Client().post(new_link(firms_world["alpha_accountant"]), {"email": "x@client.example", "company_name": "X"}, headers=CHINESE)
        password_form = Client().get(register_path(fake_ports), headers=CHINESE).content.decode()
        settings.SIGNUP_ENABLED = False
        closed = Client().get(path, headers=CHINESE).content.decode()

    assert "创建您的账户" in form and "公司名称" in form
    assert "请输入有效的电子邮件地址" in bad
    assert "确认" in fake_ports.email.sent[0]["subject"] and "Confirm" not in fake_ports.email.sent[0]["subject"]
    assert "我们已发送" in sent.content.decode()
    assert "设置您的密码" in password_form
    assert "注册已关闭" in closed


def test_an_address_too_long_to_store_asks_again_instead_of_failing(firms_world, signup_open, fake_ports, as_production_connection):
    path = new_link(firms_world["alpha_accountant"])
    long_address = "a" * 60 + "@" + ".".join(["b" * 60] * 4) + ".example"  # valid in form, over 254 characters
    assert len(long_address) > 254

    with as_production_connection():
        response = submit(Client(), path, email=long_address)

    assert response.status_code == 200 and b'role="alert"' in response.content and fake_ports.email.sent == []


# --- the switch cannot be turned on outside development until two-factor authentication is done (D40 (7)) ---------------


def load_settings(**env):
    """Import config.settings in a fresh interpreter with the given environment; returns the finished process."""
    base = {k: v for k, v in os.environ.items() if k not in ("ENVIRONMENT", "SECRET_KEY", "SIGNUP_ENABLED")}
    return subprocess.run(
        [sys.executable, "-c", "import config.settings"],
        cwd=Path(__file__).resolve().parents[2], env={**base, **env}, capture_output=True, text=True,
    )


@pytest.mark.parametrize("environment", ["production", "staging"])
def test_the_app_will_not_start_with_sign_up_on_outside_development(environment):
    result = load_settings(ENVIRONMENT=environment, SECRET_KEY="x", SIGNUP_ENABLED="true")

    assert result.returncode != 0 and "SIGNUP_ENABLED" in result.stderr


@pytest.mark.parametrize(
    "env",
    [
        {"ENVIRONMENT": "production", "SECRET_KEY": "x"},
        {"ENVIRONMENT": "production", "SECRET_KEY": "x", "SIGNUP_ENABLED": "false"},
        {"ENVIRONMENT": "development", "SIGNUP_ENABLED": "true"},
    ],
)
def test_the_app_starts_with_sign_up_off_anywhere_and_on_in_development(env):
    assert load_settings(**env).returncode == 0
