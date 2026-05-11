"""Prompt registry cache + Redis pub/sub subscriber (DRF-480 / Sprint 4 / A5).

In-process dict cache with a daemon Redis subscriber that fires
invalidations on the ``promptreg:invalidate:*`` pattern.

### Architecture

Each gunicorn worker (or test process) holds its own ``_PROMPT_CACHE``,
``_THRESHOLD_CACHE``, ``_DISCLAIMER_CACHE`` keyed by tuples. On first
read in the process, ``_ensure_subscriber()`` lazily launches a
daemon thread that subscribes to ``promptreg:invalidate:*`` and
evicts entries when a publish lands.

### Why a daemon thread, not gevent / asyncio

The platform mixes sync Django views, Celery workers, and the MAX
handler (sync). A daemon thread is the lowest-common-denominator —
no event loop dependency, exits with the process. Daemon=True so a
stuck subscribe loop never blocks shutdown.

### Auto-reconnect

If Redis bounces, the subscriber catches ``ConnectionError``,
sleeps with exponential backoff (max 30s), and retries. We don't
crash the worker on transient Redis loss.

### Publish path

Writers (A4 ``publish_prompt``) call :func:`publish_invalidate` to
post the eviction message. The subscriber in every other worker
then evicts; the writer's local cache is also evicted via the same
publish (Redis broadcasts to every subscriber including the publisher).

### Test path

Tests that don't want to spin up a Redis subscriber thread use the
:func:`_dispatch_invalidate_sync` test hook — it runs the same
eviction logic without the thread.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

import redis
from django.conf import settings

if TYPE_CHECKING:
    from apps.promptreg.models import (
        DisclaimerLibrary,
        PromptVersion,
        ThresholdConfig,
    )

logger = logging.getLogger(__name__)

# Channel pattern: `promptreg:invalidate:<tenant_uuid>` with JSON payload
# `{"kind": "prompt|threshold|disclaimer", "key": "<key-tuple-json>"}`.
_CHANNEL_PATTERN = "promptreg:invalidate:*"
_CHANNEL_PREFIX = "promptreg:invalidate:"


# Cache shape:
# - prompts:     {(tenant_id, skill_name): PromptVersion}
# - thresholds:  {(tenant_id, key, applied_to): ThresholdConfig}
# - disclaimers: {(tenant_id, category, risk_level): DisclaimerLibrary}
_PROMPT_CACHE: dict[tuple[UUID, str], "PromptVersion"] = {}
_THRESHOLD_CACHE: dict[tuple[UUID, str, str], "ThresholdConfig"] = {}
_DISCLAIMER_CACHE: dict[tuple[UUID, str, str], "DisclaimerLibrary"] = {}

_subscriber_thread: threading.Thread | None = None
_subscriber_lock = threading.Lock()
_subscriber_stop = threading.Event()


# --- Public cache accessors --------------------------------------------------


def get_prompt_cached(tenant_id: UUID, skill_name: str) -> "PromptVersion | None":
    _ensure_subscriber()
    return _PROMPT_CACHE.get((tenant_id, skill_name))


def put_prompt(tenant_id: UUID, skill_name: str, value: "PromptVersion") -> None:
    _PROMPT_CACHE[(tenant_id, skill_name)] = value


def get_threshold_cached(tenant_id: UUID, key: str, applied_to: str) -> "ThresholdConfig | None":
    _ensure_subscriber()
    return _THRESHOLD_CACHE.get((tenant_id, key, applied_to))


def put_threshold(tenant_id: UUID, key: str, applied_to: str, value: "ThresholdConfig") -> None:
    _THRESHOLD_CACHE[(tenant_id, key, applied_to)] = value


def get_disclaimer_cached(
    tenant_id: UUID, category: str, risk_level: str
) -> "DisclaimerLibrary | None":
    _ensure_subscriber()
    return _DISCLAIMER_CACHE.get((tenant_id, category, risk_level))


def put_disclaimer(
    tenant_id: UUID, category: str, risk_level: str, value: "DisclaimerLibrary"
) -> None:
    _DISCLAIMER_CACHE[(tenant_id, category, risk_level)] = value


# --- Invalidation ------------------------------------------------------------


def invalidate(tenant_id: UUID, kind: str, key: tuple[str, ...] | None = None) -> None:
    """Surgically evict cache entries.

    Args:
      tenant_id: scope the eviction to this tenant.
      kind: "prompt" | "threshold" | "disclaimer".
      key: tuple of the remaining cache key fields. ``None`` evicts
           every entry for the tenant of that kind.
    """

    cache = _select_cache(kind)
    if cache is None:
        logger.warning("promptreg.cache.invalidate.unknown_kind kind=%s", kind)
        return

    if key is None:
        # Evict every entry whose first key component matches tenant_id.
        for k in list(cache.keys()):
            if k[0] == tenant_id:
                cache.pop(k, None)
        return

    cache.pop((tenant_id, *key), None)


def invalidate_all() -> None:
    """Flush every cache. Debug / shutdown only."""

    _PROMPT_CACHE.clear()
    _THRESHOLD_CACHE.clear()
    _DISCLAIMER_CACHE.clear()


def _select_cache(kind: str) -> dict | None:
    if kind == "prompt":
        return _PROMPT_CACHE
    if kind == "threshold":
        return _THRESHOLD_CACHE
    if kind == "disclaimer":
        return _DISCLAIMER_CACHE
    return None


# --- Pub/sub publisher (called by A4 services) -------------------------------


def publish_invalidate(tenant_id: UUID, kind: str, key: tuple[str, ...] | None = None) -> None:
    """Broadcast a cache-invalidation message to every subscriber.

    Called from ``transaction.on_commit`` in A4 so subscribers never
    evict before the underlying DB write commits.

    Swallows Redis errors (telemetry contract: cache layer never
    breaks the request path).
    """

    payload: dict[str, Any] = {"kind": kind}
    if key is not None:
        payload["key"] = list(key)
    channel = f"{_CHANNEL_PREFIX}{tenant_id}"
    try:
        _client().publish(channel, json.dumps(payload))
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        logger.exception(
            "promptreg.cache.publish_failed channel=%s payload=%s",
            channel,
            payload,
        )


def _dispatch_invalidate_sync(tenant_id: UUID, kind: str, key: tuple[str, ...] | None) -> None:
    """Test hook: invoke the eviction path without going through Redis.

    Tests that need to verify cache invalidation but don't want a
    background subscriber call this directly. Production code path
    is publish_invalidate → Redis → subscriber → invalidate.
    """

    invalidate(tenant_id, kind, key)


# --- Subscriber thread -------------------------------------------------------


def _client() -> redis.Redis:
    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _ensure_subscriber() -> None:
    """Lazily launch the pub/sub subscriber once per process.

    No-op when the subscriber is already running OR when running under
    tests with ``settings.PROMPTREG_DISABLE_SUBSCRIBER = True``
    (tests use ``_dispatch_invalidate_sync`` instead).
    """

    if getattr(settings, "PROMPTREG_DISABLE_SUBSCRIBER", False):
        return

    global _subscriber_thread
    with _subscriber_lock:
        if _subscriber_thread is not None and _subscriber_thread.is_alive():
            return
        _subscriber_stop.clear()
        _subscriber_thread = threading.Thread(
            target=_subscribe_loop,
            name="promptreg-subscriber",
            daemon=True,
        )
        _subscriber_thread.start()


def stop_subscriber() -> None:
    """Cooperative stop for tests + shutdown hooks."""

    _subscriber_stop.set()


def _subscribe_loop() -> None:
    """Subscribe to ``promptreg:invalidate:*`` with auto-reconnect."""

    backoff = 1.0
    while not _subscriber_stop.is_set():
        try:
            pubsub = _client().pubsub(ignore_subscribe_messages=True)
            pubsub.psubscribe(_CHANNEL_PATTERN)
            backoff = 1.0  # reset on successful subscribe
            for message in pubsub.listen():
                if _subscriber_stop.is_set():
                    break
                if message is None or message.get("type") not in (
                    "pmessage",
                    "message",
                ):
                    continue
                _handle_message(message)
        except (redis.ConnectionError, redis.TimeoutError, OSError):
            logger.warning("promptreg.cache.subscriber.disconnect retry_in=%s", backoff)
            # Cap exponential backoff at 30s.
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
        except Exception:  # noqa: BLE001 — keep the loop alive
            logger.exception("promptreg.cache.subscriber.unexpected_error")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _handle_message(message: dict[str, Any]) -> None:
    """Parse a pub/sub message and call ``invalidate``."""

    channel = message.get("channel", "")
    if not channel.startswith(_CHANNEL_PREFIX):
        return
    tenant_str = channel[len(_CHANNEL_PREFIX) :]
    try:
        tenant_id = UUID(tenant_str)
    except ValueError:
        logger.warning("promptreg.cache.subscriber.bad_tenant tenant=%s", tenant_str)
        return

    data_raw = message.get("data") or "{}"
    try:
        payload = json.loads(data_raw)
    except (TypeError, ValueError):
        logger.warning("promptreg.cache.subscriber.bad_payload data=%s", data_raw)
        return

    kind = payload.get("kind", "")
    key_list = payload.get("key")
    key_tuple = tuple(key_list) if isinstance(key_list, list) else None
    invalidate(tenant_id, kind, key_tuple)
