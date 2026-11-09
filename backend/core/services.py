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


class FirmNameRequired(ValueError):
    pass


class CompanyNotFound(Exception):
    pass


class CompanyChanged(Exception):
    """Another operator command moved the company while this move waited for its turn; look again and retry."""


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


def _lock_firms(*firms):
    """Lock the present firms' rows, in a fixed order, and return the present ones in the order given.

    Creating a company or an accountant and moving a company all read one firm's people or companies and then write
    access rows; locking the firm makes them take turns, so a move cannot leave a stale row for the old firm's
    accountant behind.
    """
    firm_ids = [firm.pk for firm in firms if firm is not None]
    locked = {
        firm.pk: firm for firm in Firm.objects.select_for_update().filter(pk__in=firm_ids).order_by("pk")
    }
    return [locked[firm.pk] for firm in firms if firm is not None]


def _record_firm_event(firm, action, target_type, target_id, changed_fields):
    audit.record_firm_event(action, firm, target_type=target_type, target_id=target_id, changed_fields=changed_fields)


@transaction.atomic
def create_firm(name):
    """An accounting firm. Names are unique, ignoring case."""
    if not name.strip():
        raise FirmNameRequired
    firm = Firm.objects.create(name=name.strip())
    _record_firm_event(firm, AuditEntry.CREATE, "firm", firm.pk, ["name"])
    return firm


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
def create_firm_administrator(email, firm):
    """A firm administrator of `firm`, with no usable password until they choose one (D49). They get no company access."""
    _require_new_address(email)
    [firm] = _lock_firms(firm)
    administrator = get_user_model().objects.create_user(email, password=None, is_firm_admin=True, firm=firm)
    _record_firm_event(firm, AuditEntry.CREATE, "user", administrator.pk, ["email", "firm"])
    return administrator


@transaction.atomic
def create_firm_with_administrator(name, administrator_email):
    """A firm and its first firm administrator."""
    _require_new_address(administrator_email)
    return create_firm_administrator(administrator_email, create_firm(name))


@transaction.atomic
def create_company_with_owner(name, owner_email, owner_password, firm=None):
    """A company with its single owner. When linked, only that firm's accountants get access to it."""
    _require_new_address(owner_email)
    if firm is not None:
        [firm] = _lock_firms(firm)
    company = Company.objects.create(name=name, firm=firm)
    owner = get_user_model().objects.create_user(owner_email, password=owner_password)
    CompanyAccess.objects.create(user=owner, company=company, role=CompanyAccess.OWNER)
    if firm is not None:
        _open_to_accountants(company)
    _record_firm(company, AuditEntry.CREATE)
    return company


@transaction.atomic
def create_accountant(email, firm):
    """An accountant of `firm`, with access to that firm's companies only and no usable password until they choose one."""
    _require_new_address(email)
    [firm] = _lock_firms(firm)
    accountant = get_user_model().objects.create_user(email, password=None, is_accountant=True, firm=firm)
    CompanyAccess.objects.bulk_create(
        CompanyAccess(user=accountant, company=company, role=CompanyAccess.ACCOUNTANT) for company in firm.companies.all()
    )
    _record_firm_event(firm, AuditEntry.CREATE, "user", accountant.pk, ["email", "firm"])
    return accountant


@transaction.atomic
def move_company(company, firm):
    """Change a company's firm: the old firm's accountants lose access and the new firm's gain it.

    `firm` may be `None` to end the link. Owners and staff are untouched. Does nothing, and records nothing, if the
    company already has that firm (or already has no firm).

    Locks are taken in one order everywhere: the firms first (by id), then the company, then, when the transaction
    commits, the rows the access check reads. `create_accountant` and `create_company_with_owner` take the same
    order, so none of them can wait for another that waits for it.
    """
    current = Company.objects.select_related("firm").get(pk=company.pk)  # not locked: only to learn which firms to lock
    _lock_firms(current.firm, firm)
    company = Company.objects.select_for_update().get(pk=company.pk)
    if company.firm_id != current.firm_id:
        raise CompanyChanged
    if company.firm_id == (firm.pk if firm is not None else None):
        return company
    CompanyAccess.objects.filter(company=company, role=CompanyAccess.ACCOUNTANT).delete()
    company.firm = firm
    company.save(update_fields=["firm"])
    if firm is not None:
        _open_to_accountants(company)
    # The company's trail and every firm side that exists, tied together by one correlation id (D50 (5), D67 (5)).
    with audit.correlated():
        _record_firm(company, AuditEntry.UPDATE)
        for side in (current.firm, firm):
            if side is None:
                continue
            _record_firm_event(side, AuditEntry.UPDATE, "company", company.pk, ["firm"])
    return company
