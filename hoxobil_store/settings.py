from pathlib import Path
from decouple import config
import os
import dj_database_url
from decimal import Decimal
import datetime
from zoneinfo import ZoneInfo   # stdlib on Python 3.9+, no extra install needed
 
 

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────
#  CORE SETTINGS
# ─────────────────────────────────────────────────────────
SECRET_KEY = config('SECRET_KEY', default='django-insecure-18cqg*t%&c=g(te(-z6n=qr*(*-4d+3ig6&pb*f+#0@71otk^j')
DEBUG = config('DEBUG', default=False, cast=bool)

RENDER_EXTERNAL_HOSTNAME = config('RENDER_EXTERNAL_HOSTNAME', default='')

ALLOWED_HOSTS = [
    '127.0.0.1',
    'localhost',
    '.ngrok-free.dev',
    '.ngrok.io',
    '.loca.lt',
    '.pinggy.link',
    '.trycloudflare.com',
    '.onrender.com',
]

if RENDER_EXTERNAL_HOSTNAME:
    ALLOWED_HOSTS.append(RENDER_EXTERNAL_HOSTNAME)

CSRF_TRUSTED_ORIGINS = [
    'https://*.loca.lt',
    'https://*.ngrok-free.dev',
    'https://*.pinggy.link',
    'https://*.trycloudflare.com',
    'https://*.onrender.com',
]


# ─────────────────────────────────────────────────────────
#  INSTALLED APPS
# ─────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party Security Engine
    'corsheaders',

    # Custom Apps
    'shop',

    # Third-party Apps
    'django_filters',
    'djmoney',
    'djmoney.contrib.exchange',
    'widget_tweaks',
]


# ─────────────────────────────────────────────────────────
#  MIDDLEWARE
# ─────────────────────────────────────────────────────────
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'shop.launch_middleware.LaunchGateMiddleware',   # ← ADD THIS LINE
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


# ─────────────────────────────────────────────────────────
#  URLS & WSGI
# ─────────────────────────────────────────────────────────
ROOT_URLCONF = 'hoxobil_store.urls'
WSGI_APPLICATION = 'hoxobil_store.wsgi.application'


# ─────────────────────────────────────────────────────────
#  TEMPLATES
# ─────────────────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'shop' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'shop.context_processors.currency_selector',
                'shop.context_processors.payment_keys',
            ],
            'builtins': [
                'djmoney.templatetags.djmoney',
            ],
        },
    },
]


# ─────────────────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────────────────
# Uses PostgreSQL when DATABASE_URL is set (Render / production).
# Falls back to local SQLite when DATABASE_URL is not set (local dev).
DATABASE_URL = config('DATABASE_URL', default='')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }


# ─────────────────────────────────────────────────────────
#  PASSWORD VALIDATION
# ─────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ─────────────────────────────────────────────────────────
#  INTERNATIONALISATION
# ─────────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ─────────────────────────────────────────────────────────
#  STATIC & MEDIA FILES
# ─────────────────────────────────────────────────────────
STATIC_URL = 'static/'
STATICFILES_DIRS = [
    BASE_DIR / 'shop' / 'static',
]
STATIC_ROOT = BASE_DIR / 'staticfiles'

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# ─────────────────────────────────────────────────────────
#  CORS CORS SECURITY SETTINGS
# ─────────────────────────────────────────────────────────
CORS_ALLOW_ALL_ORIGINS = True  # Allows your HTML5 Canvas context to process pixel assets over localtunnels
CORS_ALLOW_CREDENTIALS = True


# ─────────────────────────────────────────────────────────
#  USER ACCOUNT SETTINGS
# ─────────────────────────────────────────────────────────
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'


# ─────────────────────────────────────────────────────────
#  EMAIL CONFIGURATION
# ─────────────────────────────────────────────────────────
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='help.hoxobil@gmail.com')


# ─────────────────────────────────────────────────────────
#  CURRENCY SETTINGS
# ─────────────────────────────────────────────────────────
DEFAULT_CURRENCY = 'USD'
CURRENCIES = ('USD', 'EUR', 'GBP', 'NGN', 'CAD', 'AUD', 'JPY')

