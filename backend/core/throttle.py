"""Failed-sign-in throttling (D40 (5)). Counts live in the database so every worker sees the same numbers.

Only a hash of the email address is stored, worked out in the database. The web role reaches the table through SECURITY DEFINER
functions, because nobody is identified yet when these checks run.

Each attempt is counted before the password is checked, and a successful sign-in clears the count, so
parallel requests cannot all pass the check before any of them is counted. Limits are per address: a limit
per client address would need the deployment's proxy settings to be trustworthy, and is left for go-live.
"""

from datetime import timedelta

from django.conf import settings

from .db import scalar


def allow_attempt(email):
    """Count a sign-in attempt for this address. False when the address is locked and the attempt must be refused."""
    window = timedelta(seconds=settings.LOGIN_FAILURE_WINDOW_SECONDS)
    return scalar("SELECT start_login_attempt(%s, %s, %s)", [email, window, settings.LOGIN_MAX_FAILURES])


def allow_signup(email, client, kind="signup"):
    """Count a sign-up submission against its email address and its client address (D48). False when either is over its limit.

    Both are counted every time, in the same table as sign-in failures under a prefix that no email address can
    carry, so one refused limit does not hide the other. `client` is the connection's address: behind a proxy it is the
    proxy's until the deployment's proxy settings are in place (see above). `kind` keeps another kind of submission,
    such as a firm's application, from using up owner sign-up's counts.
    """
    window = timedelta(seconds=settings.SIGNUP_WINDOW_SECONDS)
    by_email = scalar("SELECT start_login_attempt(%s, %s, %s)", [f"{kind}-email:{email}", window, settings.SIGNUP_MAX_PER_EMAIL])
    by_client = scalar("SELECT start_login_attempt(%s, %s, %s)", [f"{kind}-client:{client}", window, settings.SIGNUP_MAX_PER_CLIENT])
    return by_email and by_client


def clear(email):
    scalar("SELECT clear_login_failures(%s)", [email])
