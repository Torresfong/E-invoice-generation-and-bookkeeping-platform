import uuid

from django.contrib.auth import SESSION_KEY
from django.db import connection, transaction
from psycopg.pq import TransactionStatus

from .rls import CORRELATION_SETTING, IDENTITY_SETTING, WEB_ROLE


class TenantContextMiddleware:
    """Run every request as the `web` database role, with the signed-in user as the database identity.

    Both are transaction-local: the request is wrapped in a transaction, `SET LOCAL ROLE` drops the
    connection to the role that row-level security applies to, and the user id is set the same way, so
    nothing carries over to the next request on a pooled connection. Anonymous requests get an empty
    identity, which matches no company. Place it after AuthenticationMiddleware.

    When the request is already inside an outer transaction (tests), releasing the request's savepoint
    does not undo SET LOCAL, so the role and identity are reset explicitly on the normal path. If a
    database error left the transaction aborted (Django may already have turned it into a 500 response),
    more SQL would only raise and hide the original error, so the request's transaction is marked for
    rollback instead, which undoes them.

    Limits are documented in docs/architecture/tenancy.md: the web role can still change its own setting,
    and a streaming response is read after this middleware returns, outside the role.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # The identity comes from the session, not from loading the user: loading the user is itself a
        # query that row-level security restricts, and it must run as the signed-in user.
        session_user = request.session.get(SESSION_KEY, "") if hasattr(request, "session") else ""
        user_id = str(session_user) if str(session_user).isdigit() else ""

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(f"SET LOCAL ROLE {WEB_ROLE}")
                cursor.execute("SELECT set_config(%s, %s, true)", [IDENTITY_SETTING, user_id])
                # One id for every audit entry this request writes (D50 (6)).
                cursor.execute("SELECT set_config(%s, %s, true)", [CORRELATION_SETTING, str(uuid.uuid4())])
            if user_id and not request.user.is_authenticated:
                # Django rejects a session after a password change or a deactivation only when the user is
                # loaded, which happens here under the session's own identity. Drop the identity if it was
                # rejected, so a stale session keeps no access to tenant data.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT set_config(%s, '', true)", [IDENTITY_SETTING])
            response = self.get_response(request)
            if connection.connection.info.transaction_status == TransactionStatus.INERROR:
                transaction.set_rollback(True)
            else:
                with connection.cursor() as cursor:
                    cursor.execute("RESET ROLE")
                    cursor.execute("SELECT set_config(%s, '', true)", [IDENTITY_SETTING])
                    cursor.execute("SELECT set_config(%s, '', true)", [CORRELATION_SETTING])
            return response
