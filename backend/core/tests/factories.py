from django.contrib.auth import get_user_model

from core.models import Company, CompanyAccess, Firm

PASSWORD = "correct horse battery"


def make_user(email, password=PASSWORD):
    return get_user_model().objects.create_user(email, password=password)


def make_member(email, company, role, password=PASSWORD):
    """A new user with a membership row in `company`."""
    user = make_user(email, password)
    CompanyAccess.objects.create(user=user, company=company, role=role)
    return user


def default_firm():
    """The firm that tests use unless they say otherwise."""
    return Firm.objects.get_or_create(name="Test Firm")[0]


def make_accountant(email, *companies, password=PASSWORD):
    """A firm accountant with a membership in each of `companies`, in the firm of the first (or the default firm)."""
    user = make_user(email, password)
    user.is_accountant = True
    user.firm = companies[0].firm if companies else default_firm()
    user.save()
    for company in companies:
        CompanyAccess.objects.create(user=user, company=company, role=CompanyAccess.ACCOUNTANT)
    return user


def make_company(name="Acme Sdn Bhd", firm=None):
    return Company.objects.create(name=name, firm=firm or default_firm())
