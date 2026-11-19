from django.db import migrations

from core.rls import WORKER_ROLE, definer_function_sql

# The submission job (#47) must email the owner when a document goes stale (D58 (6)), but the worker role has no
# grant on core_companyaccess (migration 0003: SELECT is web-only, read-only, RLS-scoped to the signed-in user's own
# rows) and never will: widening that grant would let every job handler enumerate a company's whole membership.
# This SECURITY DEFINER function exposes exactly one narrow fact instead, for the one company id the caller (an
# already company-scoped job handler) already has in hand: the current owner's email, never the membership list.
FUNCTIONS = {
    "company_owner_email(bigint)": """
CREATE FUNCTION company_owner_email(p_company_id bigint) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
    SELECT u.email FROM core_companyaccess ca JOIN core_user u ON u.id = ca.user_id
    WHERE ca.company_id = p_company_id AND ca.role = 'owner'
$$
"""
}

FORWARD, REVERSE = definer_function_sql(FUNCTIONS, role=WORKER_ROLE)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0047_company_go_live_date'),
    ]

    operations = [
        migrations.RunSQL(FORWARD, REVERSE),
    ]