CASH_EXCHANGE_BACKEND = {
    'USD': {
        'USD': 1.0,
        'EUR': 0.95,
        'GBP': 0.76,
        'CAD': 1.41,
        'AUD': 1.54,
        'JPY': 155.00,
        'NGN': 1650.0,
    }
}

FIXER_ACCESS_KEY = config('FIXER_ACCESS_KEY', default='')


# ─────────────────────────────────────────────────────────
#  FLUTTERWAVE PAYMENT
# ─────────────────────────────────────────────────────────
FLUTTERWAVE_PUBLIC_KEY = config('FLUTTERWAVE_PUBLIC_KEY', default='')
FLUTTERWAVE_SECRET_KEY = config('FLUTTERWAVE_SECRET_KEY', default='')


# ─────────────────────────────────────────────────────────
#  PAYSTACK PAYMENT
# ─────────────────────────────────────────────────────────
PAYSTACK_PUBLIC_KEY = config('PAYSTACK_PUBLIC_KEY', default='')
PAYSTACK_SECRET_KEY = config('PAYSTACK_SECRET_KEY', default='')


# ─────────────────────────────────────────────────────────
#  PRINT-ON-DEMAND (POD)
# ─────────────────────────────────────────────────────────
PRINTIFY_BASE_URL = 'https://api.printify.com/v1/'
PRINTIFY_ACCESS_TOKEN = config('PRINTIFY_ACCESS_TOKEN', default='')
PRINTIFY_SHOP_ID = config('PRINTIFY_SHOP_ID', default='')

PRINTFUL_BASE_URL = 'https://api.printful.com/'
PRINTFUL_ACCESS_TOKEN = config('PRINTFUL_ACCESS_TOKEN', default='')
PRINTFUL_STORE_ID = config('PRINTFUL_STORE_ID', default='')

# Public-facing base URL for this server (no trailing slash).
# Printful's servers must be able to fetch your uploaded design images,
# so this MUST be a publicly reachable HTTPS URL — NOT http://127.0.0.1:8000.
#
# ── Development ──────────────────────────────────────────────────────────────
# Start a tunnel, then paste the URL here in your .env file each session:
#
#   ngrok:   ngrok http 8000
#            → PUBLIC_BASE_URL=https://abc123.ngrok-free.app
#
#   pinggy:  ssh -p 443 -R0:localhost:8000 a.pinggy.io
#            → PUBLIC_BASE_URL=https://your-pinggy-subdomain.a.pinggy.link
#
#   loca.lt: npx localtunnel --port 8000
#            → PUBLIC_BASE_URL=https://your-subdomain.loca.lt
#
# ── Production ───────────────────────────────────────────────────────────────
#   PUBLIC_BASE_URL=https://www.hoxobil.com  (or your onrender.com URL)
#
# Leave empty to fall back to request.build_absolute_uri() (safe in production,
# broken for local dev with Printful mockup generation).
PUBLIC_BASE_URL = config('PUBLIC_BASE_URL', default='')


# ─────────────────────────────────────────────────────────
#  AI & CHATBOT SETTINGS
# ─────────────────────────────────────────────────────────
GEMINI_API_KEY = config('GEMINI_API_KEY', default='')
SERPER_API_KEY = config('SERPER_API_KEY', default='')

CHATBOT_MAX_TOKENS = 300       
CHATBOT_USE_WEB_SEARCH = True   


# ─────────────────────────────────────────────────────────
#  DEEP DEBUG LOGGING CONFIGURATION
# ─────────────────────────────────────────────────────────
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[%(asctime)s] %(levelname)s [%(name)s:%(lineno)s] %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S'
        },
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': True,
        },
        'shop': {  
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

LAUNCH_DATE = datetime.datetime(2026, 9, 5, 0, 0, 0, tzinfo=ZoneInfo("Africa/Lagos"))
 
DONATION_GOAL_NGN = Decimal('500000.00')
 
# Path *prefixes* always reachable pre-launch (on top of the launch page
# itself and the donation endpoints, which the middleware allows by name).
LAUNCH_ALLOWED_PATH_PREFIXES = [
    '/admin',
    '/static',
    '/media',
]