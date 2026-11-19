from django.apps import AppConfig


class MyinvoisConfig(AppConfig):
    """Everything LHDN-specific (D59 (1), D61 (3)). It reads `core`; `core` never imports it.

    It reaches the invoice engine's side only through the gateway port and the job runner. `ready()` imports
    `myinvois.jobs`, whose module-level `@core.jobs.register` decorator registers the submission job (#47) by
    name, so the engine enqueues it without ever naming or importing this module.
    """

    name = "myinvois"

    def ready(self):
        from . import jobs  # noqa: F401 - imported for its @jobs.register side effect
