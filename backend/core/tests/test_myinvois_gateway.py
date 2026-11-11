import inspect
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import ports
from core.ports.fakes import FakeGateway
from core.ports.interfaces import (
    CANCELLED,
    INVALID,
    SUBMITTED,
    VALIDATED,
    DuplicateSubmission,
    IntermediaryCredentials,
    MyInvoisGateway,
    SessionExpired,
    TinCheck,
    WrongCompany,
)
from core.ports.signing import CertificateNotConfigured

DOCUMENT = b'{"invoice": "INV-0001"}'
DOCUMENT_SHA256 = bytes.fromhex("ea09b11d6dff65b01ee96aa654544fc189f461c97aef42e95308adacab210ad2")
COMPANY_ID = 7
OTHER_COMPANY_ID = 8


CODE = "INV-0001"
CREDENTIALS = IntermediaryCredentials(client_id="erp-client-id", client_secret="erp-client-secret")


def a_session(company_id=COMPANY_ID, tin="C1234567890", rob=None):
    return ports.get().gateway.login_as_taxpayer(CREDENTIALS, company_id, tin, rob=rob)


def a_signed_document(session=None, document=DOCUMENT, code=CODE):
    return ports.get().gateway.sign(session or a_session(), document, code)


def submit_a_document():
    """Returns the gateway, the session, the submission and the accepted document's uuid."""
    gateway, session = ports.get().gateway, a_session()
    submission = gateway.submit(session, [a_signed_document(session)])
    return gateway, session, submission, submission.accepted[0].uuid


# --- all calls go through the one module, and callers run against the fake with no network access --------------------


def differences_from_the_interface(implementation):
    """The gateway methods `implementation` lacks or whose arguments differ."""
    methods = [name for name in vars(MyInvoisGateway) if not name.startswith("_") and callable(vars(MyInvoisGateway)[name])]
    return [
        name
        for name in methods
        if not hasattr(implementation, name)
        or list(inspect.signature(getattr(MyInvoisGateway, name)).parameters)
        != list(inspect.signature(getattr(implementation, name)).parameters)
    ]


def test_the_fake_has_every_method_of_the_interface_with_the_same_arguments():
    assert differences_from_the_interface(FakeGateway) == []


def test_a_gateway_that_drifts_from_the_interface_is_noticed():
    class Drifted:
        def validate_tin(self, number):
            return True

        def submit(self, signed, session):
            return "uid"

    assert {"validate_tin", "submit", "sign"} <= set(differences_from_the_interface(Drifted))


def test_the_port_has_no_reject(fake_ports):
    assert not hasattr(MyInvoisGateway, "reject") and not hasattr(fake_ports.gateway, "reject")


# --- login: intermediary credentials, a 60-minute session, and onbehalfof from the company row (D58 (1)) ---------------


def test_a_session_for_a_company_acts_on_behalf_of_its_tin(fake_ports):
    assert a_session().onbehalf_of == "C1234567890"


def test_a_session_for_a_branch_acts_on_behalf_of_tin_and_rob(fake_ports):
    assert a_session(rob="IG9876543210").onbehalf_of == "C1234567890:IG9876543210"


def test_a_session_exposes_an_expiry_sixty_minutes_after_login(fake_ports):
    now = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    fake_ports.gateway.now = lambda: now

    assert a_session().expires_at == now + timedelta(minutes=60)


def test_a_call_with_an_expired_session_is_refused(fake_ports):
    gateway = fake_ports.gateway
    now = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    gateway.now = lambda: now
    session = a_session()
    signed = a_signed_document(session)
    gateway.now = lambda: now + timedelta(minutes=61)

    with pytest.raises(SessionExpired):
        gateway.submit(session, [signed])

    assert gateway.submissions == []


def test_printing_a_session_or_a_signed_document_shows_no_credential_token_tin_or_content():
    session = a_session()

    printed = repr(CREDENTIALS) + repr(session) + repr(a_signed_document(session)) + str(session)

    for secret in ("erp-client-secret", "C1234567890", "fake-token", "INV-0001"):
        assert secret not in printed


def test_logging_in_leaves_no_credential_or_tin_in_the_log(caplog):
    caplog.set_level(logging.DEBUG)

    session = a_session()
    ports.get().gateway.submit(session, [a_signed_document(session)])

    for secret in ("erp-client-secret", "erp-client-id", "C1234567890", "fake-token"):
        assert secret not in caplog.text


# --- TIN validation: valid, bad format or not found (D58 (2)) ---------------------------------------------------------


def test_a_registered_buyer_has_a_valid_tin(fake_ports):
    assert fake_ports.gateway.validate_tin("C1234567890", "BRN", "201901234567") is TinCheck.VALID


def test_a_malformed_tin_is_not_found_because_the_sandbox_answers_404_for_it(fake_ports):
    # Seen in the preprod sandbox (#42): spaces, too short, digits only, lowercase and too long are all 404.
    assert fake_ports.gateway.validate_tin("not a tin", "BRN", "201901234567") is TinCheck.NOT_FOUND


