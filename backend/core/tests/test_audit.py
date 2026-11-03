import re

import pytest
from django.db import connection
from django.test import Client

from core.models import AuditEntry

from .factories import PASSWORD
from .probe import create_probe_note


def sign_in(client, user, password=PASSWORD):
    return client.post("/login/", {"email": user.email, "password": password})


def trail(client, company):
    response = client.get(f"/companies/{company.pk}/audit/")
    assert response.status_code == 200
    return response.content.decode()


def test_a_successful_sign_in_appears_in_the_owners_trail(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        other = Client()
        sign_in(other, world["acme_owner"])
        page = trail(other, world["acme"])

    assert "staff@acme.example" in page and "Signed in" in page


def test_a_failed_sign_in_for_an_existing_account_is_recorded(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"], "not the password")
        owner = Client()
        sign_in(owner, world["acme_owner"])
        page = trail(owner, world["acme"])

    assert "staff@acme.example" in page and "Failed sign-in" in page


def test_signing_out_is_recorded(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        client.post("/logout/")
        owner = Client()
        sign_in(owner, world["acme_owner"])
        page = trail(owner, world["acme"])

    assert "Signed out" in page


def test_a_failed_sign_in_for_an_unknown_address_is_not_recorded_anywhere(client, world, as_production_connection):
    with as_production_connection():
        client.post("/login/", {"email": "nobody@nowhere.example", "password": "whatever"})

    assert not AuditEntry.objects.filter(action=AuditEntry.LOGIN_FAILED).exists()


def test_attempts_refused_by_the_lock_are_not_recorded(client, world, as_production_connection):
    with as_production_connection():
        for _ in range(5):
            sign_in(client, world["acme_staff"], "not the password")
        assert sign_in(client, world["acme_staff"], PASSWORD).status_code == 429

    assert AuditEntry.objects.filter(action=AuditEntry.LOGIN_FAILED).count() == 5


def activity(client, company):
    response = client.get(f"/companies/{company.pk}/activity/")
    assert response.status_code == 200
    return response.content.decode()


def test_an_owner_sees_the_trail_of_their_own_company_and_none_from_another(client, world, as_production_connection):
    with as_production_connection():
        sign_in(Client(), world["beta_owner"])
        sign_in(Client(), world["acme_staff"])
        sign_in(client, world["acme_owner"])
        page = trail(client, world["acme"])

    assert "staff@acme.example" in page and "owner@beta.example" not in page


def test_an_owner_cannot_open_another_companys_trail(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = client.get(f"/companies/{world['beta'].pk}/audit/")

    assert response.status_code == 404


def test_an_accountant_sees_the_trail_of_every_authorised_company_and_each_only_has_its_own_entries(
    client, world, as_production_connection
):
    with as_production_connection():
        sign_in(Client(), world["acme_staff"])
        sign_in(Client(), world["beta_owner"])
        sign_in(client, world["accountant"])
        acme, beta = trail(client, world["acme"]), trail(client, world["beta"])

    assert "staff@acme.example" in acme and "owner@beta.example" not in acme
    assert "owner@beta.example" in beta and "staff@acme.example" not in beta


def test_staff_are_refused_the_full_trail(client, world, as_production_connection):
    with as_production_connection():
        sign_in(Client(), world["acme_owner"])
        sign_in(client, world["acme_staff"])
        response = client.get(f"/companies/{world['acme'].pk}/audit/")

    assert response.status_code == 403 and "owner@acme.example" not in response.content.decode()


def test_staff_see_only_their_own_activity(client, world, as_production_connection):
    with as_production_connection():
        sign_in(Client(), world["acme_owner"])
        sign_in(client, world["acme_staff"])
        page = activity(client, world["acme"])

    assert "staff@acme.example" in page and "owner@acme.example" not in page


def test_an_owner_sees_only_their_own_entries_on_the_activity_page(client, world, as_production_connection):
    with as_production_connection():
        sign_in(Client(), world["acme_staff"])
        sign_in(client, world["acme_owner"])
        page = activity(client, world["acme"])

    assert "owner@acme.example" in page and "staff@acme.example" not in page


def test_activity_in_another_company_is_not_found(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        assert client.get(f"/companies/{world['beta'].pk}/activity/").status_code == 404


def test_anonymous_visitors_see_nothing(client, world, as_production_connection):
    with as_production_connection():
        assert client.get(f"/companies/{world['acme'].pk}/audit/").status_code == 401
        assert client.get(f"/companies/{world['acme'].pk}/activity/").status_code == 401


@pytest.mark.parametrize("path", ["audit", "activity"])
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_no_page_accepts_a_change_to_the_trail(client, world, path, method, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        response = getattr(client, method)(f"/companies/{world['acme'].pk}/{path}/", {"id": 1})

    assert response.status_code == 405


@pytest.fixture
def notes_table(db, settings):
    """A business table built the way later tickets will build theirs: tenant policy plus the audit trigger."""


    settings.ROOT_URLCONF = "core.tests.urls_probe"
    with connection.cursor() as cursor:
        create_probe_note(cursor, audit=True)


def make_note(client, company, body="first draft"):
    return client.post("/notes/", {"company": company.pk, "body": body}).json()["id"]


@pytest.mark.parametrize(
    "who, company, email",
    [
        ("acme_owner", "acme", "owner@acme.example"),
        ("acme_staff", "acme", "staff@acme.example"),
        ("accountant", "beta", "accountant@firm.example"),
    ],
)
def test_a_record_created_by_any_role_is_in_the_trail_with_who_and_what(
    client, world, notes_table, who, company, email, as_production_connection
):
    with as_production_connection():
        sign_in(client, world[who])
        note_id = make_note(client, world[company])
        viewer = Client()
        sign_in(viewer, world["accountant"])
        page = trail(viewer, world[company])

    assert email in page and "Created" in page and f"probe_note {note_id}" in page


def test_an_update_records_the_names_of_the_changed_fields_and_never_their_values(
    client, world, notes_table, as_production_connection
):
    secret = "customer TIN C1234567890 total RM 18,450.00"
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        note_id = make_note(client, world["acme"], secret)
        client.post(f"/notes/{note_id}/update/", {"body": "second draft " + secret})
        page = trail(client, world["acme"])

    assert "Updated" in page and "body" in page
    assert secret not in page and "C1234567890" not in page and "18,450" not in page


def test_a_delete_is_recorded(client, world, notes_table, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        note_id = make_note(client, world["acme"])
        client.post(f"/notes/{note_id}/delete/")
        page = trail(client, world["acme"])

    assert "Deleted" in page


def test_an_update_that_changes_nothing_records_nothing(client, world, notes_table, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        note_id = make_note(client, world["acme"], "same text")
        client.post(f"/notes/{note_id}/update/", {"body": "same text"})

    assert list(AuditEntry.objects.filter(target_type="probe_note").values_list("action", flat=True)) == ["create"]


def test_a_change_made_with_no_signed_in_user_is_recorded_as_the_system(world, notes_table):
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO probe_note (company_id, body) VALUES (%s, 'operator fix')", [world["acme"].pk])

    entry = AuditEntry.objects.get(target_type="probe_note")
    assert entry.actor_id is None and entry.actor_email == "" and entry.company_id == world["acme"].pk


def test_nothing_in_the_trail_contains_a_password_or_a_token(client, world, fake_ports, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"], "hunter2-definitely-wrong")
        sign_in(client, world["acme_staff"])
        client.post("/password-reset/", {"email": world["acme_staff"].email})
        client.post("/logout/")
    token = re.search(r"/reset/([\w-]+)/", fake_ports.email.sent[0]["body"]).group(1)

    with connection.cursor() as cursor:
        cursor.execute("SELECT string_agg(row(a.*)::text, ' ') FROM core_auditentry a")
        everything = cursor.fetchone()[0]

    assert everything
    for secret in ["hunter2-definitely-wrong", PASSWORD, token, "argon2"]:
        assert secret not in everything


def test_the_company_page_links_to_the_pages_the_role_may_read(client, world, as_production_connection):
    company_page = f"/companies/{world['acme'].pk}/"
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        owner = client.get(company_page).content.decode()
        client.post("/logout/")
        sign_in(client, world["acme_staff"])
        staff = client.get(company_page).content.decode()

    assert f"{company_page}audit/" in owner and f"{company_page}activity/" in owner
    assert f"{company_page}audit/" not in staff and f"{company_page}activity/" in staff


def test_the_audit_pages_are_in_chinese_on_request(client, world, as_production_connection):
    chinese = {"headers": {"accept-language": "zh-hans"}}
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        full = client.get(f"/companies/{world['acme'].pk}/audit/", **chinese).content.decode()
        own = client.get(f"/companies/{world['acme'].pk}/activity/", **chinese).content.decode()

    assert "审计记录" in full and "已登录" in full and "时间" in full
    assert "我的操作记录" in own


def test_someone_already_signed_in_is_sent_home_and_cannot_sign_in_as_another_account(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["acme_staff"])
        assert client.get("/login/").status_code == 302
        response = sign_in(client, world["acme_owner"])
        home = client.get("/").content.decode()

    assert response.status_code == 302
    assert "staff@acme.example" in home and "owner@acme.example" not in home


def test_a_delete_records_who_where_which_record_and_when_but_none_of_the_deleted_values(
    client, world, notes_table, as_production_connection
):
    secret = "customer TIN C1234567890 total RM 18,450.00"
    with as_production_connection():
        sign_in(client, world["acme_owner"])
        note_id = make_note(client, world["acme"], secret)
        client.post(f"/notes/{note_id}/delete/")

    with connection.cursor() as cursor:
        cursor.execute("SELECT string_agg(row(a.*)::text, ' ') FROM core_auditentry a")
        everything = cursor.fetchone()[0]
    deleted = AuditEntry.objects.get(action=AuditEntry.DELETE)
    assert deleted.actor_email == "owner@acme.example" and deleted.company_id == world["acme"].pk
    assert (deleted.target_type, deleted.target_id) == ("probe_note", str(note_id))
    assert deleted.occurred_at is not None and deleted.changed_fields == []
    assert secret not in everything and "C1234567890" not in everything and "18,450" not in everything
