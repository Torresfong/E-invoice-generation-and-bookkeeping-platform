from django.apps import AppConfig


class MyinvoisConfig(AppConfig):
    """Everything LHDN-specific (D59 (1), D61 (3)). It reads `core`; `core` never imports it.

    It reaches the invoice engine's side only through the gateway port and the job runner. When a ticket adds a job
    handler (#47 onward), it registers it with `core.jobs.register` from a `myinvois/jobs.py` that this config imports in
    `ready()`, so the engine never names it. Nothing is registered yet; #128 only moves and fences.
    """

    name = "myinvois"
