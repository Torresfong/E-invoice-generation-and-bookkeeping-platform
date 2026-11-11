"""The real MyInvois gateway (#42) at its only outside seam: the HTTP transport. A scripted transport stands in for LHDN,
so these tests say what we send and how each answer is read, with no network and no credentials."""

import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import pytest

from core.jobs import PermanentError, TransientError
from core.ports.interfaces import IntermediaryCredentials, TinCheck
from core.ports.myinvois import (
    AuthenticationFailed,
    HttpResponse,
    MyInvoisHttpGateway,
    PermissionDenied,
    RateLimited,
    TokenCache,
    UnexpectedResponse,
)

BASE = "https://preprod-api.example.test"
CREDENTIALS = IntermediaryCredentials(client_id="erp-client-id", client_secret="erp-client-secret")
T0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.usefixtures("no_network")


def token_answer(token="tok-1", expires_in=3600):
    body = json.dumps({"access_token": token, "token_type": "Bearer", "expires_in": expires_in, "scope": "InvoicingAPI"})
    return HttpResponse(200, {}, body.encode())


class ScriptedLhdn:
    """Answers each request with the next scripted response (or raises it) and records what was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append({"method": method, "url": url, "headers": headers, "body": body})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def a_gateway(*answers, environment="sandbox"):
    lhdn, clock = ScriptedLhdn(*answers), Clock()
    return MyInvoisHttpGateway(BASE, environment, CREDENTIALS, transport=lhdn, now=clock), lhdn, clock


def test_login_posts_our_erp_credentials_and_the_taxpayers_tin_as_onbehalfof():
    gateway, lhdn, _ = a_gateway(token_answer())

    session = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    (request,) = lhdn.requests
    assert (request["method"], request["url"]) == ("POST", f"{BASE}/connect/token")
    assert request["headers"]["onbehalfof"] == "C1234567890"
    assert parse_qs(request["body"].decode()) == {
        "client_id": ["erp-client-id"],
        "client_secret": ["erp-client-secret"],
        "grant_type": ["client_credentials"],
        "scope": ["InvoicingAPI"],  # the request that worked in the sandbox by hand sent it (#42 sandbox findings)
    }
    assert (session.company_id, session.token, session.onbehalf_of) == (7, "tok-1", "C1234567890")
    assert session.expires_at == T0 + timedelta(seconds=3600)


def test_a_branch_logs_in_with_tin_and_rob():
    gateway, lhdn, _ = a_gateway(token_answer())

    gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890", rob="IG9876543210")

    assert lhdn.requests[0]["headers"]["onbehalfof"] == "C1234567890:IG9876543210"


def test_logging_in_for_our_own_company_sends_no_onbehalfof():
    # D60 (4) step one: our own company as a taxpayer. The port takes a TIN, so an empty one means "no intermediary".
    gateway, lhdn, _ = a_gateway(token_answer())

    gateway.login_as_taxpayer(CREDENTIALS, 1, "")

    assert "onbehalfof" not in lhdn.requests[0]["headers"]


def answer(status, headers=None, body=b""):
    return HttpResponse(status, headers or {}, body)


@pytest.mark.parametrize("status", [400, 401])
def test_wrong_client_credentials_are_an_authentication_failure_that_is_not_retried(status):
    gateway, lhdn, _ = a_gateway(answer(status, body=b'{"error": "invalid_client"}'))

    with pytest.raises(AuthenticationFailed) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert isinstance(raised.value, PermanentError)
    assert len(lhdn.requests) == 1


def test_a_400_unauthorized_client_is_a_permission_denial_because_the_taxpayer_has_not_authorised_us():
    # What the preprod sandbox answers (#42) when the taxpayer has not added us as intermediary: 400, not 403.
    gateway, lhdn, _ = a_gateway(answer(400, body=b'{"error": "unauthorized_client"}'))

    with pytest.raises(PermissionDenied) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert isinstance(raised.value, PermanentError) and not isinstance(raised.value, AuthenticationFailed)
    assert len(lhdn.requests) == 1


def test_a_400_invalid_client_stays_an_authentication_failure():
    gateway, _, _ = a_gateway(answer(400, body=b'{"error": "invalid_client"}'))

    with pytest.raises(AuthenticationFailed):
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")


def test_login_asks_for_the_invoicing_scope_without_which_the_sandbox_answers_400():
    gateway, lhdn, _ = a_gateway(token_answer())

    gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert parse_qs(lhdn.requests[0]["body"].decode())["scope"] == ["InvoicingAPI"]


def test_a_403_is_a_permission_denial_not_a_transient_failure():
    # Production credentials in the sandbox, or a taxpayer that has not authorised us: no retry helps (D57 (4)).
    gateway, _, _ = a_gateway(answer(403))

    with pytest.raises(PermissionDenied) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert isinstance(raised.value, PermanentError) and not isinstance(raised.value, TransientError)


def test_a_429_is_transient_and_carries_retry_after():
    gateway, _, _ = a_gateway(answer(429, {"Retry-After": "12"}))

    with pytest.raises(RateLimited) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert isinstance(raised.value, TransientError) and raised.value.retry_after == 12


def test_a_429_without_retry_after_waits_until_the_rate_limit_resets():
    # The preprod sandbox sends no Retry-After on a 429: it sends X-Rate-Limit-Reset, an ISO time (seen in #42).
    reset = {"X-Rate-Limit-Limit": "12", "X-Rate-Limit-Reset": "2026-11-10T09:00:20.4286288Z"}
    gateway, _, _ = a_gateway(answer(429, reset))  # the clock says 09:00:00

    with pytest.raises(RateLimited) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert raised.value.retry_after == 21  # 20.43 seconds, rounded up


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Rate-Limit-Reset": "2026-11-10T08:59:00Z"},  # already past
        {"X-Rate-Limit-Reset": "soon"},
        {"X-Rate-Limit-Reset": "2026-11-10T09:00:20"},  # no zone: not trusted
        {},
    ],
)
def test_a_reset_time_that_is_past_unreadable_or_missing_gives_no_retry_after(headers):
    gateway, _, _ = a_gateway(answer(429, headers))

    with pytest.raises(RateLimited) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert raised.value.retry_after is None


def test_retry_after_wins_over_the_reset_time_when_both_are_sent():
    gateway, _, _ = a_gateway(answer(429, {"Retry-After": "7", "X-Rate-Limit-Reset": "2026-11-10T09:00:20Z"}))

    with pytest.raises(RateLimited) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert raised.value.retry_after == 7


@pytest.mark.parametrize("failure", [answer(500), answer(503), TimeoutError("timed out"), ConnectionError("reset")])
def test_a_server_error_or_a_network_failure_is_transient(failure):
    gateway, _, _ = a_gateway(failure)

    with pytest.raises(TransientError) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert not isinstance(raised.value, PermanentError)


def test_no_failure_message_or_log_carries_a_credential_token_or_tin(caplog):
    caplog.set_level(logging.DEBUG)
    body = b'{"error": "invalid_client", "echo": "erp-client-secret C1234567890"}'
    gateway, _, _ = a_gateway(answer(401, body=body), answer(403, body=body), answer(429, body=body), answer(502, body=body))
    seen = []

    for _attempt in range(4):
        with pytest.raises(Exception) as raised:
            gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")
        seen.append(str(raised.value) + repr(raised.value))

    for secret in ("erp-client-secret", "erp-client-id", "C1234567890"):
        assert secret not in "".join(seen) + caplog.text


# --- the token cache: reuse, renewal before expiry, isolation (D57 (3), D60 (5)) --------------------------------------


def test_a_second_login_for_the_same_taxpayer_reuses_the_token():
    gateway, lhdn, _ = a_gateway(token_answer("tok-1"))

    first = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")
    second = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert len(lhdn.requests) == 1 and second == first


def test_a_token_is_replaced_by_a_fresh_login_before_it_expires():
    gateway, lhdn, clock = a_gateway(token_answer("tok-1"), token_answer("tok-2"))
    gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    clock.now = T0 + timedelta(minutes=54)
    assert gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890").token == "tok-1"

    clock.now = T0 + timedelta(minutes=56)  # inside the five-minute margin: LHDN has no refresh token
    renewed = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert (renewed.token, len(lhdn.requests)) == ("tok-2", 2)
    assert renewed.expires_at == clock.now + timedelta(minutes=60)


def test_a_token_for_one_taxpayer_is_never_given_for_another():
    gateway, lhdn, _ = a_gateway(token_answer("tok-a"), token_answer("tok-b"), token_answer("tok-a-branch"))

    a = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1111111111")
    b = gateway.login_as_taxpayer(CREDENTIALS, 8, "C2222222222")
    a_branch = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1111111111", rob="IG1")

    assert (a.token, b.token, a_branch.token) == ("tok-a", "tok-b", "tok-a-branch")
    assert [r["headers"]["onbehalfof"] for r in lhdn.requests] == ["C1111111111", "C2222222222", "C1111111111:IG1"]


def test_a_cached_token_is_handed_out_with_the_asking_companys_id():
    gateway, _, _ = a_gateway(token_answer("tok-1"))
    gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert gateway.login_as_taxpayer(CREDENTIALS, 8, "C1234567890").company_id == 8


def test_a_token_for_one_erp_identity_is_never_used_for_another():
    other = IntermediaryCredentials(client_id="other-client-id", client_secret="other-secret")
    gateway, lhdn, _ = a_gateway(token_answer("tok-1"), token_answer("tok-2"))

    first = gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")
    second = gateway.login_as_taxpayer(other, 7, "C1234567890")

    assert (first.token, second.token, len(lhdn.requests)) == ("tok-1", "tok-2", 2)


def test_a_token_for_one_environment_is_never_used_in_another():
    cache = TokenCache()  # even a cache shared by both gateways keeps them apart
    sandbox_lhdn, production_lhdn = ScriptedLhdn(token_answer("sandbox")), ScriptedLhdn(token_answer("production"))
    sandbox = MyInvoisHttpGateway(BASE, "sandbox", CREDENTIALS, sandbox_lhdn, now=Clock(), cache=cache)
    production = MyInvoisHttpGateway(BASE, "production", CREDENTIALS, production_lhdn, now=Clock(), cache=cache)

    assert sandbox.login_as_taxpayer(CREDENTIALS, 7, "C1234567890").token == "sandbox"
    assert production.login_as_taxpayer(CREDENTIALS, 7, "C1234567890").token == "production"


def test_a_failed_login_is_not_cached():
    gateway, lhdn, _ = a_gateway(answer(403), token_answer("tok-1"))
    with pytest.raises(PermissionDenied):
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890").token == "tok-1"
    assert len(lhdn.requests) == 2


def test_the_cache_shows_no_credential_token_or_tin_when_printed():
    cache = TokenCache()
    gateway = MyInvoisHttpGateway(BASE, "sandbox", CREDENTIALS, ScriptedLhdn(token_answer("tok-secret")), now=Clock(), cache=cache)
    gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    shown = repr(cache) + str(cache) + repr(gateway)

    for secret in ("tok-secret", "erp-client-secret", "erp-client-id", "C1234567890"):
        assert secret not in shown


# --- TIN validation: 200 valid, 400 bad format, 404 not found (D58 (2)) ----------------------------------------------


def test_a_tin_is_validated_with_the_buyers_id_type_and_value_under_our_own_login():
    gateway, lhdn, _ = a_gateway(token_answer("tok-own"), answer(200))

    result = gateway.validate_tin("C1234567890", "BRN", "201901234567")

    login, check = lhdn.requests
    assert "onbehalfof" not in login["headers"]  # a buyer is looked up as ourselves, not as one client
    assert (check["method"], check["url"]) == ("GET", f"{BASE}/api/v1.0/taxpayer/validate/C1234567890?idType=BRN&idValue=201901234567")
    assert check["headers"]["Authorization"] == "Bearer tok-own"
    assert result is TinCheck.VALID


@pytest.mark.parametrize("status, expected", [(200, TinCheck.VALID), (400, TinCheck.BAD_FORMAT), (404, TinCheck.NOT_FOUND)])
def test_each_answer_for_a_tin_is_one_of_three_outcomes(status, expected):
    gateway, _, _ = a_gateway(token_answer(), answer(status))

    assert gateway.validate_tin("C1234567890", "NRIC", "900101015555") is expected


def test_validating_reuses_our_own_token_for_the_next_check():
    gateway, lhdn, _ = a_gateway(token_answer(), answer(200), answer(404))

    gateway.validate_tin("C1234567890", "NRIC", "900101015555")
    gateway.validate_tin("C9999999999", "NRIC", "900101015555")

    assert [r["url"].split("/")[-1].split("?")[0] for r in lhdn.requests] == ["token", "C1234567890", "C9999999999"]


def test_an_unknown_id_type_is_refused_before_anything_is_sent():
    gateway, lhdn, _ = a_gateway()

    with pytest.raises(ValueError):
        gateway.validate_tin("C1234567890", "DRIVING", "x")

    assert lhdn.requests == []


def test_a_tin_that_is_not_url_safe_cannot_change_the_path():
    gateway, lhdn, _ = a_gateway(token_answer(), answer(400))

    gateway.validate_tin("../connect/token?x=", "BRN", "a b&idType=NRIC")

    assert lhdn.requests[1]["url"] == f"{BASE}/api/v1.0/taxpayer/validate/..%2Fconnect%2Ftoken%3Fx%3D?idType=BRN&idValue=a%20b%26idType%3DNRIC"


def test_validation_failures_other_than_the_three_answers_are_classified_like_login_failures():
    gateway, _, _ = a_gateway(token_answer(), answer(403), answer(429, {"Retry-After": "3"}), answer(503))

    with pytest.raises(PermissionDenied):
        gateway.validate_tin("C1234567890", "BRN", "1")
    with pytest.raises(RateLimited) as raised:
        gateway.validate_tin("C1234567890", "BRN", "1")
    assert raised.value.retry_after == 3
    with pytest.raises(TransientError):
        gateway.validate_tin("C1234567890", "BRN", "1")


# --- answers LHDN's contract does not describe, and a token LHDN no longer honours ------------------------------------


@pytest.mark.parametrize("status", [404, 422, 302])
def test_an_unexpected_status_is_a_permanent_error_the_job_runner_can_classify(status):
    gateway, _, _ = a_gateway(answer(status, body=b"not json"))

    with pytest.raises(UnexpectedResponse) as raised:
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")

    assert isinstance(raised.value, PermanentError)


def test_a_login_answer_without_a_token_is_a_permanent_error():
    gateway, _, _ = a_gateway(answer(200, body=b'{"unexpected": true}'))

    with pytest.raises(UnexpectedResponse):
        gateway.login_as_taxpayer(CREDENTIALS, 7, "C1234567890")


def test_a_token_lhdn_no_longer_honours_is_replaced_by_one_fresh_login_and_the_call_retried():
    gateway, lhdn, _ = a_gateway(token_answer("old"), answer(401), token_answer("new"), answer(404))

    result = gateway.validate_tin("C1234567890", "BRN", "1")

    assert result is TinCheck.NOT_FOUND
    assert lhdn.requests[3]["headers"]["Authorization"] == "Bearer new"


def test_a_login_the_credentials_fail_is_not_repeated_when_validating_a_tin():
    # Only a 401 on the validation request itself means "token no longer honoured". A refused login is permanent, and
    # logging in again with the same credentials could meet a 429 and turn it into a retryable error.
    gateway, lhdn, _ = a_gateway(answer(400, body=b'{"error": "invalid_client"}'))

    with pytest.raises(AuthenticationFailed):
        gateway.validate_tin("C1234567890", "BRN", "1")

    assert len(lhdn.requests) == 1


def test_a_refused_login_is_not_masked_by_a_rate_limit_on_a_second_try():
    gateway, _, _ = a_gateway(answer(400, body=b'{"error": "invalid_client"}'), answer(429, {"Retry-After": "3"}))

    with pytest.raises(AuthenticationFailed):
        gateway.validate_tin("C1234567890", "BRN", "1")


def test_a_second_401_after_a_fresh_login_is_an_authentication_failure():
    gateway, lhdn, _ = a_gateway(token_answer("old"), answer(401), token_answer("new"), answer(401))

    with pytest.raises(AuthenticationFailed):
        gateway.validate_tin("C1234567890", "BRN", "1")

    assert len(lhdn.requests) == 4
