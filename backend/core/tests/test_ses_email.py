"""The real email adapter, over the Amazon SES API (#151, D14, D53 (3)). Every test uses a fake SES client."""

import logging

import pytest

from core import jobs, ports
from core.models import AuthToken, CompanyAccess
from core.ports import startup
from core.ports.interfaces import IdempotencyKeyMissing
from core.ports.ses import EmailSendFailed, SesEmail

from .factories import make_company, make_member

SETTINGS = {"EMAIL_SENDER_ADDRESS": "noreply@pilot.example", "EMAIL_REGION": "ap-southeast-5"}
LINK_BODY = "Set your password: http://app.example/reset/SECRET-TOKEN-VALUE/"


class FakeSesClient:
    """What the adapter needs of boto3's `sesv2` client: `send_email(**request)`."""

    def __init__(self, fail_with=None):
        self.requests = []
        self.fail_with = fail_with

    def send_email(self, **request):
        if self.fail_with:
            raise self.fail_with
        self.requests.append(request)
        return {"MessageId": "abc"}


def adapter(client=None):
    return SesEmail(client or FakeSesClient(), sender="noreply@pilot.example")


# --- the port seam ---------------------------------------------------------------------------------------------------


def test_a_message_goes_to_the_one_recipient_from_the_configured_sender():
    client = FakeSesClient()

    adapter(client).send("owner@acme.example", "Your link", LINK_BODY)

    (request,) = client.requests
    assert request["FromEmailAddress"] == "noreply@pilot.example"
    assert request["Destination"] == {"ToAddresses": ["owner@acme.example"]}
    assert request["Content"]["Simple"]["Subject"]["Data"] == "Your link"
    assert request["Content"]["Simple"]["Body"]["Text"]["Data"] == LINK_BODY


def test_a_failed_send_raises_so_the_caller_can_roll_back():
    client = FakeSesClient(fail_with=OSError("connection reset"))

    with pytest.raises(EmailSendFailed):
        adapter(client).send("owner@acme.example", "Your link", LINK_BODY)


def test_a_repeated_idempotency_key_sends_once():
    client = FakeSesClient()
    email = adapter(client)

    email.send("owner@acme.example", "Invoice", "Body", idempotency_key="job-7")
    email.send("owner@acme.example", "Invoice", "Body", idempotency_key="job-7")
    email.send("owner@acme.example", "Invoice", "Body", idempotency_key="job-8")

    assert len(client.requests) == 2


def test_a_send_that_failed_does_not_use_up_its_key():
    client = FakeSesClient(fail_with=OSError("down"))
    email = adapter(client)
    with pytest.raises(EmailSendFailed):
        email.send("owner@acme.example", "Invoice", "Body", idempotency_key="job-7")

    client.fail_with = None
    email.send("owner@acme.example", "Invoice", "Body", idempotency_key="job-7")

    assert len(client.requests) == 1


def test_messages_without_a_key_are_never_merged():
    client = FakeSesClient()

    adapter(client).send("a@example.com", "S", "B")
    adapter(client).send("a@example.com", "S", "B")

    assert len(client.requests) == 2


def test_a_job_that_sends_without_its_key_is_refused(monkeypatch):
    monkeypatch.setattr(jobs, "in_job", lambda: True)

    with pytest.raises(IdempotencyKeyMissing):
        adapter().send("a@example.com", "S", "B")


def test_nothing_in_the_logs_holds_a_link_a_token_or_an_address(caplog):
    caplog.set_level(logging.DEBUG)
    client = FakeSesClient(fail_with=OSError("could not send " + LINK_BODY))
    with pytest.raises(EmailSendFailed):
        adapter(client).send("owner@acme.example", "Your link", LINK_BODY)

    client.fail_with = None
    adapter(client).send("owner@acme.example", "Your link", LINK_BODY)

    logged = caplog.text
    assert "SECRET-TOKEN-VALUE" not in logged
    assert "owner@acme.example" not in logged


@pytest.fixture
def offline_aws(monkeypatch):
    """Building a real boto3 client resolves credentials; dummy ones and no metadata service keep that offline."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_the_real_client_gives_up_in_ten_seconds_and_never_retries_a_send(offline_aws):
    """A request waits on the send, and a retry after a read timeout could deliver the message twice."""
    from core.ports.ses import ses_client

    config = ses_client("ap-southeast-5").meta.config

    assert config.read_timeout == 10
    assert config.connect_timeout == 5
    assert config.retries["total_max_attempts"] == 1


def test_the_real_client_does_not_let_botocore_log_request_bodies(offline_aws):
    """botocore logs every request's parameters at DEBUG, and the body holds a single-use link."""
    from core.ports.ses import ses_client

    ses_client("ap-southeast-5")

    assert logging.getLogger("botocore").getEffectiveLevel() >= logging.INFO


# --- start-up --------------------------------------------------------------------------------------------------------


def test_start_up_installs_the_email_adapter_and_no_other_port(settings):
    ports.uninstall()
    settings.EMAIL_SENDER_ADDRESS = SETTINGS["EMAIL_SENDER_ADDRESS"]
    settings.EMAIL_REGION = SETTINGS["EMAIL_REGION"]
    made = []

    startup.install_configured_ports({}, email_client=lambda region: made.append(region) or FakeSesClient())

    assert isinstance(ports.get().email, SesEmail)
    assert made == ["ap-southeast-5"]
    for other in ("gateway", "ai", "storage"):
        with pytest.raises(ports.PortsNotConfigured):
            getattr(ports.get(), other)


def test_start_up_without_a_sender_address_installs_no_email_adapter(settings):
    ports.uninstall()
    settings.EMAIL_SENDER_ADDRESS = ""

    startup.install_configured_ports({}, email_client=lambda region: pytest.fail("no client without a sender"))

    with pytest.raises(ports.PortsNotConfigured):
        ports.get()


def test_the_deploy_check_warns_when_no_email_adapter_is_installed():
    ports.uninstall()
    ports.install_port("gateway", object())

    assert [w.id for w in startup.check_email_installed(None)] == ["core.W002"]

    ports.install_port("email", adapter())

    assert startup.check_email_installed(None) == []


# --- the HTTP seam ---------------------------------------------------------------------------------------------------


def test_a_reset_request_whose_real_send_fails_answers_500_and_leaves_no_token(client, as_production_connection, db):
    make_member("owner@acme.example", make_company(), CompanyAccess.OWNER)
    ports.install(type("Ports", (), {"email": adapter(FakeSesClient(fail_with=OSError("down")))})())
    client.raise_request_exception = False

    with as_production_connection():
        response = client.post("/password-reset/", {"email": "owner@acme.example"})

    assert response.status_code == 500
    assert AuthToken.objects.count() == 0
