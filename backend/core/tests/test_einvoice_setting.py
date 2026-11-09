"""The company e-invoice setting (ticket #119, D59 (2), (3), (5), D61 (3), D63).

One setting per company holds the supplier details and the current on/off switch; every change is also kept as an
append-only history row, and `einvoicing_enabled(company, on_date)` answers from that history. The owner alone may
change it, the database refuses anyone else, and TIN, BRN and ROB appear in no `repr`, no audit entry and no log.
"""

import json
from datetime import date

import pytest
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, connection, transaction

from core import einvoicing
from core.models import AuditEntry, CompanyAccess, CompanyEInvoiceSetting, CompanyEInvoiceSettingChange

from .factories import PASSWORD, make_company, make_member

# Made-up values, clearly not real registrations. No real TIN, BRN or ROB belongs in this repository (D59 (5)).
TIN, BRN, ROB = "C0000000001", "000000000001", "RB-TEST-0001"
SECRETS = (TIN, BRN, ROB)

D1, D2, D3 = date(2027, 1, 1), date(2027, 6, 1), date(2027, 12, 1)


def supplier(**overrides):
    fields = dict(
        tin=TIN,
        brn=BRN,
        rob=ROB,
        msic_code="82193",
        address_line1="1 Test Street",
        address_line2="",
        city="Kuala Lumpur",
        postal_zone="50000",
        state_code="14",
        country_code="MYS",
    )
    return {**fields, **overrides}


def change(user, company, *, enabled=True, status="voluntary", effective_from=D1, acknowledged=False, **overrides):
    return einvoicing.update_setting(
        user,
        company,
        einvoicing_enabled=enabled,
        status=status,
        effective_from=effective_from,
        acknowledged=acknowledged,
        **supplier(**overrides),
    )


def settle():
    """Run the end-of-transaction agreement checks now, as a commit would."""
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS einvoice_agreement_setting, einvoice_agreement_change IMMEDIATE")


def history(company):
    return list(CompanyEInvoiceSettingChange.objects.filter(company=company).order_by("id"))


# --- the question the engine asks ----------------------------------------------------------------------------------


def test_a_new_company_has_e_invoicing_off(world):
    assert einvoicing.einvoicing_enabled(world["acme"], D1) is False
    assert CompanyEInvoiceSetting.objects.filter(company=world["acme"]).count() == 0


def test_e_invoicing_is_on_from_the_date_it_takes_effect(world):
    change(world["acme_owner"], world["acme"], effective_from=D2)

    assert einvoicing.einvoicing_enabled(world["acme"], date(2027, 5, 31)) is False
    assert einvoicing.einvoicing_enabled(world["acme"], D2) is True
    assert einvoicing.einvoicing_enabled(world["acme"], D3) is True


def test_switching_off_later_does_not_change_the_answer_for_earlier_dates(world):
    change(world["acme_owner"], world["acme"], effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, effective_from=D3)

    assert einvoicing.einvoicing_enabled(world["acme"], D2) is True
    assert einvoicing.einvoicing_enabled(world["acme"], D3) is False


def test_one_company_being_on_says_nothing_about_another(world):
    change(world["acme_owner"], world["acme"])

    assert einvoicing.einvoicing_enabled(world["beta"], D3) is False


# --- who may change it ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["acme_staff", "accountant"])
def test_staff_and_accountants_cannot_change_the_setting(world, who):
    with pytest.raises(PermissionDenied):
        change(world[who], world["acme"])
    assert CompanyEInvoiceSetting.objects.count() == 0


def test_the_owner_of_another_company_cannot_change_it(world):
    with pytest.raises(PermissionDenied):
        change(world["beta_owner"], world["acme"])


