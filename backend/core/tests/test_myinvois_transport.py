"""The default transport and the environment configuration of the real gateway (#42). A loopback server stands in for
LHDN, so nothing leaves this machine."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from core.ports.interfaces import IntermediaryCredentials
from core.ports.myinvois import MyInvoisHttpGateway, gateway_from_environment, urllib_transport


@pytest.fixture
def lhdn():
    """A loopback server that answers what the test sets in `answer` and records the requests it gets."""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            server.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, self.rfile.read(length)))
            status, headers, body = server.answer
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _reply

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server.seen, server.answer = [], (200, {}, b"{}")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.base_url = f"http://127.0.0.1:{server.server_port}"
    yield server
    server.shutdown()
    server.server_close()


def test_the_transport_sends_method_headers_and_body_and_returns_status_headers_and_body(lhdn):
    lhdn.answer = (200, {"Retry-After": "9"}, b'{"ok": true}')

    response = urllib_transport("POST", f"{lhdn.base_url}/connect/token", {"onbehalfof": "C1"}, b"a=b")

    (method, path, headers, body) = lhdn.seen[0]
    assert (method, path, headers["onbehalfof"], body) == ("POST", "/connect/token", "C1", b"a=b")
    assert (response.status, response.headers["Retry-After"], response.json()) == (200, "9", {"ok": True})


@pytest.mark.parametrize("status", [400, 403, 404, 429, 503])
def test_an_error_status_is_returned_not_raised(lhdn, status):
    lhdn.answer = (status, {}, b"{}")

    assert urllib_transport("GET", f"{lhdn.base_url}/x", {}, None).status == status


def test_an_unreachable_server_raises_an_os_error():
    with pytest.raises(OSError):
        urllib_transport("GET", "http://127.0.0.1:9/x", {}, None)


# --- configuration lives outside the repository, one set per environment ----------------------------------------------

SANDBOX = {
    "MYINVOIS_SANDBOX_BASE_URL": "https://preprod-api.example.test/",
    "MYINVOIS_SANDBOX_CLIENT_ID": "sandbox-id",
    "MYINVOIS_SANDBOX_CLIENT_SECRET": "sandbox-secret",
}


def test_the_gateway_is_built_from_the_environments_own_settings():
    gateway = gateway_from_environment("sandbox", SANDBOX)

    assert isinstance(gateway, MyInvoisHttpGateway)
    assert (gateway.base_url, gateway.environment) == ("https://preprod-api.example.test", "sandbox")
    assert gateway.credentials == IntermediaryCredentials("sandbox-id", "sandbox-secret")


def test_another_environments_settings_are_never_used():
    assert gateway_from_environment("production", SANDBOX) is None


@pytest.mark.parametrize("missing", list(SANDBOX))
def test_a_missing_or_empty_setting_means_not_configured(missing):
    assert gateway_from_environment("sandbox", {**SANDBOX, missing: ""}) is None
    assert gateway_from_environment("sandbox", {k: v for k, v in SANDBOX.items() if k != missing}) is None


def test_an_unknown_environment_is_refused():
    with pytest.raises(ValueError):
        gateway_from_environment("staging", SANDBOX)
