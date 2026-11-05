import pytest
from django.contrib.auth import get_user_model

from core import jobs, ports


@pytest.fixture
def probe_job():
    """A job that touches every port, standing in for the real jobs later tickets add."""

    @jobs.register("probe")
    def probe(user_id):
        p = ports.get()
        p.storage.put(f"probe/{user_id}", b"payload")
        p.gateway.validate_tin("C1234567890", "BRN", "201901234567")
        p.ai.extract(p.storage.get(f"probe/{user_id}"), {"type": "object"}, idempotency_key=jobs.idempotency_key("ai"))
        p.email.send("owner@example.com", "Probe", "done", idempotency_key=jobs.idempotency_key("email"))

    yield
    jobs.HANDLERS.pop("probe", None)


@pytest.mark.django_db
def test_signed_in_user_page_and_queued_job_work_with_all_four_fake_ports(client, fake_ports, probe_job):
    user = get_user_model().objects.create_user("owner@example.com", password="unused-long-password")
    client.force_login(user)

    response = client.get("/")
    assert response.status_code == 200

    jobs.enqueue("probe", user_id=user.pk)
    jobs.run_pending()

    assert fake_ports.storage.objects[f"probe/{user.pk}"] == b"payload"
    assert fake_ports.gateway.tins_validated == [("C1234567890", "BRN", "201901234567")]
    assert len(fake_ports.ai.extractions) == 1
    assert fake_ports.email.sent[0]["subject"] == "Probe"


@pytest.mark.django_db
def test_the_page_refuses_anonymous_visitors(client):
    assert client.get("/").status_code == 401


@pytest.mark.django_db
def test_the_page_is_translated_to_chinese_on_request(client):
    client.force_login(get_user_model().objects.create_user("owner@example.com", password="unused-long-password"))

    english = client.get("/").content.decode()
    chinese = client.get("/", headers={"accept-language": "zh-hans"}).content.decode()

    assert "Welcome" in english
    assert "欢迎" in chinese
