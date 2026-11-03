import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")
DEBUG = ENVIRONMENT == "development"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY and not DEBUG:
    raise RuntimeError("SECRET_KEY must be set outside development")
SECRET_KEY = SECRET_KEY or "dev-only-insecure-key"

ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if h]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.TenantContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Postgres is required (row-level security comes later); there is deliberately no default.
DATABASES = {"default": dj_database_url.config(env="DATABASE_URL", conn_max_age=60)}

LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("zh-hans", "Chinese")]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
USE_TZ = True
TIME_ZONE = "Asia/Kuala_Lumpur"

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = ["core.auth.EmailBackend"]

# Five failed sign-ins for one email address lock sign-in for that address for fifteen minutes (D40 (5)).
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60

# Where links in emails sent outside a request (operator commands) point.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

# Password reset links last an hour, invitations a week (D40 (5)).
RESET_LINK_HOURS = 1
# The link a new firm administrator or accountant gets to choose a first password (D49).
SETUP_LINK_HOURS = 24
INVITE_LINK_DAYS = 7
# Owner self-registration (D48, D49). A firm's sign-up link lasts a week, the emailed verification link a day.
SIGNUP_LINK_DAYS = 7
REGISTRATION_LINK_HOURS = 24
# Sign-up submissions allowed per email address and per client address in the window below.
SIGNUP_MAX_PER_EMAIL = 3
SIGNUP_MAX_PER_CLIENT = 10
SIGNUP_WINDOW_SECONDS = 60 * 60
# Off unless switched on, and refused outside development until two-factor authentication is done (D40 (7)). Remove the
# refusal in the change that finishes 2FA, not before.
SIGNUP_ENABLED = os.environ.get("SIGNUP_ENABLED", "").lower() == "true"
if SIGNUP_ENABLED and not DEBUG:
    raise RuntimeError("SIGNUP_ENABLED cannot be on outside development until two-factor authentication is done")

SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
