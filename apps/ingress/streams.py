"""Redis Streams enqueue — bridge from ingress to workers (DRF-424 / C2).

Sync ingress / async execution split per design doc §5. A webhook
view records the journal row (C1), then calls ``enqueue()`` here to
hand the work off to the worker consumer (C3). The HTTP response can
ack within 300ms because the heavy lifting (LLM, tool calls) is async.

Why Redis Streams (not Celery, not a plain pub/sub):
  - Consumer groups give us at-least-once delivery with PEL (Pending
    Entries List) for retries on worker crash.
  - Trace replay (Sprint 5) reconstructs the pipeline from the same
    streams Sprint 1 writes — no second contract.
  - `redis-py` is already a dep via `celery[redis]`; no new lib.

Idempotent group creation:
  - First call to ``enqueue(stream)`` ensures the consumer group exists
    via ``XGROUP CREATE ... MKSTREAM``.
  - Subsequent calls swallow ``BUSYGROUP`` and proceed.
"""

from __future__ import annotations

import functools
import json
import logging
import uuid
from typing import Any

from django.conf import settings
import redis

from apps.events.services import emit
from apps.tenancy.context import current_trace_id

logger = logging.getLogger(__name__)

# Default Redis Streams consumer group. Per-stream group name lives on
# the same channel that produces the stream — workers add themselves
# as consumers within this group.
DEFAULT_GROUP_NAME = "consumers"


def _stream_prefix() -> str:
    """Prefix for all platform ingress streams.

    Default ``ingress`` produces stream names like ``ingress:max``,
    ``ingress:telegram``, ``ingress:web``. Tests override to avoid
    collision with a running dev consumer.
    """

    return getattr(settings, "STREAM_INGRESS_PREFIX", "ingress")


@functools.lru_cache(maxsize=1)
def _client() -> redis.Redis:
    """Cached sync Redis client. Test fixtures monkeypatch this directly."""

    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _ensure_group(client: redis.Redis, stream: str, group: str) -> None:
    """Create the consumer group on ``stream`` if it doesn't exist.

    ``XGROUP CREATE ... MKSTREAM`` is the idempotent pattern — creates
    the stream too if it's missing. ``BUSYGROUP`` raised on
    second-and-later calls means the group already exists.
    """

    try:
        client.xgroup_create(name=stream, groupname=group, id="$", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            return  # group already exists
        raise


def enqueue(
    channel: str,
    payload: dict[str, Any],
    *,
    group: str = DEFAULT_GROUP_NAME,
) -> str:
    """XADD a payload to ``ingress:<channel>`` for downstream consumers.

    Args:
      channel: Channel slug used as the stream suffix
               (``ingress:max``, etc.).
      payload: Dict serialised to JSON in the stream entry. Must
               include ``resolved_tenant_id`` (or empty string) and the
               webhook journal id so the consumer can correlate.
      group: Consumer group name. Default ``consumers``. Override only
             for specialised pipelines (e.g. ``replay`` in Sprint 5).

    Returns:
      The stream entry id assigned by Redis (e.g. ``1715456789-0``).

    Side effects:
      - Ensures the consumer group exists.
      - Emits an ``ingress.enqueued`` event row with trace_id.
    """

    stream = f"{_stream_prefix()}:{channel}"
    client = _client()
    _ensure_group(client, stream, group)

    # Stream entries are flat string-keyed dicts. Wrap the payload
    # under "data" so we always know how to deserialise. trace_id is
    # promoted to a top-level field for fast indexing by Sprint 5
    # replay tools without parsing the JSON body.
    entry = {
        "data": json.dumps(payload, ensure_ascii=False, default=str),
        "trace_id": current_trace_id() or str(uuid.uuid4()),
    }
    entry_id = client.xadd(stream, entry)  # type: ignore[arg-type]
    if isinstance(entry_id, bytes):
        entry_id = entry_id.decode("utf-8")
    assert isinstance(entry_id, str)

    emit(
        "ingress.enqueued",
        payload={
            "stream": stream,
            "entry_id": entry_id,
            "channel": channel,
        },
    )
    logger.info(
        "ingress.enqueued stream=%s entry_id=%s channel=%s",
        stream,
        entry_id,
        channel,
    )
    return entry_id
