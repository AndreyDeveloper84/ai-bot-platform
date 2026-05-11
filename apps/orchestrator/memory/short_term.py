"""Short-term Redis-backed conversation memory (DRF-454 / Sprint 2 / C1).

Stores the last N messages per Conversation as a Redis LIST. Callers
are channel handlers / AI orchestrator (Sprint 3+); they append on
every turn and recall before composing the LLM prompt.

### Why Redis LIST + LTRIM (not Stream, not Sorted Set)

- **LIST + LTRIM** gives O(1) push and a fixed-size sliding window for
  free. We only ever read the last N — no need to scan by timestamp.
- **Stream** would work but the consumer-group semantics (PEL, XACK,
  group state) are infrastructure we don't need for read-mostly recall.
- **Sorted Set** would allow time-range queries we don't run. Extra
  cost per push (ZADD vs RPUSH) for no benefit.

### Why no tenant prefix in the key

`conversation_id` is a UUID — collision across tenants is impossible
(probability ≪ 2⁻⁶⁰). Adding a tenant prefix would:
- inflate key length by 36 chars per row,
- not change correctness,
- complicate the cleanup path (you'd need to enumerate tenants to GC).

The forensic AuditLog `memory.append` row carries the tenant via
`current_tenant()` so cross-tenant attribution is preserved at the
audit layer.

### TTL

`SHORT_TERM_MEMORY_TTL_SECONDS` (default 24h) — the conversation will
get its TTL bumped on every append. Conversations that go silent for
a day age out automatically without a cleanup task. Sprint 3+'s
long-term memory captures the digest before TTL fires.
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Any, cast
from uuid import UUID

from django.conf import settings
import redis

from apps.audit.services import write_audit

logger = logging.getLogger(__name__)


# Short-term memory key prefix. Bare keys (no tenant prefix per module
# docstring) — `conv:{uuid}:msgs`.
_KEY_PREFIX = "conv"


@functools.lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    """Cached sync Redis client.

    Same pattern as `apps.ingress.streams._client` — tests monkeypatch
    this directly via `monkeypatch.setattr(short_term, "_redis_client", ...)`.
    """

    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


def _key(conversation_id: UUID | str) -> str:
    return f"{_KEY_PREFIX}:{conversation_id}:msgs"


def _depth() -> int:
    return int(getattr(settings, "SHORT_TERM_MEMORY_DEPTH", 20))


def _ttl_seconds() -> int:
    return int(getattr(settings, "SHORT_TERM_MEMORY_TTL_SECONDS", 24 * 3600))


def append(
    conversation_id: UUID | str,
    *,
    role: str,
    content: str,
    **extras: Any,
) -> None:
    """Append one message to the sliding window for `conversation_id`.

    Behaviour:
      1. RPUSH the JSON-encoded message dict at the tail.
      2. LTRIM to keep at most `SHORT_TERM_MEMORY_DEPTH` items, dropping
         the oldest on overflow.
      3. EXPIRE refreshes the 24h TTL — silent conversations age out
         automatically.
      4. write_audit("memory.append") records the append for forensic
         recovery. Payload contains only message metadata (role, len),
         never the raw content (PII rule from A1).

    Args:
      conversation_id: UUID of the Conversation row.
      role: matches `Message.Role` choices.
      content: message body. Not echoed into audit payload.
      **extras: any extra fields to persist alongside (e.g. `trace_id`,
                `action_type`). Stored as-is in the JSON dict.
    """

    msg: dict[str, Any] = {"role": role, "content": content, **extras}
    encoded = json.dumps(msg, ensure_ascii=False, default=str)
    key = _key(conversation_id)
    client = _redis_client()

    # Pipeline keeps the three commands atomic in the Redis sense —
    # multi-server clusters with cross-slot constraints don't apply
    # because all three target the same key.
    pipe = client.pipeline()
    pipe.rpush(key, encoded)
    pipe.ltrim(key, -_depth(), -1)
    pipe.expire(key, _ttl_seconds())
    pipe.execute()

    # Forensic audit row — never includes content body, only metadata.
    write_audit(
        "memory.append",
        target="Conversation",
        target_id=conversation_id if isinstance(conversation_id, UUID) else None,
        payload={
            "role": role,
            "content_length": len(content),
            "has_extras": bool(extras),
        },
    )
    logger.debug(
        "memory.short_term.append conversation=%s role=%s len=%d",
        conversation_id,
        role,
        len(content),
    )


def recall(
    conversation_id: UUID | str,
    n: int | None = None,
) -> list[dict[str, Any]]:
    """Return the last `n` messages (default: full window).

    Args:
      conversation_id: UUID of the Conversation row.
      n: optional cap. `None` returns up to `SHORT_TERM_MEMORY_DEPTH`
         (whatever's in the LIST).

    Returns:
      List of message dicts in insertion order (oldest → newest).
      Empty list when the key is missing or expired.
    """

    key = _key(conversation_id)
    client = _redis_client()
    start = -(n if n is not None else _depth())
    # redis-py types `lrange` as `Awaitable[list] | list` because the
    # same module class hosts both sync and async clients. We use the
    # sync `Redis` class, so this is always `list[str]` at runtime —
    # narrow via cast so the iteration below type-checks.
    raw = cast(list[str], client.lrange(key, start, -1))
    out: list[dict[str, Any]] = []
    for item in raw:
        try:
            out.append(json.loads(item))
        except json.JSONDecodeError:
            # A malformed value (manual operator edit?) would otherwise
            # poison the entire recall. Skip + log, never raise.
            logger.warning(
                "memory.short_term.recall.malformed conversation=%s item=%r",
                conversation_id,
                item[:200],
            )
    return out


def clear(conversation_id: UUID | str) -> None:
    """Delete the entire window for `conversation_id`.

    Used by the 152-ФЗ delete-my-data workflow and the
    `Conversation.mark_deleted()` path (Sprint 3+ will wire this when
    handoff lands).
    """

    client = _redis_client()
    client.delete(_key(conversation_id))
    write_audit(
        "memory.cleared",
        target="Conversation",
        target_id=conversation_id if isinstance(conversation_id, UUID) else None,
    )