@pytest.mark.parametrize("who", ["acme_staff", "accountant"])
def test_the_database_refuses_a_write_by_anyone_but_the_owner(world, as_user, who):
    change(world["acme_owner"], world["acme"])
    setting = CompanyEInvoiceSetting.objects.get(company=world["acme"])

    with as_user(world[who]):
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                CompanyEInvoiceSetting.objects.filter(pk=setting.pk).update(einvoicing_enabled=False)
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                CompanyEInvoiceSettingChange.objects.create(
                    company=world["acme"], changed_by=world[who], einvoicing_enabled=False, effective_from=D3, **supplier()
                )

    setting.refresh_from_db()
    assert setting.einvoicing_enabled is True and len(history(world["acme"])) == 1


def test_the_owner_changes_it_through_the_web_role(world, as_user):
    with as_user(world["acme_owner"]):
        change(world["acme_owner"], world["acme"])
        assert einvoicing.einvoicing_enabled(world["acme"], D1) is True


# --- isolation between companies -----------------------------------------------------------------------------------


def test_another_companys_setting_is_invisible_and_unwritable_for_every_role(world, as_user):
    change(world["acme_owner"], world["acme"])
    change(world["beta_owner"], world["beta"])
    acme_setting = CompanyEInvoiceSetting.objects.get(company=world["acme"])

    # Beta's owner and nobody else at Beta (the accountant belongs to both, so is tested separately)
    with as_user(world["beta_owner"]):
        assert list(CompanyEInvoiceSetting.objects.values_list("company_id", flat=True)) == [world["beta"].pk]
        assert CompanyEInvoiceSettingChange.objects.filter(company=world["acme"]).count() == 0
        assert CompanyEInvoiceSetting.objects.filter(pk=acme_setting.pk).update(einvoicing_enabled=False) == 0
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                CompanyEInvoiceSetting.objects.create(
                    company=world["acme"], effective_from=D1, einvoicing_enabled=True, **supplier()
                )
        with pytest.raises(DatabaseError):
            with transaction.atomic():
                CompanyEInvoiceSettingChange.objects.create(
                    company=world["acme"], changed_by=world["beta_owner"], einvoicing_enabled=True, effective_from=D1, **supplier()
                )

    acme_staff_company = make_company("Gamma Staff Co")
    outsider = make_member("staff@gamma.example", acme_staff_company, CompanyAccess.STAFF)
    with as_user(outsider):
        assert CompanyEInvoiceSetting.objects.count() == 0
        assert CompanyEInvoiceSettingChange.objects.count() == 0

    acme_setting.refresh_from_db()
    assert acme_setting.einvoicing_enabled is True


def test_every_member_of_the_company_can_read_the_setting(world, as_user):
    change(world["acme_owner"], world["acme"])

    for who in ("acme_owner", "acme_staff", "accountant"):
        with as_user(world[who]):
            assert CompanyEInvoiceSetting.objects.filter(company=world["acme"]).count() == 1


def test_a_company_has_at_most_one_setting(world):
    change(world["acme_owner"], world["acme"])

    # even a row that matches the latest change is refused: one per company
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            CompanyEInvoiceSetting.objects.create(
                company=world["acme"], effective_from=D1, einvoicing_enabled=True, status="voluntary", **supplier()
            )


def test_the_setting_cannot_be_written_to_differ_from_the_latest_recorded_change(world, as_user):
    change(world["acme_owner"], world["acme"])
    setting = CompanyEInvoiceSetting.objects.get(company=world["acme"])

    with as_user(world["acme_owner"]):
        for edit in ({"tin": "C0000000009"}, {"einvoicing_enabled": False}, {"status": "exempt"}, {"city": "Ipoh"}):
            with pytest.raises(DatabaseError):
                with transaction.atomic():
                    CompanyEInvoiceSetting.objects.filter(pk=setting.pk).update(**edit)

    setting.refresh_from_db()
    assert (setting.tin, setting.einvoicing_enabled, setting.status, setting.city) == (TIN, True, "voluntary", "Kuala Lumpur")


def test_a_setting_with_no_history_cannot_be_created(world):
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSetting.objects.create(
                company=world["acme"], effective_from=D1, einvoicing_enabled=True, **supplier()
            )


def test_the_setting_matches_the_history_when_the_status_is_not_checked(world):
    change(world["acme_owner"], world["acme"], enabled=False, status=None)

    assert CompanyEInvoiceSetting.objects.get(company=world["acme"]).status is None


