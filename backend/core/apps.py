from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        # Django records last_login with an UPDATE on the user table, but the web role may only read its
        # own row, and nobody is identified yet at the moment of sign-in. Sign-ins go to the audit trail.
        from django.contrib.auth import user_logged_in
        from django.contrib.auth.models import update_last_login

        user_logged_in.disconnect(update_last_login, dispatch_uid="update_last_login")

        from core.ports.startup import install_configured_ports

        install_configured_ports()

        # Registers core.documents.store_uploaded_document with core.jobs (ticket #14), as myinvois/apps.py
        # documents doing for its own handlers.
        from core import documents  # noqa: F401
