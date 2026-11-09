from django.db import migrations

from core.rls import definer_function_sql, owner_policy_sql

# Firm self-registration (D67 (1), ticket #142). An applicant has no account, so the only way in is this one function; the
# table itself is closed to the web role, which has no privilege on it (there are no default privileges, see 0003), and
# forced row-level security with a policy for the table owner alone keeps even a future broad grant from opening it. The
# operator decides applications from commands, which run as the owner.

# Stores an application and nothing else: no firm and no user. Returns false, storing nothing, when the address already has
# an account, the name matches a firm ignoring case, or an open application already has that name or address, so the
# caller answers the same either way and learns nothing about who is registered (D40 (1)). Blank or over-long fields raise
# 22023; the view checks them first.
SUBMIT_FIRM_APPLICATION = """
CREATE FUNCTION submit_firm_application(p_firm_name text, p_ssm_number text, p_contact_name text, p_email text, p_people text)
RETURNS boolean LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, public, pg_temp AS
$$
DECLARE
    v_rows integer;
BEGIN
    IF btrim(p_firm_name) = '' OR length(p_firm_name) > 200
       OR btrim(p_ssm_number) = '' OR length(p_ssm_number) > 50
       OR btrim(p_contact_name) = '' OR length(p_contact_name) > 200
       OR btrim(p_email) = '' OR length(p_email) > 254
       OR length(p_people) > 2000 THEN
        RAISE EXCEPTION 'invalid firm application' USING ERRCODE = '22023';
    END IF;
    IF EXISTS (SELECT 1 FROM core_user WHERE lower(email) = lower(btrim(p_email)))
       OR EXISTS (SELECT 1 FROM core_firm WHERE lower(name) = lower(btrim(p_firm_name))) THEN
        RETURN false;
    END IF;
    INSERT INTO core_firmapplication (firm_name, ssm_number, contact_name, contact_email, people, status,
                                      decided_by, refusal_note)
    VALUES (btrim(p_firm_name), btrim(p_ssm_number), btrim(p_contact_name), lower(btrim(p_email)), btrim(p_people),
            'pending', '', '')
    ON CONFLICT DO NOTHING;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    RETURN v_rows > 0;
END
$$
"""

FUNCTION_FORWARD, FUNCTION_REVERSE = definer_function_sql(
    {"submit_firm_application(text, text, text, text, text)": SUBMIT_FIRM_APPLICATION}
)

FORWARD = [
    "ALTER TABLE core_firmapplication ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_firmapplication FORCE ROW LEVEL SECURITY",
    *owner_policy_sql("core_firmapplication"),
    *FUNCTION_FORWARD,
]
REVERSE = [
    *FUNCTION_REVERSE,
    "DROP POLICY IF EXISTS definer_functions ON core_firmapplication",
    "ALTER TABLE core_firmapplication DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0038_firm_application")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