def test_jobs_can_read_the_setting_but_not_write_it(db):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT has_table_privilege('worker', 'core_companyeinvoicesetting', 'SELECT'),"
            " has_table_privilege('worker', 'core_companyeinvoicesetting', 'INSERT'),"
            " has_table_privilege('worker', 'core_companyeinvoicesetting', 'UPDATE'),"
            " has_table_privilege('worker', 'core_companyeinvoicesettingchange', 'SELECT'),"
            " has_table_privilege('worker', 'core_companyeinvoicesettingchange', 'INSERT')"
        )
        assert cursor.fetchone() == (True, False, False, True, False)


# --- the history -----------------------------------------------------------------------------------------------------


def test_every_change_is_kept_with_its_date_and_person(world):
    change(world["acme_owner"], world["acme"], effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, effective_from=D2)

    first, second = history(world["acme"])
    assert (first.einvoicing_enabled, first.effective_from, first.changed_by) == (True, D1, world["acme_owner"])
    assert (second.einvoicing_enabled, second.effective_from, second.changed_by) == (False, D2, world["acme_owner"])
    assert first.changed_at is not None


def test_a_change_to_supplier_details_alone_is_kept_too(world):
    change(world["acme_owner"], world["acme"], effective_from=D1)
    change(world["acme_owner"], world["acme"], effective_from=D1, city="Petaling Jaya")

    assert [h.city for h in history(world["acme"])] == ["Kuala Lumpur", "Petaling Jaya"]
    assert CompanyEInvoiceSetting.objects.get(company=world["acme"]).city == "Petaling Jaya"


def test_the_history_records_a_status_going_from_not_checked_to_a_value(world):
    change(world["acme_owner"], world["acme"], enabled=False, status=None, effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, status="mandatory", effective_from=D2)

    assert [h.status for h in history(world["acme"])] == [None, "mandatory"]


def test_a_history_row_cannot_be_changed_or_deleted(world):
    change(world["acme_owner"], world["acme"])
    row = history(world["acme"])[0]

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSettingChange.objects.filter(pk=row.pk).update(einvoicing_enabled=False)
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSettingChange.objects.filter(pk=row.pk).delete()


def test_a_change_cannot_take_effect_before_an_earlier_one(world):
    change(world["acme_owner"], world["acme"], effective_from=D2)

    with pytest.raises(einvoicing.InvalidSetting):
        change(world["acme_owner"], world["acme"], effective_from=D1)
    assert len(history(world["acme"])) == 1


def test_the_database_refuses_a_change_dated_before_an_earlier_one(world):
    change(world["acme_owner"], world["acme"], effective_from=D2)

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSettingChange.objects.create(
                company=world["acme"], changed_by=world["acme_owner"], einvoicing_enabled=True, effective_from=D1, **supplier()
            )


# --- switching off a mandatory company needs an acknowledgement (D59 (5)) ---------------------------------------------


def test_switching_off_a_mandatory_company_needs_an_acknowledgement(world):
    change(world["acme_owner"], world["acme"], status="mandatory", effective_from=D1)

    with pytest.raises(einvoicing.AcknowledgementRequired):
        change(world["acme_owner"], world["acme"], enabled=False, status="mandatory", effective_from=D2)

    assert einvoicing.einvoicing_enabled(world["acme"], D3) is True and len(history(world["acme"])) == 1


def test_an_accepted_acknowledgement_is_recorded_with_who_and_when(world):
    change(world["acme_owner"], world["acme"], status="mandatory", effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, status="mandatory", effective_from=D2, acknowledged=True)

    kept = history(world["acme"])[-1]
    assert kept.einvoicing_enabled is False
    assert kept.acknowledged_by == world["acme_owner"]
    assert kept.acknowledged_at is not None
    assert kept.acknowledgement_version == einvoicing.ACKNOWLEDGEMENT_VERSION
    assert history(world["acme"])[0].acknowledged_by is None


