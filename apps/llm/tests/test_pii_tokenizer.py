"""PII tokenizer tests (Phase E / 152-ФЗ Tier-A pre-pilot).

Covers the 6 Phase B verdicts:
  1. Per-conversation scope — different conv_id = fresh namespace.
  2. Regex-only detection — phone/email/cc/otp/url_token catch; names skip.
  3. 5 categories from existing :mod:`apps.replay.redactor` patterns.
  4. Same-token-per-entity (normalised) — coreference preserved.
  5. Defensive reverse — hallucinated tokens skipped + logged.
  6. Redis ephemeral — TTL bumped on every write; clear_conversation drops.

Uses a `_FakeRedis` HASH-only stub mirroring the pattern from
``apps.orchestrator.memory.tests.test_short_term``. No live Redis
needed; tests pin contract behaviour rather than Redis itself.
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from apps.llm import pii_tokenizer


# ---------------------------------------------------------------------------
# Fake Redis HASH surface
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory Redis HASH + register_script surface.

    Supports the operations pii_tokenizer.py exercises:
      * HGET / HSET / HINCRBY (atomic via single-threaded test process)
      * EXPIRE (records TTL без enforcing — tests assert it was called)
      * DELETE
      * register_script(lua) → callable executing the Lua via Python

    The Lua script is interpreted in Python; only the EXACT script
    pii_tokenizer ships is supported (not a general Lua VM).
    """

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}

    # Hash ops -----------------------------------------------------------
    def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    def hset(self, key: str, field: str, value: str) -> int:
        h = self.hashes.setdefault(key, {})
        is_new = field not in h
        h[field] = value
        return 1 if is_new else 0

    def hincrby(self, key: str, field: str, amount: int) -> int:
        h = self.hashes.setdefault(key, {})
        current = int(h.get(field, "0"))
        new = current + amount
        h[field] = str(new)
        return new

    def expire(self, key: str, ttl: int) -> int:
        if key in self.hashes:
            self.ttls[key] = ttl
            return 1
        return 0

    def delete(self, key: str) -> int:
        removed = 0
        if key in self.hashes:
            del self.hashes[key]
            removed = 1
        self.ttls.pop(key, None)
        return removed

    # Script support -----------------------------------------------------
    def register_script(self, script: str) -> "_FakeScript":
        return _FakeScript(self, script)


class _FakeScript:
    """Python-side interpretation of pii_tokenizer._GET_OR_CREATE_LUA.

    Hard-coded к the exact script body — guard against drift via
    test_lua_script_body_matches_expected.
    """

    def __init__(self, fake: _FakeRedis, script: str) -> None:
        self._fake = fake
        self._script = script

    def __call__(self, *, keys: list[str], args: list[str | int]) -> list[str]:
        key = keys[0]
        category, normalised, original, ttl, candidate_nonce = args
        # HSETNX simulation для nonce.
        h = self._fake.hashes.setdefault(key, {})
        if "nonce" not in h:
            h["nonce"] = candidate_nonce
        nonce = h["nonce"]
        fwd_field = f"fwd:{category}:{normalised}"
        existing = self._fake.hget(key, fwd_field)
        if existing:
            self._fake.expire(key, int(ttl))
            return [existing, nonce]
        cnt_field = f"cnt:{category}"
        idx = self._fake.hincrby(key, cnt_field, 1)
        token = f"<{category}_{nonce}_{idx}>"
        self._fake.hset(key, fwd_field, token)
        self._fake.hset(key, f"rev:{token}", original)
        self._fake.expire(key, int(ttl))
        return [token, nonce]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
    pii_tokenizer._invalidate_script_cache()
    yield fake
    pii_tokenizer._invalidate_script_cache()


@pytest.fixture
def conv_id() -> str:
    return str(uuid4())


# ---------------------------------------------------------------------------
# Token-pattern helpers (per-conversation NONCE makes plain `<PHONE_1>`
# string assertions fail — we now match the format
# `<{CAT}_{NONCE_HEX_8}_{INDEX}>` via regex.
# ---------------------------------------------------------------------------


import re as _re  # noqa: E402


def _token_re(category: str, index: int) -> _re.Pattern[str]:
    return _re.compile(rf"<{category}_[0-9a-f]+_{index}>")


def _has_token(text: str, category: str, index: int) -> bool:
    return bool(_token_re(category, index).search(text))


def _count_token(text: str, category: str, index: int) -> int:
    return len(_token_re(category, index).findall(text))


