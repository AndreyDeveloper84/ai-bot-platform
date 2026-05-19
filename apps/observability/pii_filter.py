"""PII-redacting log filter (DRF-859 / Phase 1 / PI8).

Defence-in-depth on top of the Sentry PII scrubber
(:func:`apps.observability.sentry.scrub_event` — Sprint 8 / E1) and the
Replay regex redactor (:class:`apps.replay.redactor.Redactor` — Sprint 5).

### Why a second layer

The Sentry scrubber only touches Sentry-bound events. The platform also
writes structured JSON logs (J-track, Sprint 8 / J1) to stdout / file /
journald — those bypass Sentry entirely. A misconfigured logger could
leak ``+7 905 123 4567``, ``client@example.com``, or
``4111 1111 1111 1111`` into a log file readable by audit + ops.

152-ФЗ obligation: personal data (phone, email) must not be retained
outside the consented surface. Logs are not a consented surface; this
filter scrubs phone / email / credit-card at *write* time, before any
formatter or handler emits the record.

### Layering with the Sentry scrubber

* **Sentry**: events shipped upstream → Sentry SDK ``before_send`` runs
  :func:`apps.observability.sentry.scrub_event`. We do **not** also wire
  this filter into the Sentry handler — double-redaction risks corrupting
  already-redacted placeholders ("[PHONE]" running through phone regex
  is fine, but the audit story is cleaner if each output path has one
  scrubber).
* **JSON / console / file handlers** (this filter): all persistent
  destinations get :class:`PIIRedactingFilter` so a stray
  ``logger.info("phone: %s", "+7...")`` never reaches disk.

### What is redacted

* **Russian phone numbers** — ``+7``/``8`` prefix + 10 digits with
  optional separators (space, dash, parens). US/international numbers
  are out of scope (low signal in a Russia-only deployment, and the RU
  format catches the bulk of operational chatter).
* **Emails** — standard pattern; conservative on edge cases (e.g. bare
  ``@username`` is not redacted).
* **Credit cards** — 13-19 consecutive digits (optionally split into
  groups of 4 with space/dash separators), validated via Luhn
  *inside the substitution callback*. The Luhn gate is critical: 16-
  digit YClients ``record_id``s would otherwise be redacted on every
  log line and break observability.

### What is NOT redacted (deliberately, in this PR)

* **Names** — NER (e.g. Russian first names like "Лена", "Иван") has
  high false-positive risk because common names overlap with regular
  vocabulary. Deferred to a future filter that can layer on top.
* **Whitelisted opaque IDs** — :data:`_OPAQUE_ID_KEYS` plus any dict
  key ending in ``_id`` or ``_uuid``. These are identifiers, not PII;
  redacting them breaks observability.

### Performance

The hot path is one ``%`` operation away from every log call. Budget:
< 1 ms per record. Optimisations:

* All regexes compiled at import time (class attributes).
* Cheap short-circuit: if the record carries no digit AND no ``@``,
  skip all regex evaluation.
* Credit-card validation lives **inside** the substitution callback so
  non-Luhn 16-digit sequences (the hot path — YClients ``record_id``,
  ``payment_id``) bail out before any replacement allocates.

Benchmark in :mod:`apps.observability.tests.test_pii_filter` asserts
10k records in < 10 s (= < 1 ms each on average).
"""

from __future__ import annotations

import logging
import re
from re import Match
from typing import Any, Final

# ---------------------------------------------------------------------------
# Compiled regex patterns (compiled once at import time).
# ---------------------------------------------------------------------------

# Russian phone numbers: ``+7`` or ``8`` prefix, then 10 digits with optional
# separators. The negative lookbehind/lookahead prevents the pattern from
# eating digits that happen to neighbour a long opaque id.
#
# Matches:
#   +7 (905) 123-45-67    +79051234567    8 905 123 45 67    8-905-123-45-67
# Does NOT match:
#   +1 555 123 4567       (non-RU prefix, scope decision)
#   12345678901           (no +7/8 prefix on the front)
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)"
    r"(?:\+7|8)"
    r"[\s\-]?\(?\s*\d{3}\s*\)?[\s\-]?"
    r"\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"(?!\d)"
)

# Email — RFC-5321-ish. Conservative: requires a non-empty local part, an
# ``@``, a domain with at least one dot, and a TLD of >=2 letters. Bare
# ``@username`` (Telegram mentions) is NOT matched.
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w.+-])"
    r"[A-Za-z0-9._%+\-]+"
    r"@"
    r"[A-Za-z0-9.\-]+"
    r"\.[A-Za-z]{2,}"
    r"(?![\w.\-])"
)

# Credit card: 13-19 digit groups, optional separators. Substitution
# callback Luhn-validates each match — non-Luhn matches are left untouched
# so 16-digit YClients ``record_id`` strings survive.
#
# Anchored with lookarounds so it doesn't slice the middle of a 20-digit
# uuid-fragment / opaque id.
_CREDIT_CARD_RE: Final[re.Pattern[str]] = re.compile(
    # 13-19 digits with optional space/dash separators BETWEEN digits.
    # The trailing ``\d`` keeps the match anchored on a digit so we
    # don't eat the whitespace AFTER the card number (which would turn
    # ``card 4111 1111 1111 1111 declined`` into ``card [CARD]declined``).
    r"(?<!\d)"
    r"\d(?:[\s\-]?\d){12,18}"
    r"(?!\d)"
)

# Cheap short-circuit: if neither a digit nor an "@" appears in the text,
# no phone / email / card can match. Saves three regex passes on the
# common "all-words" log line.
_HAS_PII_CANDIDATE: Final[re.Pattern[str]] = re.compile(r"[\d@]")


