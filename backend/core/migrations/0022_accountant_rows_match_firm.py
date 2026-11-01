from django.db import migrations

# A firm accountant reaches a company only through an `accountant` access row, and RLS honours any such row. The
# services write them for the accountant's own firm only (D49); this makes the database refuse any other row,
# whoever writes it: a shell, a later admin screen, or code that changes a user's or a company's firm.
#
# The rule: for every `accountant` row, the user is an accountant and their firm is the company's firm. Owner and
# staff rows are not tied to a firm. The checks are deferred constraint triggers, so the steps of a move (delete the
# old firm's rows, change the company's firm, add the new firm's rows) may come in any order inside one transaction
# and only the committed result is judged. The function is SECURITY DEFINER so it sees every row whatever the
# writer's own row-level security allows; it is not callable by anyone and fires only as a trigger.

CHECK_EXISTING_ROWS = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM core_companyaccess a
        JOIN core_user u ON u.id = a.user_id
        JOIN core_company c ON c.id = a.company_id
        WHERE a.role = 'accountant' AND (NOT u.is_accountant OR u.firm_id IS DISTINCT FROM c.firm_id)
    ) THEN
        RAISE EXCEPTION 'Some existing accountant access rows are for a company of another firm, or for someone who is not an accountant; fix them before this migration';
    END IF;
END
$$
"""

FUNCTION = """
CREATE FUNCTION accountant_rows_match_firm() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_rows bigint[];
BEGIN
    -- the access rows this change could have made wrong
    IF TG_TABLE_NAME = 'core_companyaccess' THEN
        SELECT array_agg(a.id) INTO v_rows FROM core_companyaccess a WHERE a.id = NEW.id;
    ELSIF TG_TABLE_NAME = 'core_company' THEN
        SELECT array_agg(a.id) INTO v_rows FROM core_companyaccess a WHERE a.company_id = NEW.id;
    ELSE
        SELECT array_agg(a.id) INTO v_rows FROM core_companyaccess a WHERE a.user_id = NEW.id;
    END IF;
    -- Hold the companies and users involved, always in this order, until the transaction ends: a change to one of
    -- them in another transaction then waits for this one and is checked against it, instead of both passing.
    PERFORM 1 FROM core_company c
        WHERE c.id IN (SELECT a.company_id FROM core_companyaccess a WHERE a.id = ANY (v_rows)) ORDER BY c.id FOR SHARE;
    PERFORM 1 FROM core_user u
        WHERE u.id IN (SELECT a.user_id FROM core_companyaccess a WHERE a.id = ANY (v_rows)) ORDER BY u.id FOR SHARE;
    IF EXISTS (
        SELECT 1 FROM core_companyaccess a
        JOIN core_user u ON u.id = a.user_id
        JOIN core_company c ON c.id = a.company_id
        WHERE a.id = ANY (v_rows) AND a.role = 'accountant'
          AND (NOT u.is_accountant OR u.firm_id IS DISTINCT FROM c.firm_id)
    ) THEN
        RAISE EXCEPTION 'An accountant access row must be for a company of the accountant''s own firm';
    END IF;
    RETURN NULL;
END
$$
"""

TRIGGER = (
    "CREATE CONSTRAINT TRIGGER {name} AFTER {event} ON {table} DEFERRABLE INITIALLY DEFERRED "
    "FOR EACH ROW EXECUTE FUNCTION accountant_rows_match_firm()"
)

FORWARD = [
    CHECK_EXISTING_ROWS,
    FUNCTION,
    "REVOKE ALL ON FUNCTION accountant_rows_match_firm() FROM PUBLIC",
    # a bad row is written
    TRIGGER.format(name="accountant_rows_match_firm_access", event="INSERT OR UPDATE", table="core_companyaccess"),
    # a company moved while accountant rows are left behind
    TRIGGER.format(name="accountant_rows_match_firm_company", event="UPDATE OF firm_id", table="core_company"),
    # an accountant's firm or status changed under their rows
    TRIGGER.format(name="accountant_rows_match_firm_user", event="UPDATE OF firm_id, is_accountant", table="core_user"),
]

REVERSE = [
    "DROP TRIGGER IF EXISTS accountant_rows_match_firm_user ON core_user",
    "DROP TRIGGER IF EXISTS accountant_rows_match_firm_company ON core_company",
    "DROP TRIGGER IF EXISTS accountant_rows_match_firm_access ON core_companyaccess",
    "DROP FUNCTION IF EXISTS accountant_rows_match_firm()",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0021_firm_required")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
