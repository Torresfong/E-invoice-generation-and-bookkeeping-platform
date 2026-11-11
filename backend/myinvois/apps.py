from django.apps import AppConfig


class MyinvoisConfig(AppConfig):
    """Everything LHDN-specific (D59 (1), D61 (3)). It reads `core`; `core` never imports it.

    It reaches the invoice engine's side only through the gateway port and the job runner. Job handlers it adds are
    registered with `core.jobs.register` from a `myinvois/jobs.py` imported in `ready()`, so the engine never names them.
    """

    name = "myinvois"