def test_the_database_requires_the_acknowledgement_too(world):
    change(world["acme_owner"], world["acme"], status="mandatory", effective_from=D1)

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSettingChange.objects.create(
                company=world["acme"], changed_by=world["acme_owner"], einvoicing_enabled=False,
                status="mandatory", effective_from=D2, **supplier(),
            )


def test_leaving_the_mandatory_status_while_switching_off_still_needs_it(world):
    change(world["acme_owner"], world["acme"], status="mandatory", effective_from=D1)

    with pytest.raises(einvoicing.AcknowledgementRequired):
        change(world["acme_owner"], world["acme"], enabled=False, status="voluntary", effective_from=D2)


@pytest.mark.parametrize("status", ["voluntary", "exempt"])
def test_switching_off_a_company_that_is_not_mandatory_needs_none(world, status):
    change(world["acme_owner"], world["acme"], status=status, effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, status=status, effective_from=D2)

    assert history(world["acme"])[-1].acknowledged_by is None


def test_switching_off_when_the_status_is_not_checked_yet_needs_no_acknowledgement(world):
    change(world["acme_owner"], world["acme"], enabled=True, status=None, effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, status=None, effective_from=D2)

    kept = history(world["acme"])
    assert [h.status for h in kept] == [None, None]
    assert kept[-1].einvoicing_enabled is False and kept[-1].acknowledged_by is None


def test_the_database_lets_a_not_checked_status_be_switched_off_without_an_acknowledgement(world):
    change(world["acme_owner"], world["acme"], enabled=True, status=None, effective_from=D1)

    with transaction.atomic():
        CompanyEInvoiceSettingChange.objects.create(
            company=world["acme"], changed_by=world["acme_owner"], einvoicing_enabled=False, status=None,
            effective_from=D2, **supplier(),
        )
        CompanyEInvoiceSetting.objects.filter(company=world["acme"]).update(
            einvoicing_enabled=False, status=None, effective_from=D2
        )
        settle()

    assert history(world["acme"])[-1].einvoicing_enabled is False


def test_staying_off_or_switching_on_needs_no_acknowledgement(world):
    change(world["acme_owner"], world["acme"], enabled=False, status="mandatory", effective_from=D1)
    change(world["acme_owner"], world["acme"], enabled=False, status="mandatory", effective_from=D2, city="Ipoh")
    change(world["acme_owner"], world["acme"], enabled=True, status="mandatory", effective_from=D3)

    assert [h.acknowledged_by for h in history(world["acme"])] == [None, None, None]


# --- what a setting must hold ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "unknown"},
        {"tin": ""},
        {"brn": ""},
        {"msic_code": "8219"},
        {"msic_code": "ABCDE"},
        {"address_line1": ""},
        {"postal_zone": "5000"},
        {"state_code": "99"},
        {"country_code": "MY"},
        {"tin": "x" * 21},
    ],
)
def test_switching_on_needs_complete_valid_supplier_details(world, bad):
    with pytest.raises(einvoicing.InvalidSetting):
        change(world["acme_owner"], world["acme"], **bad)
    assert CompanyEInvoiceSetting.objects.count() == 0


def test_a_company_can_be_saved_off_before_its_details_are_known(world):
    einvoicing.update_setting(
        world["acme_owner"], world["acme"], einvoicing_enabled=False, status=None, effective_from=D1,
        **supplier(tin="", brn="", msic_code="", address_line1="", city="", postal_zone="", state_code=""),
    )

    assert einvoicing.einvoicing_enabled(world["acme"], D3) is False


def test_the_rob_is_optional(world):
    change(world["acme_owner"], world["acme"], rob="")

    assert CompanyEInvoiceSetting.objects.get(company=world["acme"]).rob == ""


# --- TIN, BRN and ROB stay out of repr, audit and logs ---------------------------------------------------------------


