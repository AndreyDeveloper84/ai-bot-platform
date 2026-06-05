"""Tests for the Issue #552 cache-backend production boot assertion.

The helper :func:`config.settings.base._assert_production_cache_backend`
is invoked from :class:`apps.master_api.apps.MasterApiConfig.ready` at
Django boot. It must:

* Raise :class:`ImproperlyConfigured` when DEBUG=False and the
  configured ``default`` cache BACKEND is not a Redis-backed backend.
* Pass silently for django-redis and the Django 4+ built-in Redis
  backend when DEBUG=False (provided LOCATION + KEY_PREFIX are also
  production-shaped — see post-#552-amendment guards).
* No-op for any backend whenever DEBUG=True (local dev + tests).

The tests call the helper directly with explicit ``debug`` /
``caches`` kwargs rather than monkey-patching live settings — this
keeps them deterministic, hermetic, and independent of pytest's
DEBUG=True default for the ``config.settings.local`` settings module.
The four post-amendment tests additionally monkeypatch ``DJANGO_ENV``
since the assertion now reads it directly.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from config.settings.base import _assert_production_cache_backend


_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ci-tests",
    },
}
_REDIS_DJANGO_REDIS = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://redis.internal:6379/0",
        "KEY_PREFIX": "ai_bot_platform:production",
    },
}
_REDIS_BUILTIN = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://redis.internal:6379/0",
        "KEY_PREFIX": "ai_bot_platform:production",
    },
}
_MEMCACHED = {
    "default": {
        "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
        "LOCATION": "127.0.0.1:11211",
    },
}


@pytest.fixture
def prod_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set DJANGO_ENV to a non-'local' value for production-path tests."""
    monkeypatch.setenv("DJANGO_ENV", "production")


# ---------------------------------------------------------------------------
# DEBUG=False — the assertion path.
# ---------------------------------------------------------------------------


def test_assertion_raises_on_locmem_when_debug_false(prod_env: None) -> None:
    """Production with locmem must abort boot with an informative error."""

    with pytest.raises(ImproperlyConfigured) as exc_info:
        _assert_production_cache_backend(debug=False, caches=_LOCMEM)

    message = str(exc_info.value)
    # Message must point operators at the fix surface.
    assert "Redis" in message
    assert "REDIS_URL" in message
    assert "LocMem" in message
    assert "rate limiter" in message.lower() or "rate limiters" in message.lower()


def test_assertion_passes_on_django_redis_when_debug_false(prod_env: None) -> None:
    """django_redis backend satisfies the assertion in production."""

    # Must not raise.
    _assert_production_cache_backend(debug=False, caches=_REDIS_DJANGO_REDIS)


def test_assertion_passes_on_builtin_redis_when_debug_false(prod_env: None) -> None:
    """Django 4+ built-in Redis backend also satisfies the assertion.

    The built-in backend lacks ``delete_pattern`` but
    ``apps.skills.faq.tools.invalidate_kb_search_cache`` falls back to
    ``cache.clear()`` when the method is absent — so atomicity-of-SETNX
    (the property the assertion exists to guarantee) is preserved.
    """

    _assert_production_cache_backend(debug=False, caches=_REDIS_BUILTIN)


def test_assertion_raises_on_non_redis_backend_when_debug_false(prod_env: None) -> None:
    """Any non-Redis backend (memcached, dummy, ...) must abort production boot."""

    with pytest.raises(ImproperlyConfigured):
        _assert_production_cache_backend(debug=False, caches=_MEMCACHED)


def test_assertion_raises_on_missing_default_when_debug_false(prod_env: None) -> None:
    """A CACHES dict without ``default`` must also abort — defensive guard."""

    with pytest.raises(ImproperlyConfigured):
        _assert_production_cache_backend(debug=False, caches={})


# ---------------------------------------------------------------------------
# DEBUG=True — the bypass path. Local dev + tests must tolerate locmem.
# ---------------------------------------------------------------------------


def test_assertion_skipped_on_debug_true_with_locmem() -> None:
    """Local dev with DEBUG=True must boot cleanly on locmem."""

    _assert_production_cache_backend(debug=True, caches=_LOCMEM)


def test_assertion_skipped_on_debug_true_with_redis() -> None:
    """DEBUG=True is a hard bypass — backend choice is irrelevant."""

    _assert_production_cache_backend(debug=True, caches=_REDIS_DJANGO_REDIS)


def test_assertion_skipped_on_debug_true_with_empty_caches() -> None:
    """DEBUG=True hard-bypass — even an empty CACHES dict must not abort.

    Belt-and-braces: prove the early-return precedes the dict lookup.
    """

    _assert_production_cache_backend(debug=True, caches={})


# ---------------------------------------------------------------------------
# Post-amendment (PR #552 amendment) — adversarial PRE_PILOT findings.
# ---------------------------------------------------------------------------


def test_localhost_location_with_debug_false_raises(prod_env: None) -> None:
    """PRE_PILOT #1 — REDIS_URL silent localhost fallback.

    Forgetting to set REDIS_URL in production leaves the default
    ``redis://localhost:6379/0`` LOCATION, where no Redis runs. Boot
    would succeed and the first cache write would ConnectionError. The
    assertion must catch this at boot.
    """

    caches = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://localhost:6379/0",
            "KEY_PREFIX": "ai_bot_platform:production",
        },
    }
    with pytest.raises(ImproperlyConfigured) as exc_info:
        _assert_production_cache_backend(debug=False, caches=caches)

    message = str(exc_info.value)
    assert "localhost" in message
    assert "REDIS_URL" in message


def test_127001_location_with_debug_false_raises(prod_env: None) -> None:
    """PRE_PILOT #1 — same failure mode, IP literal form."""

    caches = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://127.0.0.1:6379/0",
            "KEY_PREFIX": "ai_bot_platform:production",
        },
    }
    with pytest.raises(ImproperlyConfigured) as exc_info:
        _assert_production_cache_backend(debug=False, caches=caches)

    assert "127.0.0.1" in str(exc_info.value)


def test_remote_location_with_debug_false_passes(prod_env: None) -> None:
    """PRE_PILOT #1 — a real remote LOCATION must satisfy the assertion."""

    caches = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://redis.internal:6379/0",
            "KEY_PREFIX": "ai_bot_platform:production",
        },
    }
    # Must not raise.
    _assert_production_cache_backend(debug=False, caches=caches)


def test_local_keyspace_suffix_with_debug_false_raises(prod_env: None) -> None:
    """PRE_PILOT #2 — KEY_PREFIX ending in ``:local`` indicates DJANGO_ENV
    is unset or literally 'local'. On a shared Redis instance this collides
    with dev/CI namespaces and corrupts SETNX idempotency + rate limits.

    The production-shaped KEY_PREFIX (``:production``) must pass.
    """

    caches_local = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://redis.internal:6379/0",
            "KEY_PREFIX": "ai_bot_platform:local",
        },
    }
    with pytest.raises(ImproperlyConfigured) as exc_info:
        _assert_production_cache_backend(debug=False, caches=caches_local)

    message = str(exc_info.value)
    assert "DJANGO_ENV" in message
    assert "local" in message

    # Production-shaped suffix passes.
    caches_prod = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://redis.internal:6379/0",
            "KEY_PREFIX": "ai_bot_platform:production",
        },
    }
    _assert_production_cache_backend(debug=False, caches=caches_prod)
