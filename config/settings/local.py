"""Local development settings."""

from .base import *  # noqa: F401,F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

# Issue #552 — local dev + the pytest suite must not require a running
# Redis. Override the base.py Redis cache with locmem; the production
# boot assertion in apps.master_api.apps is bypassed because DEBUG=True
# above. Real cross-worker atomic semantics are not needed in tests —
# everything runs single-process.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ai-bot-platform-local",
    },
}
