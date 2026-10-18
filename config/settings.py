"""
Django settings for PUCIT Class CMS.
Hardened for production deployment while preserving local development workflows.
"""

import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------------------
# Environment Variables & .env Loader
# ------------------------------------------------------------------------------
def load_env_file(env_path: Path):
    """Safely loads simple KEY=VALUE pairs from a .env file into os.environ if not already set."""
    if not env_path.is_file():
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass


load_env_file(BASE_DIR / ".env")


def get_bool_env(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes", "on")


# ------------------------------------------------------------------------------
# Core Security Settings: DEBUG, SECRET_KEY, ALLOWED_HOSTS
# ------------------------------------------------------------------------------
# DEBUG defaults to True for local development, must be set to False in production
DEBUG = get_bool_env("DJANGO_DEBUG", default=True)

# SECRET_KEY resolution: strictly required in both development and production
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY environment variable is missing. "
        "Please configure DJANGO_SECRET_KEY in your environment or local .env file."
    )

if not DEBUG:
    if SECRET_KEY.startswith("django-insecure-") or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is too weak for production use. It must be at least 50 characters "
            "and not use the 'django-insecure-' prefix."
        )

# ALLOWED_HOSTS configuration
raw_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "")
if raw_hosts:
    ALLOWED_HOSTS = [h.strip() for h in raw_hosts.split(",") if h.strip()]
    if "*" in ALLOWED_HOSTS and not DEBUG:
        raise ImproperlyConfigured("Wildcard '*' in ALLOWED_HOSTS is forbidden in production.")
else:
    if DEBUG:
        ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
    else:
        ALLOWED_HOSTS = []

# CSRF Trusted Origins
raw_origins = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "")
if raw_origins:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in raw_origins.split(",") if o.strip()]
else:
    CSRF_TRUSTED_ORIGINS = []


# ------------------------------------------------------------------------------
# Production HTTPS & Security Headers
# ------------------------------------------------------------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = get_bool_env("DJANGO_SECURE_SSL_REDIRECT", default=False)
    SESSION_COOKIE_SECURE = get_bool_env("DJANGO_SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = get_bool_env("DJANGO_CSRF_COOKIE_SECURE", default=True)

    hsts_seconds = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "0"))
    if hsts_seconds > 0:
        SECURE_HSTS_SECONDS = hsts_seconds
        SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool_env(
            "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False
        )
        SECURE_HSTS_PRELOAD = get_bool_env("DJANGO_SECURE_HSTS_PRELOAD", default=False)

    if get_bool_env("DJANGO_USE_X_FORWARDED_PROTO", default=False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    SECURE_SSL_REDIRECT = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False
    SECURE_HSTS_SECONDS = 0

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True


# ------------------------------------------------------------------------------
# Application definition
# ------------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "accounts",
    "academics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "accounts.middleware.UserActivityMiddleware",
    "accounts.middleware.RequirePasswordChangeMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "academics.context_processors.notification_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# ------------------------------------------------------------------------------
# Database
# ------------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ------------------------------------------------------------------------------
# Cache Configuration (Used for reset code rate limiting)
# ------------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "class-cms-cache",
    }
}


# ------------------------------------------------------------------------------
# Password validation
# ------------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# ------------------------------------------------------------------------------
# Internationalization
# ------------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# ------------------------------------------------------------------------------
# Static and Media Files
# ------------------------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"


# ------------------------------------------------------------------------------
# Authentication Backends & Login Configuration
# ------------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "accounts.backends.RollNumberOrEmailBackend",
]

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"


# ------------------------------------------------------------------------------
# Email Configuration
# ------------------------------------------------------------------------------
EMAIL_BACKEND = os.environ.get(
    "DJANGO_EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend",
)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True").lower() in ("true", "1", "yes")
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "False").lower() in ("true", "1", "yes")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@pucit.edu.pk")
