"""A firm accountant's reads of company data go to the audit trail (D45, D40 (6))."""

from datetime import timedelta

import pytest
from django.urls import get_resolver

from core import audit
from core.models import AuditEntry

from .factories import PASSWORD, make_accountant, make_company

CHINESE = {"accept-language": "zh-hans"}

# Every company screen is either audited or listed here with the reason it shows no company data to an
# accountant. A new screen under /companies/<id>/ must do one or the other.
NOT_AUDITED = {
    "companies/": "the accountant's own list of companies they may open, not any company's data (owner decision)",
    "companies/<int:pk>/invite/": "owner-only action: an accountant is refused before anything is shown",
    "companies/<int:pk>/activity/": "shows the signed-in user's own activity, not the company's data",
    "companies/<int:pk>/items/<int:item_pk>/deactivate/": "owner and staff action: an accountant is refused before anything is shown",
    "companies/<int:pk>/items/<int:item_pk>/activate/": "owner and staff action: an accountant is refused before anything is shown",
    "companies/<int:pk>/customers/<int:customer_pk>/deactivate/": "owner and staff action: an accountant is refused before anything is shown",
    "companies/<int:pk>/customers/<int:customer_pk>/activate/": "owner and staff action: an accountant is refused before anything is shown",
}


def sign_in(client, user):
    assert client.post("/login/", {"email": user.email, "password": PASSWORD}).status_code == 302


def reads(company):
    return list(AuditEntry.objects.filter(company=company, action=AuditEntry.VIEWED))


def company_routes(patterns=None, prefix=""):
    """Every company screen, as (route, view): the list and every route that carries a company id."""
    for pattern in patterns if patterns is not None else get_resolver("config.urls").url_patterns:
        route = prefix + str(pattern.pattern)
        if hasattr(pattern, "url_patterns"):
            yield from company_routes(pattern.url_patterns, route)
        elif route.startswith("companies/"):
            yield route, pattern.callback


def unaudited(routes):
    return [route for route, view in routes if not getattr(view, "audited_read", False) and route not in NOT_AUDITED]


AUDITED_SCREENS = [("/companies/{pk}/", "company"), ("/companies/{pk}/audit/", "audit_trail")]
PATHS = [path for path, _ in AUDITED_SCREENS]


@pytest.mark.parametrize("path, target", AUDITED_SCREENS)
def test_an_accountant_opening_a_screen_leaves_one_viewed_entry_naming_who_where_and_what(
    client, world, path, target, as_production_connection
):
    with as_production_connection():
        sign_in(client, world["accountant"])
        assert client.get(path.format(pk=world["acme"].pk)).status_code == 200

    [entry] = reads(world["acme"])
    assert (entry.actor_id, entry.actor_email) == (world["accountant"].pk, world["accountant"].email)
    assert entry.target_type == target
    assert (entry.target_id == str(world["acme"].pk)) == (target == "company")
    assert entry.occurred_at is not None


@pytest.mark.parametrize("who", ["acme_owner", "acme_staff"])
def test_a_client_opening_the_same_screens_leaves_no_read_entry(client, world, who, as_production_connection):
    with as_production_connection():
        sign_in(client, world[who])
        statuses = [client.get(path.format(pk=world["acme"].pk)).status_code for path in PATHS]

    assert statuses[0] == 200
    assert reads(world["acme"]) == []


def test_a_post_to_a_screen_is_not_a_read(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["accountant"])
        client.post(f"/companies/{world['acme'].pk}/")

    assert reads(world["acme"]) == []


def test_an_accountant_reading_one_company_leaves_nothing_in_another_companys_trail(
    client, world, as_production_connection
):
    with as_production_connection():
        sign_in(client, world["accountant"])
        client.get(f"/companies/{world['acme'].pk}/")

    assert len(reads(world["acme"])) == 1
    assert reads(world["beta"]) == []


def test_a_read_that_shows_nothing_is_not_recorded(client, world, as_production_connection):
    gamma = make_company("Gamma Bhd")  # the accountant has no access to it
    with as_production_connection():
        assert client.get(f"/companies/{world['acme'].pk}/").status_code == 401
        sign_in(client, world["accountant"])
        assert client.get(f"/companies/{gamma.pk}/").status_code == 404
        assert client.get(f"/companies/{gamma.pk}/audit/").status_code == 404

    assert AuditEntry.objects.filter(action=AuditEntry.VIEWED).count() == 0


