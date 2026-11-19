"""The real object storage adapter, over Amazon S3 (ticket #14, D14, D53 (3)). Every test uses a fake S3 client."""

import logging

import pytest

from core import ports
from core.ports import startup
from core.ports.s3 import ObjectStorageCallFailed, S3Storage

SETTINGS = {"OBJECT_STORAGE_BUCKET": "acme-documents", "OBJECT_STORAGE_REGION": "ap-southeast-5"}


class FakeS3Client:
    """What the adapter needs of boto3's `s3` client: `put_object` and `get_object`."""

    def __init__(self, fail_with=None):
        self.objects = {}
        self.fail_with = fail_with

    def put_object(self, Bucket, Key, Body):
        if self.fail_with:
            raise self.fail_with
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if self.fail_with:
            raise self.fail_with

        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(self.objects[(Bucket, Key)])}


def adapter(client=None):
    return S3Storage(client or FakeS3Client(), bucket="acme-documents")


# --- the port seam ----------------------------------------------------------------------------------------------


def test_a_write_lands_under_the_configured_bucket_and_the_given_key():
    client = FakeS3Client()

    adapter(client).put("documents/1/2026-11/7-invoice.png", b"bytes")

    assert client.objects[("acme-documents", "documents/1/2026-11/7-invoice.png")] == b"bytes"


def test_a_read_returns_what_was_written():
    client = FakeS3Client()
    storage = adapter(client)
    storage.put("k", b"bytes")

    assert storage.get("k") == b"bytes"


def test_a_failed_write_raises_so_the_job_can_retry():
    client = FakeS3Client(fail_with=OSError("connection reset"))

    with pytest.raises(ObjectStorageCallFailed):
        adapter(client).put("k", b"bytes")


def test_a_failed_read_raises_so_the_job_can_retry():
    client = FakeS3Client(fail_with=OSError("connection reset"))

    with pytest.raises(ObjectStorageCallFailed):
        adapter(client).get("k")


# --- start-up ----------------------------------------------------------------------------------------------------


def test_start_up_installs_the_storage_adapter_and_no_other_port(settings):
    ports.uninstall()
    settings.OBJECT_STORAGE_BUCKET = SETTINGS["OBJECT_STORAGE_BUCKET"]
    settings.OBJECT_STORAGE_REGION = SETTINGS["OBJECT_STORAGE_REGION"]
    made = []

    startup.install_configured_ports({}, storage_client=lambda region: made.append(region) or FakeS3Client())

    assert isinstance(ports.get().storage, S3Storage)
    assert made == ["ap-southeast-5"]
    for other in ("gateway", "ai", "email"):
        with pytest.raises(ports.PortsNotConfigured):
            getattr(ports.get(), other)


def test_start_up_without_a_bucket_installs_no_storage_adapter(settings):
    ports.uninstall()
    settings.OBJECT_STORAGE_BUCKET = ""

    startup.install_configured_ports({}, storage_client=lambda region: pytest.fail("no client without a bucket"))

    with pytest.raises(ports.PortsNotConfigured):
        ports.get()


def test_the_deploy_check_warns_when_no_storage_adapter_is_installed():
    ports.uninstall()
    ports.install_port("gateway", object())

    assert [w.id for w in startup.check_storage_installed(None)] == ["core.W003"]

    ports.install_port("storage", adapter())

    assert startup.check_storage_installed(None) == []


@pytest.fixture
def offline_aws(monkeypatch):
    """Building a real boto3 client resolves credentials; dummy ones and no metadata service keep that offline."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-key-id")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def test_the_real_client_never_retries_a_call(offline_aws):
    """A job's own retry and backoff (ADR 0001) cover a failure; boto3 is given no retry of its own."""
    from core.ports.s3 import s3_client

    config = s3_client("ap-southeast-5").meta.config

    assert config.read_timeout == 30
    assert config.connect_timeout == 5
    assert config.retries["total_max_attempts"] == 1


def test_the_real_client_does_not_let_botocore_log_request_bodies(offline_aws):
    """botocore logs every request's parameters at DEBUG, and a request body here is a client's document."""
    from core.ports.s3 import s3_client

    botocore_log = logging.getLogger("botocore")
    saved = botocore_log.level
    botocore_log.setLevel(logging.DEBUG)  # as when the root logger is at DEBUG: the case the guard exists for
    try:
        s3_client("ap-southeast-5")

        assert botocore_log.level == logging.INFO
    finally:
        botocore_log.setLevel(saved)  # the guard changes process-wide state; later tests must not inherit it
