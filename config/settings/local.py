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

# #842 — tests don't run Redis. Disable PII tokenization by default so
# the pytest suite doesn't fail on `redis.ConnectionError`. Tests that
# exercise the tokenizer explicitly (`test_pii_*`) use `fake_redis`
# fixture + opt back in via settings override / explicit `pii_context`.
PII_TOKENIZER_ENABLED = False
