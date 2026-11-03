from django.db import migrations

# A firm administrator reads no client data (D49). With no `CompanyAccess` row, row-level security shows them none,
# so the database refuses any such row, whoever writes it, and refuses flagging a user who has one. Deferred constraint
# triggers like 0022's, so only the committed result is judged. The function is SECURITY DEFINER so it sees every row
# whatever the writer's own row-level security allows; it is not callable by anyone and fires only as a trigger.

CHECK_EXISTING_ROWS = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM core_companyaccess a JOIN core_user u ON u.id = a.user_id WHERE u.is_firm_admin
    ) THEN
        RAISE EXCEPTION 'Some firm administrators have company access; fix them before this migration';
    END IF;
END
$$
"""

FUNCTION = """
CREATE FUNCTION firm_admins_have_no_company_access() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_user bigint;
BEGIN
    IF TG_TABLE_NAME = 'core_companyaccess' THEN
        v_user := NEW.user_id;
    ELSE
        v_user := NEW.id;
    END IF;
    IF EXISTS (
        SELECT 1 FROM core_user u JOIN core_companyaccess a ON a.user_id = u.id WHERE u.id = v_user AND u.is_firm_admin
    ) THEN
        RAISE EXCEPTION 'A firm administrator cannot have company access';
    END IF;
    RETURN NULL;
END
$$
"""

TRIGGER = (
    "CREATE CONSTRAINT TRIGGER {name} AFTER {event} ON {table} DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION firm_admins_have_no_company_access()"
)

FORWARD = [
    CHECK_EXISTING_ROWS,
    FUNCTION,
    "REVOKE ALL ON FUNCTION firm_admins_have_no_company_access() FROM PUBLIC",
    TRIGGER.format(name="firm_admins_have_no_company_access_row", event="INSERT OR UPDATE", table="core_companyaccess"),
    TRIGGER.format(name="firm_admins_have_no_company_access_user", event="UPDATE OF is_firm_admin", table="core_user"),
]

REVERSE = [
    "DROP TRIGGER IF EXISTS firm_admins_have_no_company_access_user ON core_user",
    "DROP TRIGGER IF EXISTS firm_admins_have_no_company_access_row ON core_companyaccess",
    "DROP FUNCTION IF EXISTS firm_admins_have_no_company_access()",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0026_firm_administrator_functions")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
