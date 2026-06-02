"""PII tokenizer for LLM-prompt path (152-ФЗ Tier-A, Phase 0).

Russian 152-ФЗ §6 treats transmission to external processors (OpenAI,
Anthropic) as «processing». Sending raw user PII (phone, email, card)
through to vendor LLMs without per-instance consent is a violation
risk. This module pseudonymises PII before LLM dispatch and reverses
the substitution on response — the vendor sees stable opaque tokens,
the user sees their original phone number back.

### Phase B verdicts locked 2026-05-26 (per tech-lead handoff)

1. **Per-conversation token scope (D1=B)** — same entity mentioned
   twice within one conversation gets the same token. Different
   conversation = fresh namespace. Storage: single Redis HASH per
   ``conversation_id`` с TTL.
2. **Regex-only detection (D2=A)** — phone / email / cc / otp / url
   token regexes reused from :mod:`apps.replay.redactor`. Russian-name
   detection requires natasha NER (ADR-0011 §1 defers к Phase 1).
3. **5 categories (D3)** — PHONE / EMAIL / CC / OTP / URL_TOKEN.
4. **Same-token-per-entity (D4=A)** — normalised lookup для phone
   (strip non-digit + Russian 7/8-prefix canonicalisation), lowercase
   for email; other categories raw.
5. **Defensive reverse-substitution (D5)** — exact token-format match,
   hallucinated tokens dropped (logged), case-sensitive, sorted by
   token length DESC.
6. **Redis ephemeral storage (D6)** — token map lives в Redis HASH с
   TTL ~= conversation TTL + 1h grace. NEVER persisted в Postgres,
   audit rows, or logs in raw form.

### Per-conversation NONCE (Phase G adversarial defence)

Without a nonce, an attacker could type literal ``<PHONE_5>`` and the
detokenize pass would reverse-substitute с another user's PII if the
conversation has 5 real phones minted. Per-conversation random
NONCE in the token format closes this attack:

  Token format: ``<{CAT}_{NONCE}_{INDEX}>`` (e.g. ``<PHONE_a8f2c1d4_1>``)

NONCE is 8 hex chars from `secrets.token_hex(4)`, seeded атомарно on
the first tokenize call via HSETNX. User cannot guess NONCE → cannot
construct token shape matching the rev-map. Sentinel-escape approach
was rejected because LLMs «normalise» weird formatting away on output.

### Hash schema

Single Redis HASH per conversation. Fields:

* ``nonce`` → 8-hex-char random string, seeded once per conversation.
* ``fwd:{cat}:{normalised}`` → token. Forward map для idempotent
  tokenisation on repeat occurrence.
* ``rev:{token}`` → original raw string. Used by detokenize().
* ``cnt:{cat}`` → integer counter. Next index для new token.

Why single HASH (not 3 keys): one EXPIRE refreshes atomically, fewer
round-trips, simpler clear_conversation.

### Atomic get-or-create via Lua

Race window analysis: parallel-turn processing of same PII would HGET
twice + write different tokens. Lua script eliminates the window
(per ``apps.workers.ceilings`` precedent для similar count+expire
atomicity). HSETNX seeds NONCE atomically on first write.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
import re
import secrets
from collections.abc import Iterator
from typing import Any, Final
from uuid import UUID

import redis
from django.conf import settings

from apps.replay.redactor import (
    CC_RE,
    EMAIL_RE,
    OTP_RE,
    PHONE_RE,
    URL_TOKEN_RE,
)

logger = logging.getLogger(__name__)


# --- Conversation-scope ContextVar ------------------------------------------


_pii_conversation_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "pii_conversation_id", default=None
)


def current_conversation_id() -> str | None:
    """Return the active PII conversation scope, or None if outside one.

    The decorator (:class:`apps.llm.pii_protected_provider
    .PIITokenizingProvider`) reads this at LLM-call boundary. None →
    no tokenization (e.g. internal background job без user PII).
    Non-None → tokenize before send, detokenize after receive.
    """
    return _pii_conversation_var.get()


@contextlib.contextmanager
def pii_context(conversation_id: UUID | str | None) -> Iterator[None]:
    """Activate PII tokenization scope для the wrapped code block.

    Usage::

        with pii_context(conversation.id):
            result = await provider.complete(messages, model="gpt-4")

    Nested scopes follow stack discipline — outer restored on exit.
    Passing ``None`` deactivates tokenization for the block (override
    для internal flows that won't contain user PII).
    """
    cid_str = str(conversation_id) if conversation_id is not None else None
    token = _pii_conversation_var.set(cid_str)
    try:
        yield
    finally:
        _pii_conversation_var.reset(token)


def _enter_scope_unsafe(conversation_id: UUID | str | None) -> Any:
    """Flat-API equivalent of :func:`pii_context` enter — returns the
    contextvar token caller passes к :func:`_exit_scope_unsafe` later.

    **Underscore-prefixed = NOT a public API.** Use when a 270-line
    `with` block would force prohibitive indent churn (e.g. wrapping
    the body of `apps/orchestrator/pipeline.py::_run_under_tenant`
    which already nests inside `tenant_scope` + `_step_event`). Stack
    discipline is the caller's responsibility: every call MUST be
    balanced by ``_exit_scope_unsafe(token)`` в a ``try/finally``
    block.

    **Forgetting the `finally` permanently leaks the contextvar к
    the next turn on the same worker thread**, causing cross-
    conversation tokenization against the wrong Redis namespace —
    silent data corruption. ALWAYS prefer :func:`pii_context`
    (``with``-style) when nesting depth permits. The underscore prefix
    is the documented signal that this is a footgun-grade helper.

    W3 adversarial verdict (PR #842, 2026-06-02): renamed from public
    `enter_scope` / `exit_scope` to discourage casual external use.
    """
    cid_str = str(conversation_id) if conversation_id is not None else None
    return _pii_conversation_var.set(cid_str)


def _exit_scope_unsafe(token: Any) -> None:
    """Counterpart of :func:`_enter_scope_unsafe`. See its docstring.

    Reset is unconditional — if ``token`` was already reset, contextvar
    raises ``LookupError``; callers should NOT catch (indicates a bug).
    """
    _pii_conversation_var.reset(token)


# --- Category configuration -------------------------------------------------


_PATTERNS: Final[list[tuple[str, re.Pattern[str]]]] = [
    ("URL_TOKEN", URL_TOKEN_RE),
    ("CC", CC_RE),
    ("PHONE", PHONE_RE),
    ("EMAIL", EMAIL_RE),
    ("OTP", OTP_RE),
]


def _normalise(category: str, raw: str) -> str:
    """Per-category normalisation для forward-map keying.

    PHONE: strip non-digit; if 11-digit Russian number с leading 7
      или 8, canonicalise by dropping the leading digit (``+7 XXX...``
      ≡ ``8 XXX...`` per Russian telco convention).
    EMAIL: lowercase. ~All real mail servers treat local-part case-
      insensitive in practice.
    Others: pass through unchanged.
    """
    if category == "PHONE":
        digits = "".join(ch for ch in raw if ch.isdigit())
        if len(digits) == 11 and digits[0] in {"7", "8"}:
            return digits[1:]
        return digits
    if category == "EMAIL":
        return raw.lower()
    return raw


# --- Lua atomic get-or-create + NONCE seed ----------------------------------

# KEYS[1] = conversation hash key
# ARGV[1] = category (PHONE / EMAIL / etc.)
# ARGV[2] = normalised_value (forward-map key suffix)
# ARGV[3] = original_value (preserved для reverse-substitute)
# ARGV[4] = ttl_seconds (refresh after every write)
# ARGV[5] = candidate_nonce (used iff this is the first write)
#
# Returns 2-element table {token, nonce} — caller uses the nonce to
# build the conversation-specific detokenize regex.
_GET_OR_CREATE_LUA: Final[str] = """
redis.call("HSETNX", KEYS[1], "nonce", ARGV[5])
local nonce = redis.call("HGET", KEYS[1], "nonce")
local fwd_field = "fwd:" .. ARGV[1] .. ":" .. ARGV[2]
local existing = redis.call("HGET", KEYS[1], fwd_field)
if existing then
    redis.call("EXPIRE", KEYS[1], tonumber(ARGV[4]))
    return {existing, nonce}
end
local cnt_field = "cnt:" .. ARGV[1]
local idx = redis.call("HINCRBY", KEYS[1], cnt_field, 1)
local token = "<" .. ARGV[1] .. "_" .. nonce .. "_" .. idx .. ">"
redis.call("HSET", KEYS[1], fwd_field, token)
redis.call("HSET", KEYS[1], "rev:" .. token, ARGV[3])
redis.call("EXPIRE", KEYS[1], tonumber(ARGV[4]))
return {token, nonce}
"""

_get_or_create_script: object | None = None


def _get_script(redis_client: redis.Redis) -> object | None:
    """Lazy-register the Lua script on first call.

    Returns None when the client lacks ``register_script`` (lightweight
    test fakes) — caller falls back на the non-atomic HGET + HSET path.
    """
    global _get_or_create_script
    if _get_or_create_script is not None:
        return _get_or_create_script
    register = getattr(redis_client, "register_script", None)
    if register is None:
        return None
    _get_or_create_script = register(_GET_OR_CREATE_LUA)
    return _get_or_create_script


def _invalidate_script_cache() -> None:
    """Test helper. Drops the cached Script handle so the next call
    re-registers against the (potentially fresh) Redis client."""
    global _get_or_create_script
    _get_or_create_script = None


# --- Redis client + key helpers ---------------------------------------------


@functools.lru_cache(maxsize=1)
def _redis_client() -> redis.Redis:
    """Cached sync Redis client. Mirrors :mod:`apps.orchestrator.memory
    .short_term` — tests monkeypatch via
    ``setattr(pii_tokenizer, "_redis_client", lambda: ...)``."""
    url = getattr(settings, "REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(url, decode_responses=True)


_KEY_PREFIX: Final[str] = "pii_tokenmap"


def _key(conversation_id: UUID | str) -> str:
    return f"{_KEY_PREFIX}:{conversation_id}"


def _ttl_seconds() -> int:
    """TTL для the token-map HASH.

    Default = short-term memory TTL (24h) + 1h grace. Token map MUST
    outlive the conversation's short-term memory by at least the
    detokenize-after-LLM-response round-trip window.
    """
    return int(
        getattr(
            settings,
            "PII_TOKENMAP_TTL_SECONDS",
            int(getattr(settings, "SHORT_TERM_MEMORY_TTL_SECONDS", 24 * 3600)) + 3600,
        )
    )


# --- Token regex для reverse-substitution -----------------------------------


_KNOWN_CATEGORIES_RE: Final[str] = "|".join(cat for cat, _ in _PATTERNS)


def _build_token_pattern(nonce: str) -> re.Pattern[str]:
    """Construct the reverse-substitution regex для a specific NONCE.

    Per-conversation NONCE means different conversations have different
    detokenize regexes — user-typed `<PHONE_5>` literally cannot match
    because no nonce → no rev-map collision.
    """
    return re.compile(r"<(?:" + _KNOWN_CATEGORIES_RE + r")_" + re.escape(nonce) + r"_\d+>")


def _nonce_candidate() -> str:
    """Generate a 8-hex-char random nonce candidate.

    Used at first tokenize() call для a conversation (via HSETNX —
    only first writer's candidate wins, subsequent calls reuse).
    """
    return secrets.token_hex(4)


# --- Public API --------------------------------------------------------------


def tokenize(text: str, conversation_id: UUID | str) -> str:
    """Replace PII matches с stable conversation-scoped tokens.

    Same entity (normalised) on repeat occurrence within one
    conversation always maps to the same token — coreference
    preserved for the LLM. Cross-conversation: fresh namespace.

    The Redis HASH lifetime is refreshed on every write (per Lua
    script). On the first call for a conversation, an 8-hex-char NONCE
    is seeded atomically via HSETNX. The token format incorporates this
    NONCE so user-supplied literal `<CAT_N>` shapes cannot collide с
    the rev-map (Phase G adversarial defence).

    Empty text or text без PII matches returns unchanged. No side
    effects on the Redis hash either.

    Args:
      text: Raw user-supplied string.
      conversation_id: Stable identifier (UUID). Maps to the Redis
        HASH key.

    Returns:
      Tokenized string suitable для transmission to LLM provider.
    """
    if not text:
        return text

    client = _redis_client()
    key = _key(conversation_id)
    ttl = str(_ttl_seconds())
    candidate_nonce = _nonce_candidate()

    result = text
    for category, pattern in _PATTERNS:

        def _sub(match: re.Match[str], *, _cat: str = category) -> str:
            raw = match.group(0)
            normalised = _normalise(_cat, raw)
            return _resolve_token(client, key, _cat, normalised, raw, ttl, candidate_nonce)

        result = pattern.sub(_sub, result)
    return result


def detokenize(text: str, conversation_id: UUID | str) -> str:
    """Reverse-substitute known tokens back to original PII.

    Loads the conversation's NONCE from Redis to build the token regex,
    then walks the text для matches and substitutes via ``rev:`` field.
    Hallucinated tokens (LLM emitted token NOT in input) leave the
    token string in place + emit a WARNING log. User-supplied literal
    `<CAT_N>` shapes don't match the NONCE-protected regex → never
    substituted, never leak.

    Empty conversation map (no nonce seeded yet) → fast no-op return.

    Args:
      text: Raw LLM response, possibly containing tokens.
      conversation_id: Same identifier used during tokenize().

    Returns:
      String с known tokens replaced; unknown tokens preserved.
    """
    if not text:
        return text

    client = _redis_client()
    key = _key(conversation_id)
    # redis-py's hget() signature unions Awaitable+sync return types
    # depending on which client class в use. We use sync only, so
    # cast for mypy clarity.
    nonce_raw: Any = client.hget(key, "nonce")
    if nonce_raw is None:
        # No tokenize() ever ran для this conversation. Nothing к reverse.
        return text
    nonce: str = nonce_raw.decode("utf-8") if isinstance(nonce_raw, bytes) else str(nonce_raw)

    token_pattern = _build_token_pattern(nonce)
    found = token_pattern.findall(text)
    if not found:
        return text

    # Dedup + sort by length DESC. Same token mentioned twice = one
    # HGET round-trip. Length-sort guards against future regex changes
    # where a shorter token might prefix-match a longer one (Python's
    # re.sub() already handles non-overlapping left-to-right, но
    # explicit sort buys safety).
    unique_tokens = sorted(set(found), key=len, reverse=True)
    result = text
    for token in unique_tokens:
        original_raw: Any = client.hget(key, f"rev:{token}")
        if original_raw is None:
            # Hallucinated. Leave string intact, log так that operator
            # can investigate. WARNING (not ERROR): user-facing text
            # is still safe — token format itself isn't sensitive.
            logger.warning(
                "pii_tokenizer.hallucinated_token conversation_id=%s token=%s",
                conversation_id,
                token,
            )
            continue
        original = (
            original_raw.decode("utf-8") if isinstance(original_raw, bytes) else str(original_raw)
        )
        result = result.replace(token, original)
    return result


def clear_conversation(conversation_id: UUID | str) -> None:
    """Drop the entire token map для a conversation.

    Called on conversation close / archive — don't wait for TTL,
    explicit DEL gets the bytes out of Redis immediately. Idempotent.
    """
    client = _redis_client()
    client.delete(_key(conversation_id))


# --- Internals --------------------------------------------------------------


def _resolve_token(
    client: redis.Redis,
    key: str,
    category: str,
    normalised: str,
    original: str,
    ttl: str,
    candidate_nonce: str,
) -> str:
    """Atomic get-or-create token + NONCE seed for (category, normalised).

    Uses the registered Lua script when available; falls back на the
    non-atomic HGET + HSET + HSETNX path when the client lacks
    ``register_script``.
    """
    script = _get_script(client)
    if script is not None:
        # Returns [token, nonce]; we only need token here.
        result = script(  # type: ignore[operator]
            keys=[key],
            args=[category, normalised, original, ttl, candidate_nonce],
        )
        token = result[0]
        if isinstance(token, bytes):
            return token.decode("utf-8")
        return str(token)

    # Fallback path — non-atomic but matches Lua semantics.
    nonce_existing: Any = client.hget(key, "nonce")
    if nonce_existing is None:
        # Simulate HSETNX — set nonce ONLY if absent.
        client.hset(key, "nonce", candidate_nonce)
        nonce = candidate_nonce
    else:
        nonce = (
            nonce_existing.decode("utf-8")
            if isinstance(nonce_existing, bytes)
            else str(nonce_existing)
        )

    fwd_field = f"fwd:{category}:{normalised}"
    existing: Any = client.hget(key, fwd_field)
    if existing is not None:
        client.expire(key, int(ttl))
        return existing if isinstance(existing, str) else existing.decode("utf-8")
    idx = client.hincrby(key, f"cnt:{category}", 1)
    token = f"<{category}_{nonce}_{idx}>"
    client.hset(key, fwd_field, token)
    client.hset(key, f"rev:{token}", original)
    client.expire(key, int(ttl))
    return token
