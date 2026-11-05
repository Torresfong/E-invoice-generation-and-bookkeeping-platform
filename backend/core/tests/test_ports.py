import pytest

from core import ports


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
