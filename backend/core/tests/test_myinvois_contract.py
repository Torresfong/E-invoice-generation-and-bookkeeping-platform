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
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from django.utils import timezone

from core.jobs import PermanentError, TransientError
from core.ports.fakes import FakeGateway
from core.ports.interfaces import IntermediaryCredentials, TaxpayerSession, TinCheck
from core.ports.myinvois import PermissionDenied, gateway_from_environment, urllib_transport

SANDBOX_ENV = "MYINVOIS_SANDBOX_"


@dataclass(repr=False)  # a failing test prints its fixtures: the TINs here must not reach a report or a log
class Scenario:
    kind: str
    gateway: object
    credentials: IntermediaryCredentials
    company_b_tin: str | None
    company_b_rob: str | None
    unauthorised_tin: str | None
    buyer: tuple | None  # (tin, id_type, id_value)
    unknown_buyer_tin: str | None

    def __repr__(self):
        return f"<Scenario {self.kind}>"


def _fake_scenario():
    gateway = FakeGateway()
    credentials = IntermediaryCredentials(client_id="fake-erp-id", client_secret="fake-erp-secret")
    gateway.accepted_client_ids = {"fake-erp-id"}
    gateway.authorised_tins = {"C2222222222"}
    gateway.unknown_tins = {"C9999999999"}
    return Scenario("fake", gateway, credentials, "C2222222222", None, "C3333333333", ("C1234567890", "BRN", "201901234567"), "C9999999999")


@pytest.fixture(scope="module")
def sandbox_gateway():
    """One gateway for the whole module, so its token cache is shared: the sandbox allows 12 logins a minute per client
    ID, and a fresh login in every test runs the quota out within a few runs (seen in #42)."""
    gateway = gateway_from_environment("sandbox", os.environ)
    if gateway is None:
        pytest.skip("no MyInvois sandbox credentials in the environment")
    return gateway


def _sandbox_scenario(gateway, env=os.environ):

    def value(name):
        return env.get(SANDBOX_ENV + name) or None

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
    if request.param == "fake":
        return _fake_scenario()
    return _sandbox_scenario(request.getfixturevalue("sandbox_gateway"))


def need(value, name):  # skips the test when its sandbox input is not configured
    if value is None:
        pytest.skip(f"{SANDBOX_ENV}{name} is not set")
    return value


# --- login (D60 (4)): our own company first, then a company that has authorised us -------------------------------------


def test_our_own_company_logs_in_as_a_taxpayer_with_no_onbehalfof(scenario):
    session = scenario.gateway.login_as_taxpayer(scenario.credentials, 1, "")

    # Booleans, not the values: a failing assert prints what it compares, and a token or TIN must not reach a report.
    has_token, acts_for_nobody = bool(session.token), session.onbehalf_of == ""
    assert has_token and acts_for_nobody


def test_a_session_lasts_sixty_minutes(scenario):
    # Expiry itself is not observable without waiting an hour; its length is, and the cache renews before it (D60 (5)).
    before = timezone.now()
    session = scenario.gateway.login_as_taxpayer(scenario.credentials, 1, "")
    after = timezone.now()

    assert before + timedelta(minutes=59) <= session.expires_at <= after + timedelta(minutes=60)


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

    expected = f"{tin}:{scenario.company_b_rob}" if scenario.company_b_rob else tin
    has_token, is_for_our_company_id, acts_for_company_b = bool(session.token), session.company_id == 2, session.onbehalf_of == expected
    assert has_token and is_for_our_company_id and acts_for_company_b


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

    outcome = scenario.gateway.validate_tin(tin, id_type, id_value)  # not inside the assert: it would print the arguments

    assert outcome is TinCheck.VALID


def test_a_malformed_tin_is_not_found(scenario):
    _, id_type, id_value = need(scenario.buyer, "BUYER_TIN, _BUYER_ID_TYPE and _BUYER_ID_VALUE")

    outcome = scenario.gateway.validate_tin("not a tin", id_type, id_value)

    assert outcome is TinCheck.NOT_FOUND