def _extract_token(text: str, category: str, index: int) -> str | None:
    m = _token_re(category, index).search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Category coverage — regex patterns reused from apps.replay.redactor
# ---------------------------------------------------------------------------


class TestPatternCoverage:
    """All 5 Phase B categories detect and tokenize."""

    def test_phone_russian_plus_format(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("Звоните +7 (495) 123-45-67 завтра", conv_id)
        assert _has_token(out, "PHONE", 1)
        assert "+7" not in out
        assert "завтра" in out  # Non-PII context preserved.

    def test_phone_russian_8_format(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("8 800 123 45 67 — горячая линия", conv_id)
        assert _has_token(out, "PHONE", 1)

    def test_email_simple(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("Напиши на anna@example.ru", conv_id)
        assert _has_token(out, "EMAIL", 1)
        assert "anna@example.ru" not in out

    def test_credit_card(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("Карта 4111 1111 1111 1111", conv_id)
        assert _has_token(out, "CC", 1)

    def test_otp_six_digit(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("Код: 123456", conv_id)
        assert _has_token(out, "OTP", 1)

    def test_otp_four_digit(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("PIN 4321 для входа", conv_id)
        assert _has_token(out, "OTP", 1)

    def test_url_token(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize(
            "Ссылка https://example.com/x?token=secret123abc — тут.", conv_id
        )
        assert _has_token(out, "URL_TOKEN", 1)
        assert "secret123abc" not in out

    def test_no_pii_passes_through(self, fake_redis, conv_id):
        text = "Привет, как дела? Хочу записаться на массаж."
        assert pii_tokenizer.tokenize(text, conv_id) == text

    def test_empty_string(self, fake_redis, conv_id):
        assert pii_tokenizer.tokenize("", conv_id) == ""

    def test_russian_names_NOT_tokenized(self, fake_redis, conv_id):
        """Phase B verdict D2=A — names skip in regex-only Phase 0.
        Documented gap; natasha NER lands в Phase 1."""
        text = "Записать Анну Иванову на завтра"
        assert pii_tokenizer.tokenize(text, conv_id) == text


# ---------------------------------------------------------------------------
# Idempotency — same entity → same token (D4 verdict)
# ---------------------------------------------------------------------------


class TestSameTokenPerEntity:
    """Phase B D4: normalised same-entity lookup."""

    def test_phone_same_format_repeated_gets_same_token(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize(
            "Позвоню на +7 999 123 45 67. Подтверди +7 999 123 45 67.", conv_id
        )
        assert _count_token(out, "PHONE", 1) == 2
        # Second phone NOT minted.
        assert not _has_token(out, "PHONE", 2)

    def test_phone_different_format_normalised_same(self, fake_redis, conv_id):
        """+7 999 123 45 67 и 8 (999) 123 4567 → same digits → same token."""
        out = pii_tokenizer.tokenize("Мой +7 999 123 45 67, ещё 8 (999) 123 4567 — это я.", conv_id)
        # PHONE_RE may capture both in the same single normalised form.
        # The PHONE_1 token should appear at least once; PHONE_2 must NOT
        # appear, because the digit-normalised key collapses both formats.
        assert _has_token(out, "PHONE", 1)
        assert not _has_token(out, "PHONE", 2)

    def test_different_phones_get_different_tokens(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize(
            "Звонок на +7 999 123 45 67. Перевести на +7 888 234 56 78.", conv_id
        )
        assert _has_token(out, "PHONE", 1)
        assert _has_token(out, "PHONE", 2)

    def test_email_case_insensitive_collapse(self, fake_redis, conv_id):
        out = pii_tokenizer.tokenize("Anna@Example.ru и anna@example.ru — одна почта.", conv_id)
        assert _has_token(out, "EMAIL", 1)
        assert not _has_token(out, "EMAIL", 2)


# ---------------------------------------------------------------------------
# Per-conversation scope (D1 verdict)
# ---------------------------------------------------------------------------


class TestConversationScope:
    def test_different_conversations_get_independent_namespaces(self, fake_redis):
        conv_a = str(uuid4())
        conv_b = str(uuid4())
        out_a = pii_tokenizer.tokenize("Мой email anna@example.ru", conv_a)
        out_b = pii_tokenizer.tokenize("Мой email boris@example.ru", conv_b)
        # Both get EMAIL_*_1 — independent counters per conversation.
        assert _has_token(out_a, "EMAIL", 1)
        assert _has_token(out_b, "EMAIL", 1)

    def test_same_entity_across_conversations_independent_tokens(self, fake_redis):
        conv_a = str(uuid4())
        conv_b = str(uuid4())
        pii_tokenizer.tokenize("anna@example.ru", conv_a)
        # Conversation A's namespace is unaware of conv B's reuse.
        out_b = pii_tokenizer.tokenize("anna@example.ru and ben@example.ru", conv_b)
        # Conv B has its own counter starting at 1.
        assert _has_token(out_b, "EMAIL", 1)
        assert _has_token(out_b, "EMAIL", 2)


# ---------------------------------------------------------------------------
# Reverse substitution (D5 verdict — defensive)
# ---------------------------------------------------------------------------


class TestDetokenize:
    def test_round_trip_single(self, fake_redis, conv_id):
        original = "Напиши anna@example.ru"
        tokenized = pii_tokenizer.tokenize(original, conv_id)
        # Simulate LLM response echoing the token.
        llm_response = f"Хорошо, напишу на {tokenized.split()[-1]} сегодня."
        recovered = pii_tokenizer.detokenize(llm_response, conv_id)
        assert "anna@example.ru" in recovered

    def test_hallucinated_token_left_intact_with_warning(self, fake_redis, conv_id, caplog):
        """LLM emits a token-shaped string AFTER nonce has been seeded
        (i.e. tokenmap exists, but the specific token isn't in rev).
        Hallucinated token preserved verbatim, WARNING logged."""
        # Seed nonce by tokenizing one real PII item.
        pii_tokenizer.tokenize("+7 999 123 45 67", conv_id)
        nonce = fake_redis.hashes[pii_tokenizer._key(conv_id)]["nonce"]
        with caplog.at_level(logging.WARNING, logger="apps.llm.pii_tokenizer"):
            out = pii_tokenizer.detokenize(f"Звоню на <PHONE_{nonce}_99> сегодня.", conv_id)
        assert f"<PHONE_{nonce}_99>" in out
        assert any("hallucinated_token" in r.message for r in caplog.records)

    def test_mutated_token_format_not_substituted(self, fake_redis, conv_id):
        """LLM emits the token с whitespace — not a known token format."""
        pii_tokenizer.tokenize("+7 999 123 45 67", conv_id)
        nonce = fake_redis.hashes[pii_tokenizer._key(conv_id)]["nonce"]
        out = pii_tokenizer.detokenize(f"Звоню на < PHONE_{nonce}_1 > сегодня.", conv_id)
        # Mutated token left as-is; raw phone NOT leaked.
        assert "+7 999 123 45 67" not in out
        assert f"< PHONE_{nonce}_1 >" in out

    def test_lowercase_token_not_substituted(self, fake_redis, conv_id):
        pii_tokenizer.tokenize("anna@example.ru", conv_id)
        nonce = fake_redis.hashes[pii_tokenizer._key(conv_id)]["nonce"]
        out = pii_tokenizer.detokenize(f"Email <email_{nonce}_1>", conv_id)
        # Case-sensitive — lowercase variant NOT substituted.
        assert "anna@example.ru" not in out

    def test_phone_1_does_not_clobber_phone_10(self, fake_redis, conv_id):
        """Sort by length DESC: PHONE_10 substituted before PHONE_1."""
        text = " ".join(f"+7 999 000 00 {i:02d}" for i in range(1, 11))
        tokenized = pii_tokenizer.tokenize(text, conv_id)
        # Both PHONE_1 AND PHONE_10 live in the same string (nonce-keyed).
        assert _has_token(tokenized, "PHONE", 1)
        assert _has_token(tokenized, "PHONE", 10)
        tok_1 = _extract_token(tokenized, "PHONE", 1)
        tok_10 = _extract_token(tokenized, "PHONE", 10)
        llm_out = f"Перезвоню на {tok_1} или {tok_10}."
        recovered = pii_tokenizer.detokenize(llm_out, conv_id)
        assert "+7 999 000 00 01" in recovered
        assert "+7 999 000 00 10" in recovered

    def test_user_supplied_literal_token_shape_does_not_leak(self, fake_redis, conv_id):
        """ADVERSARIAL (Phase G): nonce defence — user types `<PHONE_5>`
        literally; even if conversation has 5 real phones, detokenize
        cannot match without nonce → no leak."""
        for i in range(1, 6):
            pii_tokenizer.tokenize(f"+7 999 000 00 {i:02d}", conv_id)
        # User-supplied literal shape (no nonce).
        out = pii_tokenizer.detokenize("Что значит <PHONE_5>?", conv_id)
        # Real 5th phone digits MUST NOT appear.
        assert "+7 999 000 00 05" not in out
        # Literal user shape preserved.
        assert "<PHONE_5>" in out

    def test_no_tokens_in_text_returns_unchanged(self, fake_redis, conv_id):
        assert (
            pii_tokenizer.detokenize("Просто текст без токенов.", conv_id)
            == "Просто текст без токенов."
        )

    def test_empty_string(self, fake_redis, conv_id):
        assert pii_tokenizer.detokenize("", conv_id) == ""


# ---------------------------------------------------------------------------
# Ephemeral storage (D6 verdict)
# ---------------------------------------------------------------------------


class TestEphemeralStorage:
    def test_ttl_set_on_every_tokenize_write(self, fake_redis, conv_id):
        pii_tokenizer.tokenize("anna@example.ru", conv_id)
        key = pii_tokenizer._key(conv_id)
        assert key in fake_redis.ttls
        assert fake_redis.ttls[key] > 0

    def test_ttl_refreshed_on_repeat_entity_lookup(self, fake_redis, conv_id):
        """Lua script EXPIRE call refreshes TTL even on cache hit."""
        pii_tokenizer.tokenize("anna@example.ru", conv_id)
        key = pii_tokenizer._key(conv_id)
        # Tamper TTL down to simulate aging.
        fake_redis.ttls[key] = 60
        # Second tokenize of same entity should reset TTL.
        pii_tokenizer.tokenize("anna@example.ru", conv_id)
        assert fake_redis.ttls[key] > 60

    def test_clear_conversation_removes_map(self, fake_redis, conv_id):
        pii_tokenizer.tokenize("anna@example.ru", conv_id)
        key = pii_tokenizer._key(conv_id)
        assert key in fake_redis.hashes
        pii_tokenizer.clear_conversation(conv_id)
        assert key not in fake_redis.hashes

    def test_clear_conversation_idempotent_on_missing_key(self, fake_redis, conv_id):
        # Never tokenized — key absent. Clear should be no-op без raise.
        pii_tokenizer.clear_conversation(conv_id)


# ---------------------------------------------------------------------------
# Fallback path (no register_script support)
# ---------------------------------------------------------------------------


class _FakeRedisNoScript(_FakeRedis):
    """Fake без register_script — exercises the non-Lua fallback path."""

    register_script = None  # type: ignore[assignment]


class TestFallbackPath:
    """When redis-py client lacks register_script, fallback HGET + HSET works."""

    def test_fallback_emits_token(self, monkeypatch, conv_id):
        fake = _FakeRedisNoScript()
        monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
        pii_tokenizer._invalidate_script_cache()
        out = pii_tokenizer.tokenize("+7 999 123 45 67", conv_id)
        assert _has_token(out, "PHONE", 1)

    def test_fallback_idempotent_repeat(self, monkeypatch, conv_id):
        fake = _FakeRedisNoScript()
        monkeypatch.setattr(pii_tokenizer, "_redis_client", lambda: fake)
        pii_tokenizer._invalidate_script_cache()
        out = pii_tokenizer.tokenize("+7 999 123 45 67. И ещё +7 999 123 45 67.", conv_id)
        assert _count_token(out, "PHONE", 1) == 2
        assert not _has_token(out, "PHONE", 2)


# ---------------------------------------------------------------------------
# Lua script body integrity guard
# ---------------------------------------------------------------------------


class TestLuaScriptBody:
    """Guard against silent drift in the Lua script — if the body changes,
    the _FakeScript interpreter is no longer faithful, and tests would
    pass while production breaks."""

    def test_lua_script_body_unchanged(self):
        # Sanity check on script text. Update this assertion + _FakeScript
        # together when the script changes. Nonce defence (Phase G)
        # requires HSETNX seed; check that op is present too.
        body = pii_tokenizer._GET_OR_CREATE_LUA
        assert 'redis.call("HSETNX"' in body  # Nonce atomic seed.
        assert 'redis.call("HGET"' in body
        assert 'redis.call("HINCRBY"' in body
        assert 'redis.call("HSET"' in body
        assert 'redis.call("EXPIRE"' in body
        # Lua returns 2-element table {token, nonce} — guard contract.
        assert "return {existing, nonce}" in body
        assert "return {token, nonce}" in body
