from pathlib import Path
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ImproperlyConfigured
import os
import sys
from dotenv import load_dotenv
from .ui_config import DEFAULT_THEME_COLOR


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env')

DB_MODE = os.environ.get('LAKRISH_DB_MODE', 'prod').strip().lower()
if DB_MODE not in {'prod', 'demo'}:
    DB_MODE = 'prod'
ENV_LABEL = 'DEMO' if DB_MODE == 'demo' else 'PROD'
SHOW_ENV_RIBBON = os.environ.get('SHOW_ENV_RIBBON', 'False') == 'True'
PROD_BASE_URL = os.environ.get('PROD_BASE_URL', 'http://127.0.0.1:8888')
DEMO_BASE_URL = os.environ.get('DEMO_BASE_URL', 'http://127.0.0.1:8001')
SHOW_RUNBOOK_URL_MAP = os.environ.get('SHOW_RUNBOOK_URL_MAP', 'False') == 'True'
ALLOW_DEMO_LOGIN = _env_bool('ALLOW_DEMO_LOGIN', False)

THEME_COLOR = DEFAULT_THEME_COLOR

DEBUG = _env_bool('DEBUG', False)
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'dev-insecure-change-me'
    else:
        raise ImproperlyConfigured('SECRET_KEY must be set when DEBUG=False')

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'rest_framework',
    'rest_framework.authtoken',
    'axes',
    'accounts',
    'suppliers',
    'products',
    'cart',
    'orders',
    'payments',
    'admin_panel',
    'delivery',
    'distributors',
    'support',
    'stockist',
    'transactions',
    'rentals',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'admin_panel.middleware.RequestLoggingMiddleware',
    'admin_panel.middleware.DemoTransactionGuardMiddleware',
    'axes.middleware.AxesMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'lakrish_mart.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'lakrish_mart.context_processors.ui_config',
                'cart.context_processors.cart_count',
                'accounts.context_processors.active_role',
                'admin_panel.context_processors.site_branding',
            ],
        },
    },
]

WSGI_APPLICATION = 'lakrish_mart.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / ('db_demo.sqlite3' if DB_MODE == 'demo' else 'db.sqlite3'),
    }
}

# Support DATABASE_URL for cloud deployments (Heroku, AWS RDS, etc.)
# pip install dj-database-url to use: DATABASE_URL=postgres://user:pass@host:5432/dbname
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL and DB_MODE != 'demo':
    try:
        import dj_database_url
        DATABASES['default'] = dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    except ImportError:
        pass

# Keep browser sessions isolated when prod and demo run in parallel.
if DB_MODE == 'demo':
    SESSION_COOKIE_NAME = 'lakrish_demo_sessionid'
    CSRF_COOKIE_NAME = 'lakrish_demo_csrftoken'

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en'
TIME_ZONE = 'Asia/Kolkata'
# i18n is intentionally disabled — translation strings use _(…) for future readiness
# but runtime translation lookup is off. Enable USE_I18N=True when translations are ready.
USE_I18N = False
USE_L10N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTHENTICATION_BACKENDS = [
    'accounts.backends.CaseInsensitiveBackend',
    'axes.backends.AxesStandaloneBackend',
]

# django-axes: rate limit login (5 attempts, 15 min lockout)
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.25   # 15 minutes in hours
AXES_LOCKOUT_PARAMETERS = ['ip_address', 'username']
AXES_RESET_ON_SUCCESS = True

# Pagination
PRODUCTS_PER_PAGE = 12
ORDERS_PER_PAGE = 20

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ]
}


# ── Logging ───────────────────────────────────────────────────────────────────
# A FRESH request-log file is created for every server start, named with the
# start timestamp (e.g. requests_2026-10-30_15-20-05.log). This keeps each run's
# activity in its own file instead of one ever-growing log.
#
# The dev auto-reloader starts a child process, so settings load twice. We pick
# the timestamp ONCE (parent) and pass it to the child via an env var, so both
# processes write to the SAME file for a given start (no duplicate files).
import datetime as _dt

