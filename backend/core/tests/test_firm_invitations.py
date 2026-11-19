"""A client owner invites a firm by email and a firm administrator accepts (ticket #144; D67 (3), D40 (5)).

Sending and accepting both run through SECURITY DEFINER functions (migration 0049): the service module is
exercised directly here, with `as_user` where the row-level security itself is under test, matching the style
of test_firm_applications.py.
"""

import re
from datetime import timedelta

import pytest
from django.db import DatabaseError, ProgrammingError, transaction
from django.utils import timezone

from core import firm_invitations
from core.models import AuditEntry, CompanyAccess, Firm, FirmAuditEntry, FirmInvitation

from .factories import make_company, make_member, make_user

OWNER_EMAIL = "owner@acme.example"
FIRM_EMAIL = "lim@limpartners.example"


@pytest.fixture
def acme(db):
    """A company with no firm yet, owned by owner@acme.example."""
    company = make_company("Acme Sdn Bhd", firm=False)
    make_member(OWNER_EMAIL, company, CompanyAccess.OWNER)
    return company


def invitation_link(fake_ports):
    return re.search(r"(/firm-invite/[\w-]+/)", fake_ports.email.sent[-1]["body"]).group(1)


def test_sending_stores_a_pending_invitation_and_emails_the_firm(acme, fake_ports, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user

    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)

    invitation = FirmInvitation.objects.get(company=acme)
    assert (invitation.email, invitation.status, invitation.created_by) == (FIRM_EMAIL, FirmInvitation.PENDING, owner)
    assert fake_ports.email.sent[-1]["to"] == FIRM_EMAIL
    assert "/firm-invite/" in fake_ports.email.sent[-1]["body"]


def test_sending_is_recorded_in_the_companys_trail(acme, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user

    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)

    entry = AuditEntry.objects.get(company=acme, target_type="core_firminvitation")
    assert (entry.action, entry.actor_id) == (AuditEntry.CREATE, owner.pk)


def test_a_company_that_already_has_a_firm_cannot_be_sent_an_invitation(fake_ports, as_user):
    company = make_company("Acme Sdn Bhd")  # has the default test firm
    owner = make_member(OWNER_EMAIL, company, CompanyAccess.OWNER)

    with as_user(owner), pytest.raises(firm_invitations.CompanyAlreadyHasFirm):
        firm_invitations.send(company, FIRM_EMAIL)

    assert FirmInvitation.objects.count() == 0
    assert fake_ports.email.sent == []


