import pytest

from core import ports
from core.models import Customer
from core.ports import startup
from core.ports.fakes import FakeEmail
from core.ports.myinvois import HttpResponse, MyInvoisHttpGateway

from .factories import PASSWORD

SANDBOX = {
    "MYINVOIS_SANDBOX_BASE_URL": "https://preprod.example.invalid",
    "MYINVOIS_SANDBOX_CLIENT_ID": "client-id-value",
    "MYINVOIS_SANDBOX_CLIENT_SECRET": "client-secret-value",
}


def test_the_four_ports_are_reachable_and_record_what_callers_send(fake_ports):
    ports.get().gateway.validate_tin("C1234567890", "BRN", "201901234567")
    ports.get().ai.extract(b"doc", {"type": "object"})
    ports.get().email.send("a@example.com", "Subject", "Body")
    ports.get().storage.put("k", b"data")

    assert fake_ports.gateway.tins_validated == [("C1234567890", "BRN", "201901234567")]
    assert fake_ports.ai.extractions == [(b"doc", {"type": "object"})]
    assert fake_ports.email.sent[0]["to"] == "a@example.com"
    assert ports.get().storage.get("k") == b"data"


def test_using_ports_without_installing_any_fails_loudly():
    ports.uninstall()

    with pytest.raises(ports.PortsNotConfigured):
        ports.get()


# --- the real gateway is installed at start-up (#159, D14) ---------------------------------------------------------


def no_answer(method, url, headers, body):
    raise AssertionError("nothing in these tests may reach MyInvois")


def test_start_up_installs_the_real_gateway_and_no_other_port():
    ports.uninstall()

    startup.install_configured_ports(SANDBOX, transport=no_answer)

    assert isinstance(ports.get().gateway, MyInvoisHttpGateway)
    for other in ("ai", "email", "storage"):
        with pytest.raises(ports.PortsNotConfigured):
            getattr(ports.get(), other)


def test_start_up_without_credentials_installs_nothing_and_the_gateway_still_fails_loudly():
    ports.uninstall()

    startup.install_configured_ports({}, transport=no_answer)

    with pytest.raises(ports.PortsNotConfigured):
        ports.get().gateway


def test_start_up_uses_the_credentials_of_the_environment_the_app_runs_in(settings):
    settings.MYINVOIS_ENVIRONMENT = "production"
    ports.uninstall()

    startup.install_configured_ports(SANDBOX, transport=no_answer)  # only the sandbox set exists

    with pytest.raises(ports.PortsNotConfigured):
        ports.get().gateway


def test_another_port_can_be_installed_beside_the_gateway_and_each_keeps_its_own():
    ports.uninstall()
    startup.install_configured_ports(SANDBOX, transport=no_answer)
    email = FakeEmail()

    ports.install_port("email", email)

    assert ports.get().email is email
    assert isinstance(ports.get().gateway, MyInvoisHttpGateway)
    with pytest.raises(ports.PortsNotConfigured):
        ports.get().ai


@pytest.mark.parametrize(
    "answer",
    [OSError("connection refused"), HttpResponse(503), HttpResponse(429, {"Retry-After": "30"})],
    ids=["network failure", "5xx", "429"],
)
def test_a_real_gateway_outage_reaches_the_customer_screen_as_a_message_with_nothing_saved(
    client, world, as_production_connection, answer
):
    def transport(method, url, headers, body):
        if isinstance(answer, Exception):
            raise answer
        return answer

    ports.uninstall()
    startup.install_configured_ports(SANDBOX, transport=transport)
    with as_production_connection():
        assert client.post("/login/", {"email": world["acme_owner"].email, "password": PASSWORD}).status_code == 302
        response = client.post(
            f"/companies/{world['acme'].pk}/customers/",
            {"legal_name": "Buyer Sdn Bhd", "tin": "C1234567890", "id_type": "BRN", "id_value": "201901234567"},
        )

    body = response.content.decode()
    assert response.status_code == 200 and "Nothing was saved" in body
    assert "client-secret-value" not in body and "client-id-value" not in body
    assert Customer.objects.count() == 0