def test_an_empty_id_value_is_bad_format_because_it_is_the_one_input_the_sandbox_answers_400_for(fake_ports):
    assert fake_ports.gateway.validate_tin("C1234567890", "BRN", "") is TinCheck.BAD_FORMAT


def test_a_well_formed_tin_that_lhdn_does_not_know_is_not_found(fake_ports):
    fake_ports.gateway.unknown_tins.add("C9999999999")

    assert fake_ports.gateway.validate_tin("C9999999999", "NRIC", "900101015555") is TinCheck.NOT_FOUND


def test_a_tin_is_checked_with_the_buyers_id_type_and_value(fake_ports):
    fake_ports.gateway.validate_tin("C1234567890", "PASSPORT", "A1234567")

    assert fake_ports.gateway.tins_validated == [("C1234567890", "PASSPORT", "A1234567")]


def test_an_unknown_id_type_is_refused(fake_ports):
    with pytest.raises(ValueError):
        fake_ports.gateway.validate_tin("C1234567890", "DRIVING", "x")


# --- submit: one submission, a result per document (D58 (3)) ---------------------------------------------------------


def test_a_document_goes_from_login_to_submitted_through_the_port_with_no_network(no_network, fake_ports):
    gateway, session, submission, uuid = submit_a_document()

    (status,) = gateway.poll_status(session, submission.submission_uid)
    assert status.uuid == uuid and status.state == SUBMITTED
    assert [d["onbehalf_of"] for d in fake_ports.gateway.submissions] == ["C1234567890"]


def test_one_submission_can_succeed_in_part(fake_ports):
    gateway, session = fake_ports.gateway, a_session()
    fake_ports.gateway.reject_code_numbers["INV-0002"] = "DV001"
    good, bad = a_signed_document(session, code="INV-0001"), a_signed_document(session, b"other", "INV-0002")

    submission = gateway.submit(session, [good, bad])

    assert [d.code_number for d in submission.accepted] == ["INV-0001"]
    assert [(d.code_number, d.error_code) for d in submission.rejected] == [("INV-0002", "DV001")]
    assert [s.uuid for s in gateway.poll_status(session, submission.submission_uid)] == [submission.accepted[0].uuid]


def test_a_document_signed_for_one_company_cannot_be_submitted_through_anothers_session(fake_ports):
    gateway = ports.get().gateway
    signed = a_signed_document()
    other = a_session(OTHER_COMPANY_ID, "C9999999999")

    with pytest.raises(WrongCompany):
        gateway.submit(other, [signed])

    assert fake_ports.gateway.submissions == [] and fake_ports.gateway.documents == {}


# --- status is per document, polled by submission and cancelled by document -------------------------------------------


def test_a_test_can_decide_how_lhdn_answers_a_poll(fake_ports):
    gateway, session, submission, uuid = submit_a_document()

    fake_ports.gateway.validate(uuid)

    (status,) = gateway.poll_status(session, submission.submission_uid)
    assert status.state == VALIDATED
    assert status.long_id == f"fake-long-id-{uuid}" and status.date_time_validated is not None


def test_only_a_valid_document_has_a_long_id(fake_ports):
    gateway, session, submission, uuid = submit_a_document()
    fake_ports.gateway.invalidate(uuid, "the buyer's TIN does not match")

    (status,) = gateway.poll_status(session, submission.submission_uid)

    assert status.state == INVALID and status.long_id is None
    assert status.reason == "the buyer's TIN does not match"


def test_a_rejection_request_is_a_marker_read_through_polling_and_leaves_the_document_valid(fake_ports):
    gateway, session, submission, uuid = submit_a_document()
    fake_ports.gateway.validate(uuid)

    fake_ports.gateway.request_rejection(uuid)

    (status,) = gateway.poll_status(session, submission.submission_uid)
    assert status.state == VALIDATED and status.rejection_requested is True


def test_cancelling_a_document_by_its_uuid_marks_it_cancelled(fake_ports):
    gateway, session, submission, uuid = submit_a_document()

    gateway.cancel(session, uuid, "wrong amount")

    (status,) = gateway.poll_status(session, submission.submission_uid)
    assert status.state == CANCELLED
    assert fake_ports.gateway.cancellations == [{"uuid": uuid, "reason": "wrong amount"}]


def test_a_submission_uid_cannot_be_used_to_cancel_nor_a_document_uuid_to_poll(fake_ports):
    gateway, session, submission, uuid = submit_a_document()

    with pytest.raises(TypeError):
        gateway.cancel(session, submission.submission_uid, "x")
    with pytest.raises(TypeError):
        gateway.poll_status(session, uuid)
    with pytest.raises(TypeError):
        gateway.cancel(session, str(uuid), "x")  # a bare string is neither

    assert fake_ports.gateway.cancellations == []


def test_one_taxpayers_session_cannot_reach_anothers_document(fake_ports):
    gateway, _, submission, uuid = submit_a_document()
    other = a_session(OTHER_COMPANY_ID, "C9999999999")

    for call in (
        lambda: gateway.poll_status(other, submission.submission_uid),
        lambda: gateway.cancel(other, uuid, "x"),
    ):
        with pytest.raises(KeyError):
            call()
    assert gateway.find_document(other, CODE) is None

    assert fake_ports.gateway.documents[uuid].state == SUBMITTED and fake_ports.gateway.cancellations == []