def test_an_empty_id_value_is_bad_format(scenario):
    tin, id_type, _ = need(scenario.buyer, "BUYER_TIN, _BUYER_ID_TYPE and _BUYER_ID_VALUE")

    outcome = scenario.gateway.validate_tin(tin, id_type, "")

    assert outcome is TinCheck.BAD_FORMAT


def test_a_well_formed_tin_that_lhdn_does_not_know_is_not_found(scenario):
    unknown = need(scenario.unknown_buyer_tin, "UNKNOWN_BUYER_TIN")
    _, id_type, id_value = need(scenario.buyer, "BUYER_TIN, _BUYER_ID_TYPE and _BUYER_ID_VALUE")

    outcome = scenario.gateway.validate_tin(unknown, id_type, id_value)

    assert outcome is TinCheck.NOT_FOUND


# --- a failing contract test must not print the configured identities (D60 (4)) -----------------------------------------
# pytest rewrites an assert to print the values it compares and the arguments of the calls inside it, so a failure
# against the sandbox would put the configured TINs, id values and tokens in the test report. These tests make each
# contract assertion fail on purpose, against stand-in gateways, and look for those values in the failure text.

BUYER = ("C7770000001", "BRN", "ID-VALUE-0099")
UNKNOWN = "C8880000002"
COMPANY_B = "C6660000003"
OTHER_TIN = "C5550000004"
TOKEN = "TOKEN-VALUE-0042"
SECRETS = (*BUYER[::2], UNKNOWN, COMPANY_B, OTHER_TIN, TOKEN)


class _AlwaysSays:
    """A gateway that answers every TIN check with one outcome, so a test expecting another fails."""

    def __init__(self, outcome):
        self.outcome = outcome

    def validate_tin(self, tin, id_type, id_value):
        return self.outcome


class _LogsInAs:
    """A gateway whose login returns a fixed session, so a test expecting another identity fails."""

    def __init__(self, company_id, tin):
        self.session = TaxpayerSession(company_id=company_id, tin=tin, token=TOKEN, expires_at=timezone.now())

    def login_as_taxpayer(self, credentials, company_id, tin, rob=None):
        return self.session


def _scenario_with(gateway):
    return replace(_fake_scenario(), gateway=gateway, buyer=BUYER, unknown_buyer_tin=UNKNOWN, company_b_tin=COMPANY_B)


@pytest.mark.parametrize(
    "contract_test, wrong_answer",
    [
        (test_a_registered_buyer_has_a_valid_tin, TinCheck.NOT_FOUND),
        (test_a_malformed_tin_is_not_found, TinCheck.VALID),
        (test_an_empty_id_value_is_bad_format, TinCheck.VALID),
        (test_a_well_formed_tin_that_lhdn_does_not_know_is_not_found, TinCheck.VALID),
    ],
)
def test_a_failing_tin_assertion_does_not_print_the_buyers_identity(contract_test, wrong_answer):
    with pytest.raises(AssertionError) as failure:
        contract_test(_scenario_with(_AlwaysSays(wrong_answer)))

    assert [secret for secret in SECRETS if secret in str(failure.value)] == []


@pytest.mark.parametrize(
    "contract_test, gateway",
    [
        (test_the_intermediary_logs_in_for_a_company_that_has_authorised_us, _LogsInAs(2, OTHER_TIN)),  # wrong TIN
        (test_the_intermediary_logs_in_for_a_company_that_has_authorised_us, _LogsInAs(9, COMPANY_B)),  # wrong company
        (test_our_own_company_logs_in_as_a_taxpayer_with_no_onbehalfof, _LogsInAs(1, OTHER_TIN)),  # TIN where none belongs
    ],
)
def test_a_failing_login_assertion_does_not_print_a_tin_or_a_token(contract_test, gateway):
    with pytest.raises(AssertionError) as failure:
        contract_test(_scenario_with(gateway))

    assert [secret for secret in SECRETS if secret in str(failure.value)] == []
