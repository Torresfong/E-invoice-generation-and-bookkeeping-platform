"""The real MyInvois gateway behind the port (D14, D57, D58, D60). Login as intermediary and TIN validation (#42).

Signing, submission, polling and cancellation come with #47 and #48.

Every request goes through one `transport(method, url, headers, body) -> HttpResponse`, so tests script LHDN's
answers and the real sandbox is reached only by the default transport. Credentials, tokens and TINs are never logged
and never put in an exception message.
"""

import hashlib
import json
import os
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import quote, urlencode

from django.utils import timezone

from core.jobs import PermanentError, TransientError

from .interfaces import ID_TYPES, IntermediaryCredentials, TaxpayerSession, TinCheck


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict = field(default_factory=dict, repr=False)
    body: bytes = field(default=b"", repr=False)

    def json(self):
        return json.loads(self.body)


class AuthenticationFailed(PermanentError):
    """LHDN refused our client ID and secret (400 or 401). Retrying the same credentials cannot help."""


class PermissionDenied(PermanentError):
    """LHDN answered 403: wrong environment for the credentials, or the taxpayer has not authorised us for this.

    It is never a transient failure and never retried (D57 (4)); an outage is `TransientError` and is not revocation.
    """


class UnexpectedResponse(PermanentError):
    """LHDN answered with a status or body this contract does not describe. Only the fact is kept, never the body."""


class RateLimited(TransientError):
    """LHDN answered 429. `retry_after` is the seconds it asked us to wait, if it said."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(headers):
    value = {name.lower(): v for name, v in headers.items()}.get("retry-after", "")
    return int(value) if value.isdigit() else None


def urllib_transport(method, url, headers, body, timeout=30):
    """The real transport. An error status is an answer to return, not an exception; a network failure is an `OSError`."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:  # noqa: S310 - the base URL is our own configuration
            return HttpResponse(answer.status, dict(answer.headers), answer.read())
    except urllib.error.HTTPError as error:
        return HttpResponse(error.code, dict(error.headers), error.read())


RENEW_BEFORE_EXPIRY = timedelta(minutes=5)


class TokenCache:
    """Tokens by environment, ERP identity and represented taxpayer (D57 (3)): one is never used for another.

    The key holds a hash of the client ID, and nothing prints: a cache in a log or a traceback shows no secret.
    """

    def __init__(self):
        self._tokens = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(environment, credentials, onbehalf_of):
        return environment, hashlib.sha256(credentials.client_id.encode()).hexdigest(), onbehalf_of

    def get(self, environment, credentials, onbehalf_of, now):
        """The cached (token, expires_at), or None when there is none or it is within the renewal margin of expiring."""
        with self._lock:
            cached = self._tokens.get(self._key(environment, credentials, onbehalf_of))
        return cached if cached is not None and now < cached[1] - RENEW_BEFORE_EXPIRY else None

    def evict(self, environment, credentials, onbehalf_of):
        with self._lock:
            self._tokens.pop(self._key(environment, credentials, onbehalf_of), None)

    def put(self, environment, credentials, onbehalf_of, token, expires_at):
        with self._lock:
            self._tokens[self._key(environment, credentials, onbehalf_of)] = (token, expires_at)

    def __repr__(self):
        return f"<TokenCache: {len(self._tokens)} tokens>"


