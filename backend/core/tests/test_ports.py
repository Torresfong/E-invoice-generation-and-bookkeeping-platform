from io import StringIO

import pytest
from django.core.management import call_command

from core import ports
from core.jobs import TransientError
from core.ports import startup
from core.ports.fakes import FakeEmail
from core.ports.myinvois import MyInvoisHttpGateway

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


def test_a_hung_lhdn_is_given_up_on_after_ten_seconds_on_the_request_path(monkeypatch):
    waited = []

    def hung(request, timeout=None):
        waited.append(timeout)
        raise OSError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", hung)
    ports.uninstall()
    startup.install_configured_ports(SANDBOX)  # the real transport, as at start-up

    with pytest.raises(TransientError):
        ports.get().gateway.validate_tin("C1234567890", "BRN", "201901234567")

    assert waited == [10]


def test_the_app_start_up_itself_installs_the_gateway_from_the_environment(monkeypatch):
    from django.apps import apps

    ports.uninstall()
    for name, value in SANDBOX.items():
        monkeypatch.setenv(name, value)

    apps.get_app_config("core").ready()

    assert isinstance(ports.get().gateway, MyInvoisHttpGateway)
    with pytest.raises(ports.PortsNotConfigured):
        ports.get().email


# --- a missing gateway is reported at deploy, not at the first customer save (#180, D14) ---------------------------------


def deploy_check_output():
    out, err = StringIO(), StringIO()
    call_command("check", "--deploy", stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def test_the_deploy_check_reports_a_missing_gateway_and_does_not_fail_it():
    ports.uninstall()

    report = deploy_check_output()  # a warning: call_command would raise on an error

    assert "no MyInvois gateway installed" in report


def test_the_deploy_check_is_quiet_when_the_gateway_is_installed():
    ports.uninstall()
    startup.install_configured_ports(SANDBOX, transport=no_answer)

    assert "no MyInvois gateway installed" not in deploy_check_output()


def test_a_plain_check_passes_without_credentials_and_says_nothing_about_the_gateway():
    ports.uninstall()
    out, err = StringIO(), StringIO()

    call_command("check", stdout=out, stderr=err)

    assert "MyInvois" not in out.getvalue() + err.getvalue()
