"""Install the real adapters when the app starts (#159, D14).

`CoreConfig.ready()` calls this for the web process and the worker alike. Each real adapter has its own installer here,
so installing one never makes `ports.get()` pretend another exists; the email adapter (#151) adds its own. Tests install
fakes with `ports.install()` after this has run, so no test reaches a real service. Nothing here makes a network call:
the gateway logs in only when a caller first needs it.
"""

import os

from django.conf import settings

from . import install_port
from .myinvois import gateway_from_environment, urllib_transport


def install_configured_ports(env=os.environ, transport=urllib_transport):
    """Install every real adapter whose settings and secrets are present. One that is missing stays uninstalled."""
    gateway = gateway_from_environment(settings.MYINVOIS_ENVIRONMENT, env, transport)
    if gateway is not None:
        install_port("gateway", gateway)
