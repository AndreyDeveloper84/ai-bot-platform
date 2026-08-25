"""PII redactor — regex layer (DRF-507/503/504/571 / Sprint 5 + 7).

Per PHASE0_DESIGN §7.2 Phase 0 ships **regex-only** redaction;
Russian NER layer (natasha) deferred to Phase 1.

### Why pinned regex constants

The patterns + placeholder tokens are part of the persistence
contract: a row stamped `redaction_method="regex_v1"` is committing
that *exactly these patterns* ran on it. If we change a pattern, we
must bump the version string so a future migration can re-redact
v1 rows (with raw-text access from another source if available).

### Why a class, not free functions

Two reasons:
1. Allowlist lookup is per-redactor-instance — settings can change
   between test runs without leaking allowlist entries between
   tests.
2. ``redact_steps`` is a recursive walk over nested dicts/lists;
   keeping it on a class keeps the call-chain readable
   (``self._redact_value`` self-recursion).

### KB chunk coverage (Sprint 7 / K13 / DRF-571 — 152-ФЗ compliance)

The Sprint 7 FAQ skill (F2 / DRF-589) emits ``SkillResult.tool_calls_made``
with ``args.chunks`` (list of strings) and ``args.retrieved_chunks``
(list of dicts with ``text`` / ``metadata`` keys) — KB content that
may include master phones, salon addresses, or contact emails.

The recursive walk in :meth:`Redactor.redact_steps` already reaches
those nested paths via :meth:`_redact_value` (dict → list → string).
K13 (DRF-571) adds explicit test coverage for that flow and
documents the structure here so future code that adds new
chunk-bearing keys stays in scope.

**Paths the recursive walk covers when KB tool_calls land in
``pipeline_steps``:**

* ``step["tool_calls_made"][*]["args"]["chunks"][*]`` — raw chunk text
* ``step["tool_calls_made"][*]["args"]["retrieved_chunks"][*]["text"]``
* ``step["tool_calls_made"][*]["args"]["retrieved_chunks"][*]["metadata"]["source_uri"]``
* ``step["tool_calls_made"][*]["result"]["hits"][*]["text"]``

If a future skill adds a new key carrying chunk text, no code change
is needed — the walker is structural — but new tests should pin
the contract.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# --- Pinned patterns (redaction_method = "regex_v1") ----------------------

REDACTION_METHOD = "regex_v1"

# Phone: +7/8/+anything followed by 10 digits, optionally with spaces / dashes
# / parens. Catches:
#   +7 (495) 123-45-67   8 800 123 45 67   +12345678901
#
# The boundary guards exclude ASCII letters as well as digits (DRF-1382).
# ``(?<!\d)`` alone let the pattern open a match inside a hex identifier,
# because the neighbouring character there is a letter, not a digit:
#
#   c4202567-6706-417c-...  ->  c[PHONE]-417c-...
#
# Measured at 3.12% of random canonical UUIDs and 7.98% of 32-char hex ids
# — roughly one identifier in thirty. A trace with its ``trace_id`` middle
# removed cannot be joined to the log or to the DB row, which is the whole
# reason the trace was kept.
#
# The regex is NOT relaxed: a missed phone in a trace stays worse than a
# mangled identifier, so the body is byte-for-byte what it was and only the
# boundary tightened, from "not a digit" to "not a digit and not an ASCII
# letter". A phone number is never written flush against an ASCII letter;
# non-ASCII letters are still allowed on both sides, so a number abutting
# Cyrillic text is caught exactly as before.
#
# Does NOT match:
#   3f2a84113328793b   (digit run welded to ASCII letters — an id)
PHONE_RE = re.compile(
    r"(?<![\dA-Za-z])"
    r"(?:\+?\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"(?![\dA-Za-z])"
)

# Email: simple RFC-5322 lite. Don't try to validate, just match the shape.
EMAIL_RE = re.compile(r"[\w\.\-+]+@[\w\-]+\.[\w]{2,}")

# Credit card: 13-19 digit groups separated by space/dash.
#
# Same boundary tightening as PHONE_RE above, for the same reason
# (DRF-1382): ``(?<!\d)`` alone sliced 2.20% of random canonical UUIDs and
# 2.17% of 32-char hex ids.
#
#   c4202567-6706-417c-...  ->  c[CC]c-...
#
# Matches are Luhn-gated in :func:`_is_card_number` (DRF-1382) — see
# there for why the "too expensive for redactor hot path" comment that
# used to sit on this line did not survive being measured.
CC_RE = re.compile(
    r"(?<![\dA-Za-z])"
    r"(?:\d[\s\-]?){13,19}"
    r"(?![\dA-Za-z])"
)

# OTP: standalone 4- or 6-digit sequences (not embedded in longer numbers).
# Negative lookbehind+lookahead prevent matching the inside of phone numbers
# (those are already matched by PHONE_RE).
#
# MEASURED AND DELIBERATELY NOT CHANGED HERE (DRF-1382).
#
# ``\w`` already excludes letters on both sides, so this pattern never
# opened inside a hex run the way PHONE_RE and CC_RE did. It has a
# different hole: a dash is not ``\w``, and the middle groups of a
# canonical UUID are exactly four characters long between two dashes.
# Whenever such a group happens to be all digits, it is redacted:
#
#   c4202567-6706-417c-...  ->  c4202567-[OTP]-417c-...
#
# Measured on 200 000 random identifiers: **44.07%** of canonical UUIDs
# (0% of dash-free 32-char hex ids — the shape is the whole cause). That
# is an order of magnitude worse than the 3.12% / 2.20% this ticket was
# opened for, and it is the dominant remaining reason a replay trace_id
# comes out of this file unsearchable.
#
# It is left alone on purpose. Closing it means adding ``-`` to the
# boundary class, and unlike the ASCII-letter guard above that is NOT
# free: it stops redacting a code written as ``код-1234`` and it drops
# both numbers in a dash-joined pair. That is a decision about which
# direction of error to accept, not a mechanical tightening, so it gets
# its own ticket and its own measurement rather than riding along here.
OTP_RE = re.compile(r"(?<![\w\d])\d{4}(?![\w\d])|(?<![\w\d])\d{6}(?![\w\d])")

# URLs with sensitive query params: ?token= / ?key= / ?secret= / ?auth=
# Captures the URL up to the next whitespace.
URL_TOKEN_RE = re.compile(
    r"https?://\S*?[?&](?:token|key|secret|auth|api_key|access_token)=\S+",
    re.IGNORECASE,
)


_NON_DIGIT_RE = re.compile(r"[^\d]")


def _luhn_valid(digits: str) -> bool:
    """Standard Luhn checksum over a pure-digit string.

    Same implementation as :func:`apps.observability.pii_filter._luhn_valid`
    — kept local rather than imported so ``apps.replay`` does not grow a
    dependency on ``apps.observability`` for four lines of arithmetic.
    """

    n = len(digits)
    if n < 13 or n > 19:
        return False
    total = 0
    # Iterate right-to-left; double every second digit.
    parity = n % 2
    for idx, ch in enumerate(digits):
        digit = ord(ch) - 48  # ord("0") == 48; faster than int()
        if digit < 0 or digit > 9:
            return False
        if idx % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _is_card_number(matched: str) -> bool:
    """Luhn gate for :data:`CC_RE` matches (DRF-1382).

    The comment this replaced said a Luhn check was "too expensive for
    redactor hot path". That claim had never been measured. It has been
    now, on this file's own hot path — ``Redactor.redact_text`` over
    50 000 realistic replay step strings — and it is wrong:

    ============================================  =========  =========  ======
    corpus                                        no Luhn    Luhn       delta
    ============================================  =========  =========  ======
    realistic replay step text                    88.8 us    95.3 us    +7.3%
    worst case (13-19 digit run in every string)  111.5 us   147.7 us   +32.4%
    ============================================  =========  =========  ======

    Read the ratios, not the absolutes — the benchmark host is slow (an
    empty Python loop iteration measures 0.137 us there). Two ratios
    settle it:

    * the checksum costs **12.4 us per call** on a host where
      :data:`EMAIL_RE` costs **51.3 us** on the same string. Luhn is a
      quarter of the price of a regex nobody has ever questioned.
    * most of the worst-case +32% is not the checksum at all. It is
      downstream: digits the gate spares stay in the string, so
      :data:`PHONE_RE` and :data:`OTP_RE` then have something to scan.

    The gate lives **inside** the substitution callback, so it runs only
    once :data:`CC_RE` has already matched. Text with no 13-19 digit run
    pays literally nothing.

    What it buys, measured on 200 000 random identifiers:

    * canonical UUIDs sliced by :data:`CC_RE`: 0.1395% -> **0.0125%**
    * 16-char hex ids: 0.060% -> **0.0035%**
    * and, the reason that matters more than either: a purely numeric
      **14-19 digit** string — an order number, a YClients
      ``record_id``, a nanosecond timestamp — went from **0% surviving**
      a trace to **~90%** (measured per length, 5 000 samples each).
      Before the gate every one of them came out as ``[CC]``.

    Note the lower bound. A **13**-digit run — an epoch-ms timestamp —
    is still redacted, because :data:`PHONE_RE` accepts 10-13 digits and
    runs straight after :data:`CC_RE`. ``ts=1756080000000`` is now
    ``ts=[PHONE]`` instead of ``ts=[CC]``, which is no better for the
    reader. The Luhn gate does not reach that case and is not claimed
    to; it is pinned in ``TestLuhnGate`` so the claim stays honest.

    ### The price, named

    Luhn is not free of risk, and the risk is not CPU. ``re.sub`` does
    not retry a shorter match after the callback declines one, so a card
    welded to a short neighbouring number by **exactly one** space or
    dash is now missed where the blanket redaction caught it:

        заказ 99 4111111111111111   ->  unredacted  (was "заказ [CC]")

    It is confined to that shape. The neighbour must be **1-3 digits**,
    so the combined run is 17-19 digits and still inside ``{13,19}``. At
    4 or more the combined run overflows the quantifier, the engine
    backtracks onto the card alone, and it is redacted normally — as it
    is with any two separators, a comma, or a word in between.

    This is a real step in the direction the ticket calls the worse one
    (a missed number beats no mangled id), and it is taken deliberately:
    the loss it removes is certain and systematic (every timestamp,
    every long id, on every trace), the loss it adds is rare and
    characterised. Closing it needs a different mechanism than a
    checksum — re-testing card-length windows inside a rejected run —
    which costs on the *failure* path, i.e. the common one. Tracked
    separately rather than papered over; pinned in
    ``TestLuhnGateKnownMiss`` so it cannot change silently.
    """

    return _luhn_valid(_NON_DIGIT_RE.sub("", matched))


# (pattern, placeholder, guard). ``guard`` is an optional predicate over the
# matched text: return False to leave the match alone. Only CC uses one.
_PATTERNS: list[tuple[re.Pattern[str], str, Callable[[str], bool] | None]] = [
    # Order matters: URL_TOKEN first (contains everything else), then CC
    # (greedy on digit sequences), then PHONE, then EMAIL, then OTP.
    (URL_TOKEN_RE, "[URL_TOKEN]", None),
    (CC_RE, "[CC]", _is_card_number),
    (PHONE_RE, "[PHONE]", None),
    (EMAIL_RE, "[EMAIL]", None),
    (OTP_RE, "[OTP]", None),
]


class Redactor:
    """Single-pass regex redactor with allowlist support.

    Read settings.REPLAY_REDACTION_ALLOWLIST at construction time —
    each instance is bound to a snapshot of the allowlist so test
    isolation is automatic (build a new Redactor in the test fixture
    after overriding settings).
    """

    def __init__(self, allowlist: list[str] | None = None) -> None:
        """Initialize with explicit allowlist or pull from settings.

        Args:
          allowlist: Override for testing. None → read from
            ``settings.REPLAY_REDACTION_ALLOWLIST``.
        """

        raw = (
            allowlist
            if allowlist is not None
            else list(getattr(settings, "REPLAY_REDACTION_ALLOWLIST", []))
        )
        # Pre-build set of literal strings for O(1) "is this token in
        # the allowlist" check.
        self._allowlist: set[str] = {a for a in raw if a}

    # --- Public API -------------------------------------------------------

    def redact_text(self, text: str) -> str:
        """Single-pass replacement of every PII pattern with placeholders.

        Allowlist matches are restored after replacement — we redact
        first, then check the redaction span against the allowlist; if
        the original span was an exact allowlist entry, we keep the
        original.

        Idempotent: a second call on the output is a no-op because the
        placeholder tokens don't match any of the patterns.
        """

        if not text:
            return text

        result = text
        for pattern, placeholder, guard in _PATTERNS:
            result = self._replace_with_allowlist(pattern, placeholder, result, guard)
        return result

    def redact_steps(self, steps: list[Any]) -> list[Any]:
        """Recursive walk over pipeline steps — redacts every str leaf.

        Preserves non-string leaves (ints, floats, None, bools)
        unchanged.
        """

        return [self._redact_value(s) for s in steps]

    def redact_value(self, value: Any) -> Any:
        """Public single-value recursive redaction.

        Same recursive walk as :meth:`redact_steps`, but accepts an
        arbitrary value (dict / list / scalar) instead of a list of
        step snapshots. Sprint 8 / E1 (Sentry ``before_send`` hook)
        consumes this on whole event payloads — keeping the entry
        point public is the SOLID / LSP fix from the Sprint 8 code
        review (was reaching into ``_redact_value`` directly).
        """
        return self._redact_value(value)

    # --- Internals --------------------------------------------------------

    def _replace_with_allowlist(
        self,
        pattern: re.Pattern[str],
        placeholder: str,
        text: str,
        guard: Callable[[str], bool] | None = None,
    ) -> str:
        """Apply `pattern` replacement honoring `guard` and the allowlist.

        `guard` runs first and is the cheap bail-out: a match it rejects
        is left untouched without allocating a replacement.
        """

        def _sub(match: re.Match[str]) -> str:
            original = match.group(0)
            if guard is not None and not guard(original):
                return original
            if original in self._allowlist:
                return original
            return placeholder

        return pattern.sub(_sub, text)

    def _redact_value(self, value: Any) -> Any:
        """Recursive dispatch on type."""

        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {k: self._redact_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._redact_value(v) for v in value]
        # int, float, None, bool, etc. — preserved unchanged.
        return value
