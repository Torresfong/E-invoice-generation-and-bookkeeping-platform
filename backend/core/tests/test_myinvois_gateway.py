import inspect
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from core import ports
from core.ports.fakes import FakeGateway
from core.ports.interfaces import CANCELLED, SUBMITTED, VALIDATED, MyInvoisGateway
from core.ports.signing import CertificateNotConfigured

DOCUMENT = b'{"invoice": "INV-0001"}'
DOCUMENT_SHA256 = bytes.fromhex("ea09b11d6dff65b01ee96aa654544fc189f461c97aef42e95308adacab210ad2")
COMPANY_ID = 7


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args):
        raise AssertionError("a test tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def submit_a_document():
    gateway = ports.get().gateway
    session = gateway.login_as_taxpayer("C1234567890")
    return gateway, session, gateway.submit(session, gateway.sign(DOCUMENT, COMPANY_ID))


# --- all calls go through the one module, and callers run against the fake with no network access --------------------


def differences_from_the_interface(implementation):
    """The gateway methods `implementation` lacks or whose arguments differ."""
    methods = [name for name in vars(MyInvoisGateway) if not name.startswith("_") and callable(vars(MyInvoisGateway)[name])]
    return [
        name
        for name in methods
        if not hasattr(implementation, name)
        or inspect.signature(getattr(MyInvoisGateway, name)).parameters.keys()
        != inspect.signature(getattr(implementation, name)).parameters.keys()
    ]


def test_the_fake_has_every_method_of_the_interface_with_the_same_arguments():
    assert differences_from_the_interface(FakeGateway) == []


def test_a_gateway_that_drifts_from_the_interface_is_noticed():
    class Drifted:
        def validate_tin(self, number):
            return True

    assert {"validate_tin", "sign"} <= set(differences_from_the_interface(Drifted))


def test_one_taxpayers_session_cannot_reach_anothers_document(fake_ports):
    gateway, _, uid = submit_a_document()
    other = gateway.login_as_taxpayer("C9999999999")

    for call in (lambda: gateway.poll_status(other, uid), lambda: gateway.cancel(other, uid, "x"), lambda: gateway.reject(other, uid, "x")):
        with pytest.raises(KeyError):
            call()

    assert fake_ports.gateway.statuses[uid].state == SUBMITTED and fake_ports.gateway.cancellations == []


def test_printing_a_session_or_a_signed_document_shows_no_token_tin_or_content():
    gateway = ports.get().gateway

    printed = repr(gateway.login_as_taxpayer("C1234567890")) + repr(gateway.sign(DOCUMENT, COMPANY_ID))

    assert "C1234567890" not in printed and "INV-0001" not in printed


def test_a_document_goes_from_login_to_submitted_through_the_port_with_no_network(no_network, fake_ports):
    gateway, session, uid = submit_a_document()

    assert session.tin == "C1234567890"
    assert gateway.poll_status(session, uid).state == SUBMITTED
    assert [d["tin"] for d in fake_ports.gateway.submissions] == ["C1234567890"]


def test_a_test_can_decide_how_lhdn_answers_a_poll(fake_ports):
    gateway, session, uid = submit_a_document()

    fake_ports.gateway.statuses[uid].state = VALIDATED

    assert gateway.poll_status(session, uid).state == VALIDATED


def test_cancelling_a_document_marks_it_cancelled(fake_ports):
    gateway, session, uid = submit_a_document()

    gateway.cancel(session, uid, "wrong amount")

    assert gateway.poll_status(session, uid).state == CANCELLED
    assert fake_ports.gateway.cancellations == [{"uid": uid, "reason": "wrong amount"}]


def test_a_rejection_is_a_marker_and_leaves_the_document_valid(fake_ports):
    gateway, session, uid = submit_a_document()
    fake_ports.gateway.statuses[uid].state = VALIDATED

    gateway.reject(session, uid, "wrong buyer")

    status = gateway.poll_status(session, uid)
    assert status.state == VALIDATED and status.rejection_requested is True


# --- the signing key is reached only through the key provider, and every signing is logged (D13) ----------------------


def test_signing_asks_the_key_provider_to_sign_with_this_environments_certificate(fake_ports):
    signed = ports.get().gateway.sign(DOCUMENT, COMPANY_ID)

    assert fake_ports.gateway.key_provider.signings == [("vault/sandbox-certificate", DOCUMENT_SHA256)]
    assert signed.document == DOCUMENT
    assert signed.signature == fake_ports.gateway.key_provider.signature


def test_production_signs_with_the_production_certificate(fake_ports, settings):
    settings.MYINVOIS_ENVIRONMENT = "production"

    ports.get().gateway.sign(DOCUMENT, COMPANY_ID)

    assert fake_ports.gateway.key_provider.signings == [("vault/production-certificate", DOCUMENT_SHA256)]


def test_every_signing_is_logged_with_what_was_signed_and_never_the_document(caplog):
    gateway = ports.get().gateway

    gateway.sign(DOCUMENT, COMPANY_ID)
    gateway.sign(DOCUMENT, COMPANY_ID)

    lines = [r.getMessage() for r in caplog.records if r.name == "core.ports.signing"]
    assert len(lines) == 2
    for fragment in (f"company={COMPANY_ID}", "environment=sandbox", "vault/sandbox-certificate", DOCUMENT_SHA256.hex()):
        assert fragment in lines[0]
    assert "INV-0001" not in caplog.text


def test_a_signing_that_fails_in_the_vault_is_still_logged(fake_ports, caplog):
    def vault_down(reference, digest):
        raise RuntimeError("the vault is down")

    fake_ports.gateway.key_provider.sign = vault_down

    with pytest.raises(RuntimeError):
        ports.get().gateway.sign(DOCUMENT, COMPANY_ID)

    assert DOCUMENT_SHA256.hex() in caplog.text


def test_nothing_is_signed_when_the_environment_has_no_certificate(fake_ports, settings, caplog):
    settings.MYINVOIS_CERTIFICATES = {"sandbox": "", "production": "vault/production-certificate"}

    with pytest.raises(CertificateNotConfigured):
        ports.get().gateway.sign(DOCUMENT, COMPANY_ID)

    assert fake_ports.gateway.key_provider.signings == []
    assert "signing" not in caplog.text


# --- sandbox and production certificates are configured separately, as references and never as keys -------------------


def start_django(code, **env):
    """Run `code` after django.setup() in a fresh interpreter with the given environment; returns the finished process."""
    cleared = ("ENVIRONMENT", "SECRET_KEY", "MYINVOIS_SANDBOX_CERTIFICATE_REF", "MYINVOIS_PRODUCTION_CERTIFICATE_REF")
    base = {k: v for k, v in os.environ.items() if k not in cleared}
    return subprocess.run(
        [sys.executable, "-c", f"import django; django.setup(); {code}"],
        cwd=Path(__file__).resolve().parents[2],
        env={**base, "DJANGO_SETTINGS_MODULE": "config.settings", "SECRET_KEY": "x", **env},
        capture_output=True,
        text=True,
    )


CHOSEN = "from django.conf import settings as s; print(s.MYINVOIS_ENVIRONMENT, s.MYINVOIS_CERTIFICATES[s.MYINVOIS_ENVIRONMENT])"
BOTH = {"MYINVOIS_SANDBOX_CERTIFICATE_REF": "vault/sandbox", "MYINVOIS_PRODUCTION_CERTIFICATE_REF": "vault/production"}


@pytest.mark.parametrize(
    "environment, expected",
    [
        ("development", "sandbox vault/sandbox"),
        ("staging", "sandbox vault/sandbox"),
        ("production", "production vault/production"),
    ],
)
def test_only_the_production_environment_can_reach_the_production_certificate(environment, expected):
    result = start_django(CHOSEN, ENVIRONMENT=environment, **BOTH)

    assert result.stdout.strip() == expected


def test_the_app_will_not_start_with_one_certificate_for_sandbox_and_production():
    same = {"MYINVOIS_SANDBOX_CERTIFICATE_REF": "vault/one", "MYINVOIS_PRODUCTION_CERTIFICATE_REF": "vault/one"}

    result = start_django("", ENVIRONMENT="production", **same)

    assert result.returncode != 0 and "certificate" in result.stderr


def test_the_app_starts_with_no_certificates_configured():
    assert start_django("", ENVIRONMENT="production").returncode == 0


def test_the_signing_log_reaches_the_output_in_production():
    code = "import logging; logging.getLogger('core.ports.signing').info('a signing')"

    result = start_django(code, ENVIRONMENT="production")

    assert "a signing" in result.stderr