def test_no_repr_or_str_shows_a_tin_brn_or_rob(world):
    change(world["acme_owner"], world["acme"])
    rows = [CompanyEInvoiceSetting.objects.get(company=world["acme"]), *history(world["acme"])]

    for row in rows:
        for text in (repr(row), str(row), f"{row!r}", "%s" % row):
            assert not any(secret in text for secret in SECRETS), text


def test_the_audit_trail_names_the_changed_fields_and_never_their_values(world):
    change(world["acme_owner"], world["acme"])
    change(world["acme_owner"], world["acme"], city="Ipoh", tin="C0000000002")

    entries = AuditEntry.objects.filter(company=world["acme"], target_type__startswith="core_companyeinvoicesetting")
    dump = json.dumps(list(entries.values("target_type", "changed_fields", "action")))
    assert "core_companyeinvoicesetting" in dump
    assert "tin" in dump and "city" in dump
    assert not any(text in dump for text in (*SECRETS, "C0000000002", "Ipoh"))
    assert not any(text in repr(list(entries.values())) for text in (*SECRETS, "C0000000002"))


def test_a_refused_change_logs_no_tin_brn_or_rob(world, caplog):
    caplog.set_level("DEBUG")
    with pytest.raises(einvoicing.InvalidSetting):
        change(world["acme_owner"], world["acme"], msic_code="bad")
    with pytest.raises(PermissionDenied):
        change(world["acme_staff"], world["acme"])
    change(world["acme_owner"], world["acme"], status="mandatory")
    with pytest.raises(einvoicing.AcknowledgementRequired):
        change(world["acme_owner"], world["acme"], enabled=False, status="mandatory", effective_from=D2)

    assert not any(secret in caplog.text for secret in SECRETS)


