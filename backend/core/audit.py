"""The audit trail (D40 (6)): who did what, to which record, when.

Create, update and delete events on business tables are written by a database trigger, so application code
cannot forget or bypass them (`enable_audit` in a table's migration). Sign-in events go through
`record_auth_event`. Domain events that are not a row change, such as an approval, are written with `record`.
Entries hold the names of changed fields and never their values; nothing here may be given a credential,
a token or a customer's details.

Events that belong to a firm, not a company (an accountant's sign-in, an operator command), go to the separate firm
trail (D50) with `record_firm_event`; `enable_firm_audit` does for a table with a `firm_id` what `enable_audit` does for
one with a `company_id`. A firm event is never written into a company's trail.

A firm accountant's reads of company data are recorded too (D45): put `audited_read` on every screen that shows
company data to an accountant. See "Audited reads" in docs/architecture/tenancy.md.
"""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from functools import wraps

from django.db import migrations
from django.db.models.functions import Now

from . import permissions
from .db import scalar
from .models import AuditEntry, CompanyAccess, FirmAuditEntry
from .rls import CORRELATION_SETTING

# Reading the same thing again within this window is one entry, so a reload does not flood the trail.
VIEW_WINDOW = timedelta(minutes=5)


def record_auth_event(action, email=None):
    """Write a sign-in event for each company the account belongs to. Nothing is written for an unknown address.

    Pass the address for a sign-in (the person is not identified yet); leave it out for a sign-out.
    """
    scalar("SELECT record_auth_event(%s, %s)", [action, email])


def record(action, company, actor, target_type="", target_id="", changed_fields=()):
    """Write a domain event as the signed-in user, for example an approval. Row changes are audited by trigger instead."""
    return AuditEntry.objects.create(
        company=company,
        actor_id=actor.pk,
        actor_email=actor.email,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        changed_fields=list(changed_fields),
    )


OPERATOR = "operator"


def record_operator_event(action, company, target_type, target_id, changed_fields=()):
    """Write a company-trail entry for something the platform operator did from a command, with no signed-in user.

    The actor is shown as "operator" (D50 (1)). Like every entry it names fields and never their values.
    """
    return AuditEntry.objects.create(
        company=company,
        actor_email=OPERATOR,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        changed_fields=list(changed_fields),
    )


def record_firm_event(action, firm, actor=None, target_type="", target_id="", changed_fields=()):
    """Write a firm-level event (D50). `actor` is the signed-in user; leave it out for an operator command.

    Like every entry it names fields and never their values. Its correlation id is the request's or job's, or the one
    of the enclosing `correlated()` block.
    """
    return FirmAuditEntry.objects.create(
        firm=firm,
        actor_id=actor.pk if actor else None,
        actor_email=actor.email if actor else OPERATOR,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        changed_fields=list(changed_fields),
    )


def current_correlation_id():
    """The correlation id an entry written now would carry (a fresh one, not kept, if nothing has set it)."""
    return scalar("SELECT audit_correlation_id()")


@contextmanager
def correlated():
    """Give the entries written in this block one correlation id, for work that is neither a request nor a job.

    Inside a request or job its id is kept. Use it inside a transaction: the id is transaction-local.
    """
    if scalar("SELECT nullif(current_setting(%s, true), '')", [CORRELATION_SETTING]):
        yield
        return
    scalar("SELECT set_config(%s, %s, true)", [CORRELATION_SETTING, str(uuid.uuid4())])
    try:
        yield
    finally:
        scalar("SELECT set_config(%s, '', true)", [CORRELATION_SETTING])


def record_read(user, company_id, target_type, target_id=""):
    """Record that `user` viewed a company's data: what kind of thing and which one, never what it contained.

    Skipped when the same user viewed the same thing in the same company within `VIEW_WINDOW` (database time).
    The time of the entry is stamped by the database.
    """
    target_id = str(target_id)
    recent = AuditEntry.objects.filter(
        company_id=company_id,
        actor_id=user.pk,
        action=AuditEntry.VIEWED,
        target_type=target_type,
        target_id=target_id,
        occurred_at__gte=Now() - VIEW_WINDOW,
    )
    if recent.exists():
        return
    AuditEntry.objects.create(
        company_id=company_id,
        actor_id=user.pk,
        actor_email=user.email,
        action=AuditEntry.VIEWED,
        target_type=target_type,
        target_id=target_id,
        changed_fields=[],
    )


def audited_read(target_type, id_arg=None):
    """Decorate a screen that shows a company's data: a firm accountant's successful view is recorded (D45).

    The company is the view's `pk` argument. `target_type` names what is shown ("company", "document"); `id_arg`
    names the view argument that holds the record's id, and is left out for a list. Nothing is recorded for a
    client, for a request that is refused or not found, for an anonymous visitor, or for a request that is not a
    read (a POST is not a view).
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            response = view(request, *args, **kwargs)
            company_id = kwargs["pk"]
            is_read = request.method in ("GET", "HEAD") and response.status_code == 200
            if is_read and permissions.role_in(request.user, company_id) == CompanyAccess.ACCOUNTANT:
                record_read(request.user, company_id, target_type, kwargs[id_arg] if id_arg else "")
            return response

        wrapper.audited_read = True  # what the guard test looks for
        return wrapper

    return decorator


def _trigger_sql(table, column, trigger, function):
    """The first statement refuses a table without `id` and `column`: the trigger would otherwise fail on the first
    write and block it."""
    return [
        "DO $$ BEGIN IF (SELECT count(*) FROM information_schema.columns WHERE table_schema = current_schema() "
        f"AND table_name = '{table}' AND column_name IN ('id', '{column}')) < 2 THEN "
        f"RAISE EXCEPTION 'cannot audit {table}: it needs id and {column} columns'; END IF; END $$",
        f"CREATE TRIGGER {trigger} AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION {function}()",
    ]


def audit_trigger_sql(table):
    """SQL that puts `table` under the audit trigger. The table needs `id` and `company_id` columns."""
    return _trigger_sql(table, "company_id", "audit_changes", "audit_row_change")


def firm_audit_trigger_sql(table):
    """SQL that puts `table` under the firm audit trigger. The table needs `id` and `firm_id` columns."""
    return _trigger_sql(table, "firm_id", "firm_audit_changes", "firm_audit_row_change")


def drop_audit_trigger_sql(table):
    return [f"DROP TRIGGER IF EXISTS audit_changes ON {table}"]


def drop_firm_audit_trigger_sql(table):
    return [f"DROP TRIGGER IF EXISTS firm_audit_changes ON {table}"]


def enable_audit(table):
    """A migration operation that audits every insert, update and delete on `table`.

    Add it after `enable_tenant_rls` in the migration that creates a business table.
    """
    return migrations.RunSQL(audit_trigger_sql(table), drop_audit_trigger_sql(table))


def enable_firm_audit(table):
    """Like `enable_audit`, into the firm trail, for a table that carries `id` and `firm_id`."""
    return migrations.RunSQL(firm_audit_trigger_sql(table), drop_firm_audit_trigger_sql(table))
