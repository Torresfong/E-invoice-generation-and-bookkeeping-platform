"""Operations that create accounts, firms and companies outside a web request: operator commands, onboarding, tests.

They run with the database owner's rights and are not reachable from the web role. Firms and firm accountants are
created only here, never by a client (D48, D49).
"""

from django.contrib.auth import get_user_model
from django.db import transaction

from . import audit
from .models import AuditEntry, Company, CompanyAccess, Firm


class EmailInUse(Exception):
    """The address already has an account. One login holds one kind of membership for now (D40 (1))."""


class FirmNotFound(Exception):
    pass


class CompanyNotFound(Exception):
    pass


def _require_new_address(email):
    if get_user_model().objects.filter(email__iexact=email.strip()).exists():
        raise EmailInUse


def _accountants(firm):
    return get_user_model().objects.filter(is_accountant=True, firm=firm)


def _open_to_accountants(company):
    """Give each accountant of the company's firm a membership in it. Others get none."""
    CompanyAccess.objects.bulk_create(
        CompanyAccess(user=accountant, company=company, role=CompanyAccess.ACCOUNTANT)
        for accountant in _accountants(company.firm)
    )


def _record_firm(company, action):
    audit.record_operator_event(action, company, "company", company.pk, changed_fields=["firm"])


def create_firm(name):
    """An accounting firm. Names are unique, ignoring case."""
    return Firm.objects.create(name=name.strip())


def get_firm(name):
    try:
        return Firm.objects.get(name__iexact=name.strip())
    except Firm.DoesNotExist as error:
        raise FirmNotFound from error


def get_company(pk):
    try:
        return Company.objects.get(pk=pk)
    except Company.DoesNotExist as error:
        raise CompanyNotFound from error


@transaction.atomic
def create_company_with_owner(name, owner_email, owner_password, firm):
    """A company of `firm` with its single owner. Only that firm's accountants get access to it."""
    _require_new_address(owner_email)
    company = Company.objects.create(name=name, firm=firm)
    owner = get_user_model().objects.create_user(owner_email, password=owner_password)
    CompanyAccess.objects.create(user=owner, company=company, role=CompanyAccess.OWNER)
    _open_to_accountants(company)
    _record_firm(company, AuditEntry.CREATE)
    return company


@transaction.atomic
def create_accountant(email, firm):
    """An accountant of `firm`, with access to that firm's companies only and no usable password until they choose one."""
    _require_new_address(email)
    accountant = get_user_model().objects.create_user(email, password=None, is_accountant=True, firm=firm)
    CompanyAccess.objects.bulk_create(
        CompanyAccess(user=accountant, company=company, role=CompanyAccess.ACCOUNTANT) for company in firm.companies.all()
    )
    return accountant


@transaction.atomic
def move_company(company, firm):
    """Move a company to another firm: the old firm's accountants lose access, the new firm's gain it.

    Owners and staff are untouched. Does nothing, and records nothing, if the company is already in that firm.
    """
    if company.firm_id == firm.pk:
        return company
    CompanyAccess.objects.filter(company=company, role=CompanyAccess.ACCOUNTANT).delete()
    company.firm = firm
    company.save(update_fields=["firm"])
    _open_to_accountants(company)
    _record_firm(company, AuditEntry.UPDATE)
    return company