# Placeholders. Literal tokens so operators can grep for "[PHONE]" etc.
# in shipped logs.
_PHONE_PLACEHOLDER: Final[str] = "[PHONE]"
_EMAIL_PLACEHOLDER: Final[str] = "[EMAIL]"
_CARD_PLACEHOLDER: Final[str] = "[CARD]"


# Dict-style keyword logging: keys whose VALUES should NOT be redacted.
# These are opaque identifiers — redacting them breaks observability /
# trace-correlation in the log shipper.
_OPAQUE_ID_KEYS: Final[frozenset[str]] = frozenset(
    {
        "tenant_id",
        "trace_id",
        "span_id",
        "conversation_id",
        "bot_user_id",
        "record_id",
        "payment_id",
        "order_id",
        "skill_id",
        "pipeline_step",
    }
)


def _is_opaque_id_key(key: object) -> bool:
    """Return True when ``key`` names a known opaque identifier.

    The check is intentionally permissive: explicit whitelist hits OR any
    string key ending in ``_id`` / ``_uuid`` is treated as opaque.
    """
    if not isinstance(key, str):
        return False
    if key in _OPAQUE_ID_KEYS:
        return True
    return key.endswith("_id") or key.endswith("_uuid")


def _luhn_valid(digits: str) -> bool:
    """Standard Luhn checksum over a pure-digit string.

    Returns True for valid card numbers. Cheap — runs in O(n) over
    13-19 chars. Called on the hot path (per credit-card regex hit).
    """
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    # Iterate right-to-left; double every second digit.
    parity = len(digits) % 2
    for idx, ch in enumerate(digits):
        digit = ord(ch) - 48  # ord('0') == 48; faster than int()
        if digit < 0 or digit > 9:
            return False
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


class PIIRedactingFilter(logging.Filter):
    """Redact phone / email / credit-card from log records before emit.

    Belt-and-suspenders over the Sentry scrubber (which only catches
    Sentry-bound events, not file / stdout / journald logs).

    Whitelisted keys (NOT redacted, treated as opaque identifiers):

      * ``tenant_id``, ``trace_id``, ``conversation_id``, ``bot_user_id``
      * ``record_id`` (YClients), ``payment_id`` (YooKassa)
      * any dict-args key ending in ``_id`` or ``_uuid``

    NOT implemented in this filter:

      * Name redaction (NER) — false-positive risk on common Russian
        first names ("Лена", "Иван") that overlap with regular
        vocabulary. A future NER-based filter can layer on top.

    The filter is destructive: it mutates ``record.msg`` and
    ``record.args`` in place so every handler attached to the logger
    sees the redacted version. It never drops a record — :meth:`filter`
    always returns ``True``.
    """

    # ------------------------------------------------------------------
    # logging.Filter contract
    # ------------------------------------------------------------------

    def filter(self, record: logging.LogRecord) -> bool:
        """Mutate ``record`` in place; always return True.

        Defensive: any unexpected error during redaction is swallowed
        (we never want a filter bug to crash production logging).
        """
        try:
            self._redact_record(record)
        except Exception:  # noqa: BLE001 — filter must never raise
            # A broken filter is worse than no filter: it would crash
            # every log call through this handler. Swallow and pass.
            pass
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _redact_record(self, record: logging.LogRecord) -> None:
        """Apply redaction to ``record.msg`` and ``record.args``."""
        # 1. record.msg — the format string or pre-formatted message.
        if isinstance(record.msg, str):
            record.msg = self._redact(record.msg)

        # 2. record.args — tuple of positional values OR dict of kwargs.
        args = record.args
        if args is None:
            return

        if isinstance(args, dict):
            new_dict: dict[str, Any] = {}
            for key, value in args.items():
                if _is_opaque_id_key(key):
                    new_dict[key] = value
                elif isinstance(value, str):
                    new_dict[key] = self._redact(value)
                else:
                    new_dict[key] = value
            record.args = new_dict  # type: ignore[assignment]
            return

        if isinstance(args, tuple):
            # Tuple args have no key info — every string element is
            # redacted unconditionally.
            redacted_tuple = tuple(self._redact(v) if isinstance(v, str) else v for v in args)
            record.args = redacted_tuple
            return

        # ``record.args`` should be tuple-or-dict-or-None per stdlib
        # contract, but be defensive: leave anything else untouched.

    def _redact(self, text: str) -> str:
        """Apply every pattern to a single string. Idempotent.

        Short-circuits on the common "no digit, no @" case to avoid
        three regex passes on every log line.
        """
        if not text:
            return text
        if not _HAS_PII_CANDIDATE.search(text):
            return text

        # Order: credit card first (greediest digit run), then phone,
        # then email. Each pattern runs once via re.sub — no findall/
        # replace loops.
        text = _CREDIT_CARD_RE.sub(_sub_credit_card, text)
        text = _PHONE_RE.sub(_PHONE_PLACEHOLDER, text)
        text = _EMAIL_RE.sub(_EMAIL_PLACEHOLDER, text)
        return text


# ---------------------------------------------------------------------------
# Module-level substitution callbacks (avoid per-call closure allocation).
# ---------------------------------------------------------------------------


def _sub_credit_card(match: Match[str]) -> str:
    """Replace a Luhn-valid digit run with ``[CARD]``; pass through otherwise.

    The regex matches any 13-19 digit group separated by space/dash,
    but only Luhn-valid sequences are actually credit cards. 16-digit
    YClients ``record_id`` values would otherwise be redacted on every
    log line, which we explicitly want to avoid.
    """
    raw = match.group(0)
    digits = re.sub(r"[\s\-]", "", raw)
    if _luhn_valid(digits):
        return _CARD_PLACEHOLDER
    return raw
