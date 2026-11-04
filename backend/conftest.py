from contextlib import contextmanager

import pytest
from django.db import connection

from core import ports
from core.rls import IDENTITY_SETTING, WEB_ROLE
from core.ports.fakes import FakePorts


@pytest.fixture(autouse=True)
def fake_ports():
    """Install fakes for the four external ports for every test."""
    fakes = FakePorts()
    ports.install(fakes)
    yield fakes
    ports.uninstall()


@pytest.fixture
def email_down(fake_ports):
    """The email port refuses every message, as when the mail server is down. A request that sends one answers 500."""

    def refuse(to, subject, body):
        raise RuntimeError("the mail server is down")

    fake_ports.email.send = refuse
    return fake_ports


@pytest.fixture
def as_user(db):
    """Run a block as the `web` database role with the given user as the signed-in identity.

    Independent of the production middleware on purpose: it sets the role and identity with plain SQL, so
    the policies can be tested without a request. Fixtures are created before entering the block, as the
    table owner, because the web role cannot write to the membership tables.
    """
    @contextmanager
    def switch(user):
        with connection.cursor() as cursor:
            cursor.execute(f"SET ROLE {WEB_ROLE}")
            cursor.execute("SELECT set_config(%s, %s, false)", [IDENTITY_SETTING, str(user.pk)])
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")
                cursor.execute("SELECT set_config(%s, '', false)", [IDENTITY_SETTING])

    return switch


@pytest.fixture
def as_production_connection(db):
    """Make the whole connection behave like production: a login role that is only a member of `web`.

    Unlike `as_user`, this changes who the connection is, not just the current role, so the session and
    authentication lookups that run before the tenant middleware are subject to the same privileges as
    everything else. Create fixtures first, then enter the block.
    """

    @contextmanager
    def switch():
        with connection.cursor() as cursor:
            cursor.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_test') THEN "
                f"CREATE ROLE app_test LOGIN NOSUPERUSER NOBYPASSRLS IN ROLE {WEB_ROLE}; END IF; END $$"
            )
            cursor.execute("SET SESSION AUTHORIZATION app_test")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")

    return switch


@pytest.fixture(autouse=True)
def base_url(settings):
    """Links in emails are built from BASE_URL, not from the request, so tests pin it to the test client's host."""
    settings.BASE_URL = "http://testserver"


@pytest.fixture
def world(db):
    """Acme (owner, staff), Beta (owner), and a firm accountant on both. Passwords are `factories.PASSWORD`."""
    from core.models import CompanyAccess
    from core.tests.factories import make_company, make_member

    acme, beta = make_company("Acme Sdn Bhd"), make_company("Beta Trading")
    accountant = make_member("accountant@firm.example", acme, CompanyAccess.ACCOUNTANT)
    CompanyAccess.objects.create(user=accountant, company=beta, role=CompanyAccess.ACCOUNTANT)
    accountant.is_accountant = True
    accountant.firm = acme.firm
    accountant.save()
    return {
        "acme": acme,
        "beta": beta,
        "acme_owner": make_member("owner@acme.example", acme, CompanyAccess.OWNER),
        "acme_staff": make_member("staff@acme.example", acme, CompanyAccess.STAFF),
        "beta_owner": make_member("owner@beta.example", beta, CompanyAccess.OWNER),
        "accountant": accountant,
    }


@pytest.fixture
def as_worker_connection(db):
    """Make the whole connection behave like the production worker process: a login role that is only a member of the worker role."""

    @contextmanager
    def switch():
        with connection.cursor() as cursor:
            cursor.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_worker_test') THEN "
                "CREATE ROLE app_worker_test LOGIN NOSUPERUSER NOBYPASSRLS IN ROLE worker; END IF; END $$"
            )
            cursor.execute("SET SESSION AUTHORIZATION app_worker_test")
        try:
            yield
        finally:
            with connection.cursor() as cursor:
                cursor.execute("RESET SESSION AUTHORIZATION")

    return switch


@pytest.fixture
def firms_world(db):
    """Two firms, each with an accountant and a company. Passwords are `factories.PASSWORD`."""
    from core import services
    from core.tests.factories import PASSWORD

    alpha, beta = services.create_firm("Alpha Accounting"), services.create_firm("Beta Books")
    people = {"alpha_accountant": services.create_accountant("acc@alpha.example", alpha)}
    people["beta_accountant"] = services.create_accountant("acc@beta.example", beta)
    for user in people.values():
        user.set_password(PASSWORD)
        user.save()
    acme = services.create_company_with_owner("Acme Sdn Bhd", "owner@acme.example", PASSWORD, alpha)
    gamma = services.create_company_with_owner("Gamma Holdings", "owner@gamma.example", PASSWORD, beta)
    return {"alpha": alpha, "beta": beta, "acme": acme, "gamma": gamma, **people}
