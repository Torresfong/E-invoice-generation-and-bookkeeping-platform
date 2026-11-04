"""Registry for the four external ports (MyInvois gateway, AI provider, email, object storage).

Application code reaches external services only through get(). Real adapters are installed by the
tickets that build them; tests install fakes.
"""

from .interfaces import AIProvider, EmailSender, MyInvoisGateway, ObjectStorage

__all__ = [
    "AIProvider",
    "EmailSender",
    "MyInvoisGateway",
    "ObjectStorage",
    "PortsNotConfigured",
    "get",
    "install",
    "uninstall",
]


class PortsNotConfigured(RuntimeError):
    pass


_installed = None


def install(ports):
    """Install an object exposing .gateway, .ai, .email and .storage."""
    global _installed
    _installed = ports


def uninstall():
    global _installed
    _installed = None


def get():
    if _installed is None:
        raise PortsNotConfigured("No external ports installed; refusing to guess an implementation.")
    return _installed
