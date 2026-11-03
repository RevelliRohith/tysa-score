import os
from pathlib import Path
import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize django-environ and read .env if present
env = environ.Env()
env_file = BASE_DIR / '.env'
if env_file.exists():
    environ.Env.read_env(str(env_file))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'tysa-score-insecure-dev-key-change-in-production-2026')

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DJANGO_DEBUG', 'True').lower() in ('true', '1', 'yes')

ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')

# Application definition
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party
    'rest_framework',
    'channels',
    
    # TYSA Score core apps
    'accounts',
    'athletes',
    'judges',
    'championships',
    'catalogue',
    'registrations',
    'competition',
    'scoring',
    'corrections',
    'results',
    'certificates',
    'reports',
    'audit',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'accounts.context_processors.user_roles',
                'accounts.context_processors.active_championship_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database
# Support Neon PostgreSQL via DATABASE_URL if provided, else fallback to SQLite for local development.
DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    # Resilient DNS resolver fallback for Neon AWS endpoint CNAMEs on Windows
    try:
        import socket
        _orig_getaddrinfo = socket.getaddrinfo
        def _neon_resilient_getaddrinfo(host, port, *args, **kwargs):
            try:
                return _orig_getaddrinfo(host, port, *args, **kwargs)
            except (socket.gaierror, Exception):
                if host and 'neon.tech' in str(host):
                    # Fallback to known Neon pooler IPs when local Wi-Fi/router DNS blocks/refuses resolution
                    for fallback_ip in ['13.251.17.193', '18.138.49.39', '3.0.167.45']:
                        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (fallback_ip, port))]
                raise
        socket.getaddrinfo = _neon_resilient_getaddrinfo
    except Exception:
        pass
    try:
        import dj_database_url
        db_config = dj_database_url.config(
            default=DATABASE_URL,
            conn_max_age=int(os.environ.get('DB_CONN_MAX_AGE', '0')),  # Neon connection pooler (PgBouncer) requires conn_max_age=0
            conn_health_checks=True,
        )
        # Enforce SSL requirement and disable server-side cursors for pooled Neon connections
        if 'OPTIONS' not in db_config:
            db_config['OPTIONS'] = {}
        db_config['OPTIONS']['sslmode'] = 'require'
        db_config['DISABLE_SERVER_SIDE_CURSORS'] = True
        db_config['TEST'] = {
            'SERIALIZE': False,
        }
        
        DATABASES = {'default': db_config}
    except ImportError:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
                'OPTIONS': {'timeout': 30},
            }
        }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'OPTIONS': {'timeout': 30},
        }
    }

# Custom User Model
AUTH_USER_MODEL = 'accounts.User'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {'min_length': 6},
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Media files (Uploads, Certificates, Pose photos)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

# Channels Channel Layer
REDIS_URL = os.environ.get('REDIS_URL')
if REDIS_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [REDIS_URL],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer"
        }
    }

# Celery Configuration
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_TASK_ALWAYS_EAGER = os.environ.get('CELERY_ALWAYS_EAGER', 'True').lower() in ('true', '1', 'yes')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# Login URLs
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# Allowed CSRF Origins for local development & previews
CSRF_TRUSTED_ORIGINS = [
    'http://127.0.0.1:8027',
    'http://localhost:8027',
    'http://127.0.0.1:8000',
    'http://localhost:8000',
]
# Automatically permit local machine LAN IPs and private network origins
try:
    import socket
    for _ip in socket.gethostbyname_ex(socket.gethostname())[2]:
        if _ip not in ('127.0.0.1',):
            CSRF_TRUSTED_ORIGINS.extend([f'http://{_ip}:8027', f'http://{_ip}:8000'])
except Exception:
    pass

# Production Security Flags
if not DEBUG and os.environ.get('SECURE_SSL_ENABLED', 'False').lower() in ('true', '1', 'yes'):
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