def test_a_second_invitation_while_one_is_pending_is_refused(acme, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user

    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
        with pytest.raises(firm_invitations.InvitationAlreadyPending):
            firm_invitations.send(acme, "other@otherfirm.example")

    assert FirmInvitation.objects.count() == 1


def test_an_invitation_can_be_sent_again_once_the_pending_one_has_expired(acme, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    stale = FirmInvitation.objects.get(company=acme)
    stale.expires_at = timezone.now() - timedelta(days=1)
    stale.save(update_fields=["expires_at"])

    with as_user(owner):
        firm_invitations.send(acme, "other@otherfirm.example")

    stale.refresh_from_db()
    assert stale.status == FirmInvitation.EXPIRED
    assert FirmInvitation.objects.filter(company=acme, status=FirmInvitation.PENDING).count() == 1


# --- accepting --------------------------------------------------------------------------------------------------


def make_firm_admin(email, firm_name="Lim & Partners"):
    firm = Firm.objects.create(name=firm_name)
    admin = make_user(email)
    admin.is_firm_admin = True
    admin.firm = firm
    admin.save()
    return admin


def test_a_firm_administrator_can_accept_and_the_company_gets_that_firm_with_no_companyaccess_granted(
    acme, fake_ports, as_user
):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    link = invitation_link(fake_ports)
    admin = make_firm_admin(FIRM_EMAIL)

    with as_user(admin):
        company_id = firm_invitations.accept(link.split("/")[-2])

    acme.refresh_from_db()
    assert company_id == acme.pk and acme.firm == admin.firm
    invitation = FirmInvitation.objects.get(company=acme)
    assert (invitation.status, invitation.accepted_by, invitation.accepted_firm) == (
        FirmInvitation.ACCEPTED,
        admin,
        admin.firm,
    )
    assert not CompanyAccess.objects.filter(company=acme, user=admin).exists()


def test_accepting_is_recorded_in_the_companys_trail_and_the_firms_trail(acme, fake_ports, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    link = invitation_link(fake_ports)
    admin = make_firm_admin(FIRM_EMAIL)

    with as_user(admin):
        firm_invitations.accept(link.split("/")[-2])

    company_entry = AuditEntry.objects.get(company=acme, target_type="company")
    assert (company_entry.action, company_entry.actor_id, company_entry.changed_fields) == (
        AuditEntry.UPDATE,
        admin.pk,
        ["firm"],
    )
    firm_entry = FirmAuditEntry.objects.get(firm=admin.firm, target_type="company")
    assert (firm_entry.action, firm_entry.actor_id, firm_entry.changed_fields) == (AuditEntry.UPDATE, admin.pk, ["firm"])


def test_a_used_link_is_refused_the_second_time(acme, fake_ports, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    link = invitation_link(fake_ports)
    admin = make_firm_admin(FIRM_EMAIL)

    with as_user(admin):
        firm_invitations.accept(link.split("/")[-2])
        with pytest.raises(firm_invitations.InvalidLink):
            firm_invitations.accept(link.split("/")[-2])


def test_an_expired_link_is_refused(acme, fake_ports, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    invitation = FirmInvitation.objects.get(company=acme)
    invitation.expires_at = timezone.now() - timedelta(days=1)
    invitation.save(update_fields=["expires_at"])
    link = invitation_link(fake_ports)
    admin = make_firm_admin(FIRM_EMAIL)

    with as_user(admin), pytest.raises(firm_invitations.InvalidLink):
        firm_invitations.accept(link.split("/")[-2])

    acme.refresh_from_db()
    assert acme.firm is None
    invitation.refresh_from_db()
    assert invitation.status == FirmInvitation.PENDING  # swept to "expired" on the next send, not on a failed accept


def test_a_tampered_token_is_refused(acme, fake_ports, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    admin = make_firm_admin(FIRM_EMAIL)

    with as_user(admin), pytest.raises(firm_invitations.InvalidLink):
        firm_invitations.accept("not-the-real-token")


# --- row-level security: one company's invitation is invisible and unwritable to another ------------------------


def test_an_invitation_is_invisible_to_another_companys_owner(acme, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    other_company = make_company("Beta Trading", firm=False)
    other_owner = make_member("owner@beta.example", other_company, CompanyAccess.OWNER)

    with as_user(other_owner):
        visible = list(FirmInvitation.objects.all())

    assert visible == []


def test_a_signed_in_user_cannot_insert_an_invitation_directly(acme, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user

    with as_user(owner):
        with pytest.raises(ProgrammingError), transaction.atomic():
            FirmInvitation.objects.create(
                company=acme, email=FIRM_EMAIL, token_hash="x" * 64, created_by=owner, expires_at=timezone.now()
            )


def test_a_non_owner_company_member_cannot_send_an_invitation_even_through_the_service(acme, as_user):
    staff = make_member("staff@acme.example", acme, CompanyAccess.STAFF)

    with as_user(staff), pytest.raises(DatabaseError):
        firm_invitations.send(acme, FIRM_EMAIL)

    assert FirmInvitation.objects.count() == 0


def test_a_person_with_no_firm_account_cannot_accept(acme, fake_ports, as_user):
    owner = acme.access.get(role=CompanyAccess.OWNER).user
    with as_user(owner):
        firm_invitations.send(acme, FIRM_EMAIL)
    link = invitation_link(fake_ports)
    nobody_special = make_user(FIRM_EMAIL)

    with as_user(nobody_special), pytest.raises(firm_invitations.NotFirmAdministrator):
        firm_invitations.accept(link.split("/")[-2])

    acme.refresh_from_db()
    assert acme.firm is None
