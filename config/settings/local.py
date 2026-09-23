"""Local development settings."""

from .base import *  # noqa: F401,F403
import os

from .base import payments_test_mode_from

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

# DRF-2340 — режим оплаты называется в контуре, а не подразумевается.
# Умолчание — тестовый: сеть не трогается, ссылка заглушечная. Но значение
# из окружения НЕ затирается: `manage.py`, celery и воркеры делают
# ``setdefault(DJANGO_SETTINGS_MODULE, "config.settings.local")``, а
# docker-compose задаёт local через ``environment:`` (он бьёт ``env_file:`` —
# эту же механику репозиторий уже измерил на DRF-1391). Жёсткое ``True``
# здесь означало бы, что такой процесс выдаёт заглушечные ссылки ДАЖЕ когда
# контур сказал ``false``.
AYLA_PAYMENTS_TEST_MODE = payments_test_mode_from(  # noqa: F405
    os.environ.get("AYLA_PAYMENTS_TEST_MODE", "true")  # noqa: F405
)
