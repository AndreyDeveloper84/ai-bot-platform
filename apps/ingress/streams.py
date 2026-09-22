"""Redis Streams enqueue — bridge from ingress to workers (DRF-424 / C2).

Sync ingress / async execution split per design doc §5. A webhook
view records the journal row (C1), then calls ``enqueue()`` here to
hand the work off to the worker consumer (C3). The HTTP response can
ack within 300ms because the heavy lifting (LLM, tool calls) is async.

Why Redis Streams (not Celery, not a plain pub/sub):
  - Consumer groups give us at-least-once delivery with PEL (Pending
    Entries List) for retries on worker crash.
  - (DRF-2220) A stream is a hand-off, not an archive. An entry carries
    the raw webhook body — message text, name, a shared contact — so it
    is deleted once processed, and whatever is left (failed entries in
    the PEL, the reaper's ``:dlq`` copies) is trimmed after
    ``settings.INGRESS_RAW_RETENTION_HOURS``. Nothing reads a stream's
    history back: an earlier line here promised a replay that reads these
    streams, and no such code exists.
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
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, cast

from django.conf import settings
from django.utils import timezone
import redis

from apps.events.services import emit
from apps.tenancy.context import current_tenant, current_trace_id

logger = logging.getLogger(__name__)

# Default Redis Streams consumer group. Per-stream group name lives on
# the same channel that produces the stream — workers add themselves
# as consumers within this group.
DEFAULT_GROUP_NAME = "consumers"

#: The reaper's parallel stream for terminal entries: ``ingress:max`` →
#: ``ingress:max:dlq``. It holds the same raw bodies, so it is bounded the
#: same way (DRF-2220).
DLQ_SUFFIX = ":dlq"

#: XRANGE page size for the per-person purge. Streams are bounded by the
#: retention window, so a page or two per stream is the normal case.
_PURGE_PAGE = 500


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
    tenant_id: str | None = None,
    group: str = DEFAULT_GROUP_NAME,
) -> str:
    """XADD a payload to ``ingress:<channel>`` for downstream consumers.

    Args:
      channel: Channel slug used as the stream suffix
               (``ingress:max``, etc.).
      payload: Dict serialised to JSON in the stream entry's ``data`` field.
      tenant_id: Explicit tenant id (str(UUID)) to promote to a top-level
                 entry field. If omitted, reads ``current_tenant()`` from
                 ContextVar. If still None, the field is "" (system event).
      group: Consumer group name. Default ``consumers``. Override only
             for specialised pipelines (e.g. ``replay`` in Sprint 5).

    Returns:
      The stream entry id assigned by Redis (e.g. ``1715456789-0``).

    Side effects:
      - Ensures the consumer group exists.
      - Emits an ``ingress.enqueued`` event row with trace_id + tenant_id.

    Why top-level tenant_id + trace_id (not nested in ``data``):
      Redis Streams indexes by flat field, and the consumer's
      TenantAwareTask reads the tenant from the top-level entry field
      without parsing the JSON body. (DRF-2220: this line used to cite
      replay tools querying the stream; there are none, and the entry
      does not outlive its processing — see the module docstring.)
    """

    stream = f"{_stream_prefix()}:{channel}"
    client = _client()
    _ensure_group(client, stream, group)

    resolved_tenant_id = tenant_id
    if resolved_tenant_id is None:
        current = current_tenant()
        resolved_tenant_id = str(current.id) if current is not None else ""

    # Stream entries are flat string-keyed dicts. ``data`` carries the
    # full payload; ``trace_id`` + ``resolved_tenant_id`` are top-level
    # for indexing without JSON parsing.
    entry = {
        "data": json.dumps(payload, ensure_ascii=False, default=str),
        "trace_id": current_trace_id() or str(uuid.uuid4()),
        "resolved_tenant_id": resolved_tenant_id,
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
            "resolved_tenant_id": resolved_tenant_id,
        },
    )
    logger.info(
        "ingress.enqueued stream=%s entry_id=%s channel=%s",
        stream,
        entry_id,
        channel,
    )
    return entry_id


# --- DRF-2220: how long a raw webhook body may live here ------------------


def raw_streams() -> list[str]:
    """Every stream that holds raw webhook bodies: each ingress stream and its DLQ."""

    from apps.workers.registry import registered_streams

    out: list[str] = []
    for stream in registered_streams():
        out.extend((stream, f"{stream}{DLQ_SUFFIX}"))
    return out


def retention_cutoff_id(now: datetime | None = None) -> str:
    """Stream id below which an entry is past ``INGRESS_RAW_RETENTION_HOURS``.

    A stream id's first half is the enqueue time in milliseconds, so the
    retention window is a plain id comparison — no body is read.
    """

    hours = int(getattr(settings, "INGRESS_RAW_RETENTION_HOURS", 72))
    moment = (now or timezone.now()).timestamp() - hours * 3600
    return str(int(moment * 1000))


def trim_expired(now: datetime | None = None) -> dict[str, int]:
    """XTRIM MINID every raw stream to the retention window. Returns counts.

    Exact, not ``~``: the streams are small, and «approximately 72 hours»
    is not a retention period anyone can state. Entries still in the PEL
    go too — past the window a failed entry has had its reaper pass and
    its triage time; that is the trade the window names.
    """

    client = _client()
    minid = retention_cutoff_id(now)
    trimmed: dict[str, int] = {}
    for stream in raw_streams():
        # The sync client returns int; its stub union includes the async variant.
        removed = int(cast(int, client.xtrim(stream, minid=minid, approximate=False)))
        if removed:
            trimmed[stream] = removed
            logger.info("ingress.trim_expired stream=%s removed=%d", stream, removed)
    return trimmed


class NoIngressStreams(RuntimeError):
    """The handler registry is empty, so there is nothing to scan — and «nothing
    found» would be a lie. Raised instead of returning an empty result so that
    the caller reports the streams as NOT checked (DRF-2220)."""


@dataclass(frozen=True)
class RawPurgeResult:
    """What one per-person purge moved. Counts, never bodies."""

    deleted: int = 0
    #: Entries at or before the cutoff whose body did not parse, so their
    #: sender is unknown. Not deleted (they may be someone else's) and not
    #: skipped silently: counted, logged, and gone by the retention window.
    unattributed: int = 0


def _sender_of(fields: dict[str, Any]) -> str | None:
    """The MAX user id an entry belongs to, or ``None`` if it cannot be told."""

    from apps.channels.max.parser import parse_max_webhook

    try:
        payload = json.loads(fields.get("data") or "")
        return str(parse_max_webhook(payload).channel_user_id) or None
    except Exception:  # noqa: BLE001 — any unreadable body is «unknown sender»
        return None


def purge_person_entries(channel_user_ids: Iterable[str], *, through: datetime) -> RawPurgeResult:
    """XDEL a person's raw webhook entries enqueued at or before ``through``.

    ``through`` is the erasure cutoff, and it bounds the scan by stream id:
    a message the person sent AFTER asking is their own again and may still
    be in flight to the consumer — deleting it would drop a live turn.
    """

    wanted = {str(i) for i in channel_user_ids if i}
    if not wanted:
        return RawPurgeResult()

    targets = raw_streams()
    if not targets:
        # The registry is filled by `apps.channels` at app ready. A process
        # without it would scan zero streams and report «0 deleted» as if it
        # had looked. It did not look — say so.
        raise NoIngressStreams(
            "no ingress streams registered — the channel handlers are not loaded in this process"
        )

    client = _client()
    max_id = str(int(through.timestamp() * 1000))
    deleted = 0
    unattributed = 0
    for stream in targets:
        doomed: list[str] = []
        low = "-"
        while True:
            page = cast(
                list[tuple[str, dict[str, Any]]],
                client.xrange(stream, min=low, max=max_id, count=_PURGE_PAGE),
            )
            for entry_id, fields in page:
                sender = _sender_of(fields)
                if sender is None:
                    unattributed += 1
                elif sender in wanted:
                    doomed.append(entry_id)
            if len(page) < _PURGE_PAGE:
                break
            low = f"({page[-1][0]}"
        if doomed:
            deleted += int(cast(int, client.xdel(stream, *doomed)))
    if unattributed:
        logger.warning(
            "ingress.purge_person unattributed=%d — bodies that did not parse "
            "stay until the retention window",
            unattributed,
        )
    return RawPurgeResult(deleted=deleted, unattributed=unattributed)
