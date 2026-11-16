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


PORT_NAMES = ("gateway", "ai", "email", "storage")

_installed = None  # a whole set at once: what the tests install
_adapters = {}  # one real adapter at a time, each installed by the ticket that builds it


class _InstalledAdapters:
    """What `get()` returns while only some ports have a real adapter: the others fail loudly, never fall back to a fake."""

    def __getattr__(self, name):
        if name not in PORT_NAMES:
            raise AttributeError(name)
        try:
            return _adapters[name]
        except KeyError:
            raise PortsNotConfigured(f"The {name} port has no adapter installed; refusing to guess an implementation.") from None


def install(ports):
    """Install an object exposing .gateway, .ai, .email and .storage."""
    global _installed
    _installed = ports


def install_port(name, adapter):
    """Install the adapter of one port. The other ports stay uninstalled until their own adapter is."""
    if name not in PORT_NAMES:
        raise ValueError(f"port must be one of {', '.join(PORT_NAMES)}")
    _adapters[name] = adapter


def uninstall():
    global _installed
    _installed = None
    _adapters.clear()


def get():
    if _installed is not None:
        return _installed
    if _adapters:
        return _InstalledAdapters()
    raise PortsNotConfigured("No external ports installed; refusing to guess an implementation.")
