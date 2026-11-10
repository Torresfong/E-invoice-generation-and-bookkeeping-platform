"""Seam 2 contract tests for the MyInvois gateway (D33, D58 (7), D60 (4), #42).

The same assertions run against the fake, always, and against LHDN's preprod sandbox when its credentials are in the
environment; without them the sandbox runs skip. Where the two disagree, the fake is wrong and is corrected.

What the sandbox runs need, all held outside the repository (names only in `.env.example`):
  MYINVOIS_SANDBOX_BASE_URL, MYINVOIS_SANDBOX_CLIENT_ID, MYINVOIS_SANDBOX_CLIENT_SECRET   our intermediary ERP identity
  MYINVOIS_SANDBOX_COMPANY_B_TIN (and optionally _ROB)    a company we control that has authorised us as intermediary
  MYINVOIS_SANDBOX_UNAUTHORISED_TIN                       a test taxpayer of ours that has NOT authorised us
  MYINVOIS_SANDBOX_BUYER_TIN, _BUYER_ID_TYPE, _BUYER_ID_VALUE      a buyer identity that exists in preprod data
  MYINVOIS_SANDBOX_UNKNOWN_BUYER_TIN                      a well-formed TIN that preprod does not know
Only test taxpayers we control are used, never a client's real data. A missing value skips only the tests that need it.

A successful login proves the intermediary is authorised for that taxpayer and nothing more: no submit, cancel or
reject permission is concluded from these tests (D57 (3)); those are proved only when the operation is exercised.
"""

import os
from dataclasses import dataclass

import pytest

from core.jobs import PermanentError, TransientError
from core.ports.fakes import FakeGateway
from core.ports.interfaces import IntermediaryCredentials, TinCheck
from core.ports.myinvois import PermissionDenied, gateway_from_environment, urllib_transport

SANDBOX_ENV = "MYINVOIS_SANDBOX_"


@dataclass
class Scenario:
    kind: str
    gateway: object
    credentials: IntermediaryCredentials
    company_b_tin: str | None
    company_b_rob: str | None
    unauthorised_tin: str | None
    buyer: tuple | None  # (tin, id_type, id_value)
    unknown_buyer_tin: str | None


def _fake_scenario():
    gateway = FakeGateway()
    credentials = IntermediaryCredentials(client_id="fake-erp-id", client_secret="fake-erp-secret")
    gateway.accepted_client_ids = {"fake-erp-id"}
    gateway.authorised_tins = {"C2222222222"}
    gateway.unknown_tins = {"C9999999999"}
    return Scenario("fake", gateway, credentials, "C2222222222", None, "C3333333333", ("C1234567890", "BRN", "201901234567"), "C9999999999")


def _sandbox_scenario(env=os.environ):
    gateway = gateway_from_environment("sandbox", env)
    if gateway is None:
        pytest.skip("no MyInvois sandbox credentials in the environment")
    value = lambda name: env.get(SANDBOX_ENV + name) or None  # noqa: E731
    buyer = (value("BUYER_TIN"), value("BUYER_ID_TYPE"), value("BUYER_ID_VALUE"))
    return Scenario(
        "sandbox",
        gateway,
        gateway.credentials,
        value("COMPANY_B_TIN"),
        value("COMPANY_B_ROB"),
        value("UNAUTHORISED_TIN"),
        buyer if all(buyer) else None,
        value("UNKNOWN_BUYER_TIN"),
    )


@pytest.fixture(params=["fake", "sandbox"])
def scenario(request):
    return _fake_scenario() if request.param == "fake" else _sandbox_scenario()


def need(value, name):
    if value is None:
        pytest.skip(f"{SANDBOX_ENV}{name} is not set")
    return value


# --- login (D60 (4)): our own company first, then a company that has authorised us -------------------------------------


def test_our_own_company_logs_in_as_a_taxpayer_with_no_onbehalfof(scenario):
    session = scenario.gateway.login_as_taxpayer(scenario.credentials, 1, "")

    assert session.token and session.onbehalf_of == ""


def test_a_protected_call_succeeds_with_our_own_login(scenario):
    # Proves only that our own credentials work (D60 (4) step one). Get Document Types is not on the port.
    if scenario.kind == "fake":
        pytest.skip("the fake has no protected calls beyond the port")
    token = scenario.gateway.login_as_taxpayer(scenario.credentials, 1, "").token

    answer = urllib_transport("GET", f"{scenario.gateway.base_url}/api/v1.0/documenttypes", {"Authorization": f"Bearer {token}"}, None)

    assert answer.status == 200


def test_the_intermediary_logs_in_for_a_company_that_has_authorised_us(scenario):
    tin = need(scenario.company_b_tin, "COMPANY_B_TIN")

    session = scenario.gateway.login_as_taxpayer(scenario.credentials, 2, tin, rob=scenario.company_b_rob)

    assert session.token and session.company_id == 2
    expected = f"{tin}:{scenario.company_b_rob}" if scenario.company_b_rob else tin
    assert session.onbehalf_of == expected


def test_wrong_client_credentials_are_refused_and_never_retried(scenario):
    wrong = IntermediaryCredentials(client_id="not-a-client-id", client_secret="not-a-secret")

    with pytest.raises(PermanentError) as raised:
        scenario.gateway.login_as_taxpayer(wrong, 1, "")

    assert not isinstance(raised.value, TransientError)


def test_a_taxpayer_that_has_not_authorised_us_is_a_permission_denial_not_a_transient_failure(scenario):
    tin = need(scenario.unauthorised_tin, "UNAUTHORISED_TIN")

    with pytest.raises(PermissionDenied) as raised:
        scenario.gateway.login_as_taxpayer(scenario.credentials, 3, tin)

    assert not isinstance(raised.value, TransientError)


# --- TIN validation: valid, bad format, not found (D58 (2)) ------------------------------------------------------------


def test_a_registered_buyer_has_a_valid_tin(scenario):
    tin, id_type, id_value = need(scenario.buyer, "BUYER_TIN, _BUYER_ID_TYPE and _BUYER_ID_VALUE")

    assert scenario.gateway.validate_tin(tin, id_type, id_value) is TinCheck.VALID


def test_a_malformed_tin_is_bad_format(scenario):
    _, id_type, id_value = need(scenario.buyer, "BUYER_TIN, _BUYER_ID_TYPE and _BUYER_ID_VALUE")

    assert scenario.gateway.validate_tin("not a tin", id_type, id_value) is TinCheck.BAD_FORMAT


def test_a_well_formed_tin_that_lhdn_does_not_know_is_not_found(scenario):
    unknown = need(scenario.unknown_buyer_tin, "UNKNOWN_BUYER_TIN")
    _, id_type, id_value = need(scenario.buyer, "BUYER_TIN, _BUYER_ID_TYPE and _BUYER_ID_VALUE")

    assert scenario.gateway.validate_tin(unknown, id_type, id_value) is TinCheck.NOT_FOUND