(BASE_DIR / 'logs').mkdir(exist_ok=True)
# A NEW timestamped request-log file is created only for an actual running
# server — not for one-off commands (migrate, shell, test, check...), which
# would otherwise litter logs/ with empty files.
#
#   * dev runserver: the auto-reloader runs a watcher parent + a worker child;
#     only the worker sets RUN_MAIN=true and only it serves requests, so only
#     the worker owns a fresh file (parent writes nothing).
#   * production (gunicorn/wsgi, DEBUG=False): always a serving process.
#
# Everything else logs to a single throwaway file that never accumulates.
_argv = ' '.join(sys.argv)
_is_runserver = 'runserver' in _argv
_is_prod_serve = (not _env_bool('DEBUG', False)) and ('manage.py' not in _argv)
_is_server_worker = (_is_runserver and os.environ.get('RUN_MAIN') == 'true') or _is_prod_serve

_run_stamp = _dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
if _is_server_worker:
    REQUEST_LOG_FILE = str(BASE_DIR / 'logs' / f'requests_{_run_stamp}.log')
    # Auto-rotate: keep only the 7 most recent per-run request logs so the
    # logs/ folder never fills up. Runs once at server-worker startup.
    try:
        import glob as _glob
        _existing = sorted(_glob.glob(str(BASE_DIR / 'logs' / 'requests_*.log')))
        _keep = 7
        for _old in _existing[:-_keep] if len(_existing) >= _keep else []:
            try:
                os.remove(_old)
            except OSError:
                pass
    except Exception:
        pass
else:
    # Non-serving process (watcher, or any manage.py command). One fixed file
    # that is reused/overwritten, so it never piles up.
    REQUEST_LOG_FILE = str(BASE_DIR / 'logs' / '_non_server.log')

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] {levelname} {name} {message}',
            'style': '{',
        },
        'plain': {
            'format': '{message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'security_file': {
            'class': 'logging.FileHandler',
            'filename': str(BASE_DIR / 'logs' / 'security.log'),
            'formatter': 'verbose',
        },
        # One fresh file per server start (timestamped). One line per HTTP
        # request: who / url / status / time.
        'requests_file': {
            'class': 'logging.FileHandler',
            'filename': REQUEST_LOG_FILE,
            'encoding': 'utf-8',
            'formatter': 'plain',
        },
        # Activity log: every meaningful business event (order status changes,
        # payments, registrations, product actions, stock changes, errors).
        # Single rotating file — max 10 MB, keeps 5 backups.
        'activity_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(BASE_DIR / 'logs' / 'activity.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 5,
            'encoding': 'utf-8',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'security': {
            'handlers': ['console', 'security_file'],
            'level': 'WARNING',
            'propagate': False,
        },
        'requests': {
            'handlers': ['requests_file'],
            'level': 'INFO',
            'propagate': False,
        },
        # Business-event logger — imported as logging.getLogger('activity')
        # in views / signals. Captures every significant state change.
        'activity': {
            'handlers': ['activity_file', 'console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'WARNING'),
        },
        # Capture all Django errors to the activity log as well.
        'django.request': {
            'handlers': ['activity_file'],
            'level': 'ERROR',
            'propagate': True,
        },
    },
}
# Ensure logs directory exists
(BASE_DIR / 'logs').mkdir(exist_ok=True)

# ── Security settings (applied in production when DEBUG=False) ────────────────
if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Strict'
    CSRF_COOKIE_SAMESITE = 'Strict'
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # Use whitenoise for static files in production (no separate web server needed)
    MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'


# ── Test-run tweaks ───────────────────────────────────────────────────────────
# PBKDF2 is deliberately slow, which is right for real passwords and wrong for a
# suite that creates dozens of accounts per test. Swapped only while running
# tests — never in dev or production.
if 'test' in sys.argv:
    PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