def test_two_branches_sharing_a_tin_cannot_reach_each_others_documents(fake_ports):
    gateway = fake_ports.gateway
    first, second = a_session(rob="IG1111111111"), a_session(OTHER_COMPANY_ID, rob="IG2222222222")
    own = gateway.submit(first, [a_signed_document(first)])
    uuid = own.accepted[0].uuid

    for call in (
        lambda: gateway.poll_status(second, own.submission_uid),
        lambda: gateway.cancel(second, uuid, "x"),
    ):
        with pytest.raises(KeyError):
            call()
    assert gateway.find_document(second, CODE) is None

    # the same internal number is not a duplicate for the other branch, and finds its own uuid
    again = gateway.submit(second, [a_signed_document(second)])
    assert len(again.accepted) == 1 and again.rejected == []
    assert gateway.find_document(second, CODE).uuid == again.accepted[0].uuid != uuid
    assert gateway.find_document(first, CODE).uuid == uuid


# --- LHDN has no idempotency key: it answers a repeat with an error (D58 (5)) ----------------------------------------


def test_the_same_payload_sent_again_within_ten_minutes_is_a_422_that_does_not_return_the_uuid(fake_ports):
    gateway, session, _, _ = submit_a_document()

    with pytest.raises(DuplicateSubmission) as repeated:
        gateway.submit(session, [a_signed_document(session)])

    assert repeated.value.status_code == 422 and "uuid" not in str(repeated.value).lower()
    assert len(fake_ports.gateway.submissions) == 1


def test_the_same_document_sent_again_within_two_hours_is_rejected_dv302_without_its_uuid(fake_ports):
    gateway, session, _, uuid = submit_a_document()
    fake_ports.gateway.now = lambda: datetime.now(timezone.utc) + timedelta(minutes=30)

    again = gateway.submit(session, [a_signed_document(session, b'{"invoice": "changed"}')])

    assert again.accepted == []
    assert [(d.code_number, d.error_code) for d in again.rejected] == [(CODE, "DV302")]
    assert uuid not in repr(again)


def test_after_two_hours_the_same_internal_number_is_accepted_again(fake_ports):
    gateway, session, _, _ = submit_a_document()
    fake_ports.gateway.now = lambda: datetime.now(timezone.utc) + timedelta(hours=3)
    session = a_session()  # the first login has expired by now

    again = gateway.submit(session, [a_signed_document(session, b'{"invoice": "changed"}')])

    assert len(again.accepted) == 1


def test_a_document_is_found_by_its_internal_number_with_its_uuid(fake_ports):
    gateway, session, _, uuid = submit_a_document()

    assert gateway.find_document(session, CODE).uuid == uuid
    assert gateway.find_document(session, "INV-9999") is None


# --- the signing key is reached only through the key provider, and every signing is logged (D13) ----------------------


def test_signing_asks_the_key_provider_to_sign_with_this_environments_certificate(fake_ports):
    signed = ports.get().gateway.sign(a_session(), DOCUMENT, CODE)

    assert fake_ports.gateway.key_provider.signings == [("vault/sandbox-certificate", DOCUMENT_SHA256)]
    assert signed.document == DOCUMENT
    assert signed.signature == fake_ports.gateway.key_provider.signature


def test_production_signs_with_the_production_certificate(fake_ports, settings):
    settings.MYINVOIS_ENVIRONMENT = "production"

    ports.get().gateway.sign(a_session(), DOCUMENT, CODE)

    assert fake_ports.gateway.key_provider.signings == [("vault/production-certificate", DOCUMENT_SHA256)]


def test_every_signing_is_logged_with_what_was_signed_and_never_the_document(caplog):
    gateway = ports.get().gateway

    gateway.sign(a_session(), DOCUMENT, CODE)
    gateway.sign(a_session(), DOCUMENT, CODE)

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
        ports.get().gateway.sign(a_session(), DOCUMENT, CODE)

    assert DOCUMENT_SHA256.hex() in caplog.text


@pytest.mark.parametrize(
    "environment, certificates",
    [
        ("sandbox", {"sandbox": "", "production": "vault/production-certificate"}),
        ("production", {"sandbox": "vault/sandbox-certificate", "production": ""}),
    ],
)
def test_nothing_is_signed_when_the_environment_has_no_certificate(environment, certificates, fake_ports, settings, caplog):
    settings.MYINVOIS_ENVIRONMENT = environment
    settings.MYINVOIS_CERTIFICATES = certificates

    with pytest.raises(CertificateNotConfigured):
        ports.get().gateway.sign(a_session(), DOCUMENT, CODE)

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


@pytest.mark.parametrize("environment", ["prod", "Production", "live", ""])
def test_the_app_will_not_start_in_an_environment_it_does_not_know(environment):
    result = start_django(CHOSEN, ENVIRONMENT=environment, **BOTH)

    assert result.returncode != 0 and "ENVIRONMENT" in result.stderr and result.stdout == ""


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