# --- the owner's screen ------------------------------------------------------------------------------------------------


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def form(**overrides):
    values = {"einvoicing_enabled": "on", "status": "voluntary", "effective_from": "2027-01-01", **supplier()}
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def test_the_owner_saves_the_setting_from_the_screen(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = client.post(f"/companies/{world['acme'].pk}/einvoicing/", form())
        page = client.get(f"/companies/{world['acme'].pk}/einvoicing/").content.decode()

    assert response.status_code == 302
    assert einvoicing.einvoicing_enabled(world["acme"], D1) is True
    assert "82193" in page and 'name="einvoicing_enabled"' in page


def test_staff_and_accountants_see_the_setting_but_get_no_form_and_cannot_post(client, world, as_production_connection):
    change(world["acme_owner"], world["acme"])
    for who in ("acme_staff", "accountant"):
        with as_production_connection():
            sign_in(client, world[who])
            page = client.get(f"/companies/{world['acme'].pk}/einvoicing/")
            refused = client.post(f"/companies/{world['acme'].pk}/einvoicing/", form(einvoicing_enabled=None))
            client.post("/logout/")

        assert page.status_code == 200
        assert b'name="einvoicing_enabled"' not in page.content
        assert refused.status_code == 403

    assert einvoicing.einvoicing_enabled(world["acme"], D1) is True


def test_another_companys_screen_is_not_found(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["beta_owner"])
        assert client.get(f"/companies/{world['acme'].pk}/einvoicing/").status_code == 404
        assert client.post(f"/companies/{world['acme'].pk}/einvoicing/", form()).status_code == 404

    assert CompanyEInvoiceSetting.objects.count() == 0


def test_the_screen_asks_for_the_acknowledgement_before_switching_a_mandatory_company_off(
    client, world, as_production_connection
):
    change(world["acme_owner"], world["acme"], status="mandatory", effective_from=D1)
    url = f"/companies/{world['acme'].pk}/einvoicing/"
    off = form(einvoicing_enabled=None, status="mandatory", effective_from="2027-06-01")

    with as_production_connection():
        sign_in(client, world["acme_owner"])
        refused = client.post(url, off)
    assert refused.status_code == 200 and b'name="acknowledged"' in refused.content
    assert einvoicing.einvoicing_enabled(world["acme"], D3) is True

    with as_production_connection():
        accepted = client.post(url, {**off, "acknowledged": "on"})

    assert accepted.status_code == 302
    assert einvoicing.einvoicing_enabled(world["acme"], D3) is False
    assert history(world["acme"])[-1].acknowledged_by == world["acme_owner"]


def test_the_screen_shows_an_error_and_keeps_the_values_for_an_invalid_form(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = client.post(f"/companies/{world['acme'].pk}/einvoicing/", form(msic_code="bad"))

    assert response.status_code == 200 and b'role="alert"' in response.content
    assert CompanyEInvoiceSetting.objects.count() == 0


def test_the_screen_is_available_in_chinese(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        page = client.get(f"/companies/{world['acme'].pk}/einvoicing/", headers={"accept-language": "zh-hans"})

    assert "电子发票" in page.content.decode()


def test_the_company_page_links_to_the_screen(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        page = client.get(f"/companies/{world['acme'].pk}/").content.decode()

    assert f"/companies/{world['acme'].pk}/einvoicing/" in page


# --- the migration -----------------------------------------------------------------------------------------------------


def test_the_guard_triggers_reverse_and_apply_again(db):
    from importlib import import_module

    migration = import_module("core.migrations.0035_company_einvoice_setting")

    def guards():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_trigger WHERE tgname IN ('a_einvoice_owner_guard', 'b_einvoice_setting_guard', 'c_einvoice_setting_matches_history', 'b_einvoice_change_guard')"
            )
            return cursor.fetchone()[0]

    with connection.cursor() as cursor:
        for statement in migration.REVERSE:
            cursor.execute(statement)
        assert guards() == 0
        for statement in [*migration.FUNCTIONS_FORWARD, *migration.TRIGGERS_FORWARD]:
            cursor.execute(statement)

    assert guards() == 5


# --- the setting and its history agree when a transaction ends -------------------------------------------------------------


def test_a_history_row_appended_without_updating_the_setting_cannot_be_committed(world):
    change(world["acme_owner"], world["acme"])

    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSettingChange.objects.create(
                company=world["acme"], changed_by=world["acme_owner"], einvoicing_enabled=False,
                status="voluntary", effective_from=D2, **supplier(),
            )
            settle()


def test_a_history_row_with_no_setting_at_all_cannot_be_committed(world):
    with pytest.raises(DatabaseError):
        with transaction.atomic():
            CompanyEInvoiceSettingChange.objects.create(
                company=world["acme"], changed_by=world["acme_owner"], einvoicing_enabled=True,
                status="voluntary", effective_from=D1, **supplier(),
            )
            settle()


def test_the_service_writes_history_then_setting_and_they_agree_at_the_end(world):
    with transaction.atomic():
        change(world["acme_owner"], world["acme"], enabled=False, status=None)
        change(world["acme_owner"], world["acme"], effective_from=D2)
        settle()


@pytest.mark.django_db(transaction=True)
def test_two_first_saves_at_once_take_turns_instead_of_failing():
    import threading

    from .factories import make_company, make_member

    company = make_company("Race Co")
    owner = make_member("owner@race.example", company, CompanyAccess.OWNER)
    start, errors = threading.Barrier(2), []

    def save():
        try:
            start.wait()
            change(owner, company)
        except Exception as problem:  # noqa: BLE001 - the test reports whatever the save raised
            errors.append(type(problem).__name__)
        finally:
            connection.close()

    threads = [threading.Thread(target=save) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert CompanyEInvoiceSetting.objects.filter(company=company).count() == 1
    assert len(history(company)) == 2


# --- the screen says what the switch covers (D61 (2)) -------------------------------------------------------------------


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_the_screen_says_the_switch_governs_new_invoices_only(client, world, as_production_connection, who):
    with as_production_connection():
        sign_in(client, world[who])
        english = client.get(f"/companies/{world['acme'].pk}/einvoicing/").content.decode()
        chinese = client.get(
            f"/companies/{world['acme'].pk}/einvoicing/", headers={"accept-language": "zh-hans"}
        ).content.decode()

    assert "applies to new invoices only" in english and "already sent to LHDN" in english
    assert "仅适用于新发票" in chinese
