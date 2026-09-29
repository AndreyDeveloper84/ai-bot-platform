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
a day age out automatically without a cleanup task.

**Это место раньше обещало то, чего нет.** Здесь стояло «Sprint 3+'s
long-term memory captures the digest before TTL fires» — снимок перед
истечением не делает никто, и не делал никогда. Обещание, у которого нет
адресата, хуже молчания: следующий читатель верит ему и не ищет.

### Два выхода, и второй перехватить нечем (DRF-2511)

Сообщение покидает окно **двумя** путями, и они разной природы:

1. **Вытеснение** — двадцать первое сообщение выдавливает первое. Наблюдаемо:
   в момент `append` уходящее ещё лежит в списке, поэтому `append` его
   возвращает и просмотр возможен.
2. **Истечение по TTL** — сутки тишины, и ключ исчезает. **Наблюдать нечего:**
   истечение происходит в Redis, никакой наш код в этот момент не работает.
   Перехват потребовал бы либо подписки на keyspace-уведомления, либо обхода
   ключей по `TTL` — то и другое инфраструктура, и заводить её ради этого —
   отдельное решение. **Этот выход остаётся неперехваченным, и это названный
   предел, а не забытый случай.**
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


def _decode_all(raw: Any, conversation_id: UUID | str) -> list[dict[str, Any]]:
    """Разобрать хранимые строки; кривую — пропустить с логом, не бросать.

    Один разбор на оба чтения (окно и вытесненное): второй разошёлся бы с
    первым в том, как относится к испорченной записи, и одна из дорог начала
    бы ронять ход целиком.
    """
    out: list[dict[str, Any]] = []
    for item in cast(list[str], raw or []):
        try:
            out.append(json.loads(item))
        except json.JSONDecodeError:
            # A malformed value (manual operator edit?) would otherwise
            # poison the entire read. Skip + log, never raise.
            logger.warning(
                "memory.short_term.decode.malformed conversation=%s item=%r",
                conversation_id,
                item[:200],
            )
    return out


def append(
    conversation_id: UUID | str,
    *,
    role: str,
    content: str,
    **extras: Any,
) -> list[dict[str, Any]]:
    """Append one message to the sliding window; return what fell off.

    Behaviour:
      1. LRANGE reads the items this append is about to push out.
      2. RPUSH the JSON-encoded message dict at the tail.
      3. LTRIM to keep at most `SHORT_TERM_MEMORY_DEPTH` items, dropping
         the oldest on overflow.
      4. EXPIRE refreshes the 24h TTL — silent conversations age out
         automatically.
      5. write_audit("memory.append") records the append for forensic
         recovery. Payload contains only message metadata (role, len),
         never the raw content (PII rule from A1).

    DRF-2511 — step 1 is new, and the return value with it. Until then
    eviction was **unobservable**: the trim dropped the oldest item and
    nobody could say what it was, so nothing downstream could look at a
    message on its way out. The read rides in the same pipeline as the
    write, so it costs one round trip, not two, and returns an empty list
    for every conversation shorter than the window — which is most of them.

    Deciding what to DO with the dropped items is deliberately not here:
    this module is storage and knows nothing of consent, identity or
    extraction. The caller that holds the person decides
    (:mod:`apps.orchestrator.memory.evicted_review`).

    Args:
      conversation_id: UUID of the Conversation row.
      role: matches `Message.Role` choices.
      content: message body. Not echoed into audit payload.
      **extras: any extra fields to persist alongside (e.g. `trace_id`,
                `action_type`). Stored as-is in the JSON dict.

    Returns:
      The messages this append pushed out of the window, oldest first.
      Empty when the window had room. Callers that ignore it are correct —
      the value is an offer, not an obligation.
    """

    msg: dict[str, Any] = {"role": role, "content": content, **extras}
    encoded = json.dumps(msg, ensure_ascii=False, default=str)
    key = _key(conversation_id)
    client = _redis_client()

    # Pipeline keeps the three commands atomic in the Redis sense —
    # multi-server clusters with cross-slot constraints don't apply
    # because all three target the same key.
    depth = _depth()
    pipe = client.pipeline()
    # `lrange(key, 0, -depth)` BEFORE the push is exactly the set this append
    # will evict: with L items it returns indices 0..L-depth, and for L < depth
    # the range is empty. Reading after the trim would be too late — the items
    # would already be gone.
    pipe.lrange(key, 0, -depth)
    pipe.rpush(key, encoded)
    pipe.ltrim(key, -depth, -1)
    pipe.expire(key, _ttl_seconds())
    results = pipe.execute()
    dropped = _decode_all(results[0] if results else [], conversation_id)

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
        "memory.short_term.append conversation=%s role=%s len=%d dropped=%d",
        conversation_id,
        role,
        len(content),
        len(dropped),
    )
    return dropped


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
    return _decode_all(raw, conversation_id)


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
