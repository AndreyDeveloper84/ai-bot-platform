"""Tests for the Issue #552 cache-backend production boot assertion.

The helper :func:`config.settings.base._assert_production_cache_backend`
is invoked from :class:`apps.master_api.apps.MasterApiConfig.ready` at
Django boot. It must:

* Raise :class:`ImproperlyConfigured` when DEBUG=False and the
  configured ``default`` cache BACKEND is not a Redis-backed backend.
* Pass silently for django-redis and the Django 4+ built-in Redis
  backend when DEBUG=False.
* No-op for any backend whenever DEBUG=True (local dev + tests).

The tests call the helper directly with explicit ``debug`` /
``caches`` kwargs rather than monkey-patching live settings — this
keeps them deterministic, hermetic, and independent of pytest's
DEBUG=True default for the ``config.settings.local`` settings module.
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
        "LOCATION": "redis://localhost:6379/0",
    },
}
_REDIS_BUILTIN = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://localhost:6379/0",
    },
}
_MEMCACHED = {
    "default": {
        "BACKEND": "django.core.cache.backends.memcached.PyMemcacheCache",
        "LOCATION": "127.0.0.1:11211",
    },
}


# ---------------------------------------------------------------------------
# DEBUG=False — the assertion path.
# ---------------------------------------------------------------------------


def test_assertion_raises_on_locmem_when_debug_false() -> None:
    """Production with locmem must abort boot with an informative error."""

    with pytest.raises(ImproperlyConfigured) as exc_info:
        _assert_production_cache_backend(debug=False, caches=_LOCMEM)

    message = str(exc_info.value)
    # Message must point operators at the fix surface.
    assert "Redis" in message
    assert "REDIS_URL" in message
    assert "LocMem" in message
    assert "rate limiter" in message.lower() or "rate limiters" in message.lower()


def test_assertion_passes_on_django_redis_when_debug_false() -> None:
    """django_redis backend satisfies the assertion in production."""

    # Must not raise.
    _assert_production_cache_backend(debug=False, caches=_REDIS_DJANGO_REDIS)


def test_assertion_passes_on_builtin_redis_when_debug_false() -> None:
    """Django 4+ built-in Redis backend also satisfies the assertion.

    The built-in backend lacks ``delete_pattern`` but
    ``apps.skills.faq.tools.invalidate_kb_search_cache`` falls back to
    ``cache.clear()`` when the method is absent — so atomicity-of-SETNX
    (the property the assertion exists to guarantee) is preserved.
    """

    _assert_production_cache_backend(debug=False, caches=_REDIS_BUILTIN)


def test_assertion_raises_on_non_redis_backend_when_debug_false() -> None:
    """Any non-Redis backend (memcached, dummy, ...) must abort production boot."""

    with pytest.raises(ImproperlyConfigured):
        _assert_production_cache_backend(debug=False, caches=_MEMCACHED)


def test_assertion_raises_on_missing_default_when_debug_false() -> None:
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