class MyInvoisHttpGateway:
    def __init__(self, base_url, environment, credentials, transport, now=timezone.now, cache=None):
        self.base_url = base_url.rstrip("/")
        self.environment = environment
        self.credentials = credentials
        self._transport = transport
        self._now = now
        self._cache = cache if cache is not None else TokenCache()

    def __repr__(self):
        return f"<MyInvoisHttpGateway {self.environment}>"

    def _send(self, method, path, headers, body=None, answers=()):
        """Make one request and return it if LHDN accepted it; otherwise raise the error the job runner acts on.

        `answers` are statuses the caller reads itself (a 404 for a TIN is an answer, not a failure). Messages say what happened and never repeat anything LHDN or the caller sent: they can echo a secret or a TIN.
        """
        try:
            answer = self._transport(method, f"{self.base_url}{path}", headers, body)
        except OSError:
            raise TransientError("MyInvois could not be reached") from None
        if answer.status in answers:
            return answer
        if answer.status == 429:
            raise RateLimited("MyInvois rate limit reached", _retry_after(answer.headers))
        if answer.status == 403:
            raise PermissionDenied("MyInvois refused the operation (403)")
        if answer.status in (400, 401):
            raise AuthenticationFailed(f"MyInvois refused the credentials ({answer.status})")
        if answer.status >= 500:
            raise TransientError(f"MyInvois is unavailable ({answer.status})")
        if not 200 <= answer.status < 300:
            raise UnexpectedResponse(f"MyInvois answered {answer.status}, which the contract does not describe")
        return answer

    def login_as_taxpayer(self, credentials, company_id, tin, rob=None):
        onbehalf_of = f"{tin}:{rob}" if rob else tin
        cached = self._cache.get(self.environment, credentials, onbehalf_of, self._now())
        if cached is not None:
            return TaxpayerSession(company_id=company_id, tin=tin, token=cached[0], expires_at=cached[1], rob=rob)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if onbehalf_of:
            headers["onbehalfof"] = onbehalf_of
        body = urlencode(
            {
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "grant_type": "client_credentials",
                "scope": "InvoicingAPI",
            }
        )
        requested_at = self._now()
        answer = self._send("POST", "/connect/token", headers, body.encode())
        try:
            token, lifetime = answer.json()["access_token"], answer.json()["expires_in"]
            expires_at = requested_at + timedelta(seconds=lifetime)
        except (KeyError, TypeError, ValueError):
            raise UnexpectedResponse("MyInvois answered the login without a usable token") from None
        self._cache.put(self.environment, credentials, onbehalf_of, token, expires_at)
        return TaxpayerSession(company_id=company_id, tin=tin, token=token, expires_at=expires_at, rob=rob)

    def _validate_once(self, path):
        own = self.login_as_taxpayer(self.credentials, 0, "")
        return self._send("GET", path, {"Authorization": f"Bearer {own.token}"}, answers=(400, 404))

    def validate_tin(self, tin, id_type, id_value):
        """Looked up under our own login, with no `onbehalfof`: the buyer is not one of our clients."""
        if id_type not in ID_TYPES:
            raise ValueError(f"id_type must be one of {', '.join(ID_TYPES)}")
        query = urlencode({"idType": id_type, "idValue": id_value}, quote_via=quote)
        path = f"/api/v1.0/taxpayer/validate/{quote(tin, safe='')}?{query}"
        try:
            answer = self._validate_once(path)
        except AuthenticationFailed:
            # LHDN has stopped honouring the token (it can be lost on their side before it expires): log in once more.
            self._cache.evict(self.environment, self.credentials, "")
            answer = self._validate_once(path)
        return {400: TinCheck.BAD_FORMAT, 404: TinCheck.NOT_FOUND}.get(answer.status, TinCheck.VALID)


ENVIRONMENTS = ("sandbox", "production")


def gateway_from_environment(environment, env=os.environ, transport=urllib_transport):
    """The gateway for `environment`, or None when its base URL, client ID or client secret is not set.

    The three values come from configuration outside the repository, one set per environment, and are never logged.
    """
    if environment not in ENVIRONMENTS:
        raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
    prefix = f"MYINVOIS_{environment.upper()}_"
    base_url, client_id, client_secret = (env.get(prefix + name, "") for name in ("BASE_URL", "CLIENT_ID", "CLIENT_SECRET"))
    if not (base_url and client_id and client_secret):
        return None
    return MyInvoisHttpGateway(base_url, environment, IntermediaryCredentials(client_id, client_secret), transport)