def test_an_entry_holds_no_field_value_and_no_company_text(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["accountant"])
        for path in PATHS:
            client.get(path.format(pk=world["acme"].pk))

    entries = reads(world["acme"])
    assert len(entries) == 2
    for entry in entries:
        assert entry.changed_fields == []
        stored = {entry.actor_email, entry.action, entry.target_type, entry.target_id}
        assert stored <= {"accountant@firm.example", "viewed", "company", "audit_trail", str(world["acme"].pk), ""}


def test_reloading_within_the_window_adds_no_entry_and_a_read_after_it_adds_one(
    client, world, monkeypatch, as_production_connection
):
    path = f"/companies/{world['acme'].pk}/"
    with as_production_connection():
        sign_in(client, world["accountant"])
        client.get(path)
        client.get(path)
        client.get(f"{path}audit/")  # a different thing viewed is its own entry
    assert len(reads(world["acme"])) == 2

    monkeypatch.setattr(audit, "VIEW_WINDOW", timedelta(seconds=-1))  # a window that has already passed
    with as_production_connection():
        client.get(path)

    assert len(reads(world["acme"])) == 3


def test_two_accountants_each_get_their_own_entry_for_the_same_page(client, world, as_production_connection):
    other = make_accountant("other@firm.example", world["acme"])
    with as_production_connection():
        sign_in(client, world["accountant"])
        client.get(f"/companies/{world['acme'].pk}/")
        client.post("/logout/")
        sign_in(client, other)
        client.get(f"/companies/{world['acme'].pk}/")

    assert sorted(entry.actor_email for entry in reads(world["acme"])) == ["accountant@firm.example", "other@firm.example"]


# --- who can read the entries -----------------------------------------------------------------------------


def test_the_owner_and_the_accountants_read_the_entries_in_the_trail_and_staff_still_cannot(
    client, world, as_production_connection
):
    other = make_accountant("other@firm.example", world["acme"])
    with as_production_connection():
        sign_in(client, world["accountant"])
        client.get(f"/companies/{world['acme'].pk}/")
        client.post("/logout/")
        trail = f"/companies/{world['acme'].pk}/audit/"

        sign_in(client, world["acme_owner"])
        owner_page = client.get(trail).content.decode()
        client.post("/logout/")
        sign_in(client, other)
        accountant_page = client.get(trail).content.decode()
        client.post("/logout/")
        sign_in(client, world["acme_staff"])
        staff_response = client.get(trail)
        activity = client.get(f"/companies/{world['acme'].pk}/activity/").content.decode()

    for page in (owner_page, accountant_page):
        assert "Viewed" in page and "accountant@firm.example" in page
    assert staff_response.status_code == 403
    assert "accountant@firm.example" not in activity


def test_the_viewed_label_is_in_chinese_on_request(client, world, as_production_connection):
    with as_production_connection():
        sign_in(client, world["accountant"])
        client.get(f"/companies/{world['acme'].pk}/")
        page = client.get(f"/companies/{world['acme'].pk}/audit/", headers=CHINESE).content.decode()

    assert "已查看" in page


# --- the guard --------------------------------------------------------------------------------------------


def test_every_company_screen_is_audited_or_listed_as_showing_an_accountant_no_company_data():
    routes = list(company_routes())

    assert routes  # the walk found the company screens
    assert unaudited(routes) == []
    assert {route for route, _ in routes} >= set(NOT_AUDITED)  # the exemptions are for routes that exist


def test_every_audited_screen_takes_the_company_as_pk():
    """The decorator reads the company from the view's `pk` argument, so a screen that names it otherwise would fail."""
    routes = [(route, view) for route, view in company_routes() if getattr(view, "audited_read", False)]

    assert routes and all("<int:pk>" in route for route, _ in routes)


def test_the_guard_fails_for_a_screen_that_skips_the_mechanism():
    def skipped(request, pk):
        pass

    @audit.audited_read("thing")
    def covered(request, pk):
        pass

    assert unaudited([("things/<int:pk>/", skipped), ("covered/<int:pk>/", covered)]) == ["things/<int:pk>/"]


def test_a_screen_that_skips_the_mechanism_leaves_no_entry_and_the_decorated_one_does(
    client, world, settings, as_production_connection
):
    with as_production_connection():
        sign_in(client, world["accountant"])
        settings.ROOT_URLCONF = "core.tests.urls_audited_reads"
        client.get(f"/skipped/{world['acme'].pk}/")
        assert reads(world["acme"]) == []
        client.get(f"/covered/{world['acme'].pk}/")

    assert [entry.target_type for entry in reads(world["acme"])] == ["thing"]
