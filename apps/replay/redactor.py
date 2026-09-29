"""PII redactor — regex layer (DRF-507/503/504/571 / Sprint 5 + 7).

Per PHASE0_DESIGN §7.2 Phase 0 ships **regex-only** redaction;
Russian NER layer (natasha) deferred to Phase 1.

### Why pinned regex constants

The patterns + placeholder tokens are part of the persistence
contract: a row stamped `redaction_method="regex_v3"` is committing
that *exactly these patterns* ran on it. If we change a pattern, we
must bump the version string so a future migration can re-redact
older rows (with raw-text access from another source if available).

**v1 -> v2 (DRF-1382).** ``PHONE_RE`` and ``CC_RE`` had their boundary
guards tightened to exclude ASCII letters, and ``CC_RE`` gained a Luhn
gate. The version string is bumped because this file says it must be:
a v1 row ran under patterns that sliced 3.12% / 2.20% of identifiers,
and that is a different contract from what a v2 row committed to.

**v2 -> v3 (DRF-1389 / DRF-1390).** Two boundary defects, both of them
named in v2's own comments and left for their own tickets, are closed:

* ``OTP_RE`` no longer redacts the middle group of a canonical UUID.
  A v2 row carries UUIDs that were damaged 43.91% of the time; a v3
  row does not. This is the difference the stamp exists to record.
* ``CC_RE`` matches that fail the Luhn gate are re-searched for a card
  welded to a short neighbouring number. A v2 row could carry an
  unredacted card number in that shape (67.15% of them, measured); a
  v3 row cannot.

Re-redaction cannot repair v1 or v2 rows — the removed digits are gone
and this file never saw the raw text twice — so the stamp's only job
here is to let a reader tell "this trace_id is intact" from "this
trace_id may have had its middle cut out", and "this row was scanned
for welded cards" from "this row was not". That distinction is exactly
what a row-level version string is for.

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

# --- Pinned patterns (redaction_method = "regex_v3") ----------------------

REDACTION_METHOD = "regex_v3"

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
# CLOSED HERE (DRF-1389). Measured before: **43.91%** of canonical UUIDs
# came out of this file damaged, 43.72% of them by this pattern alone
# (200 000 random uuid4, 0% of dash-free 32-char hex ids — the shape was
# the whole cause). ``\w`` already excluded letters on both sides, so
# this pattern never opened inside a hex run the way PHONE_RE and CC_RE
# did; it had a different hole. A dash is not ``\w``, and the middle
# groups of a canonical UUID are exactly four characters long between
# two dashes, so an all-digit group looked like a standalone code:
#
#   c4202567-6706-417c-...  ->  c4202567-[OTP]-417c-...
#
# ### Why not simply add ``-`` to the boundary class
#
# That is the wide fix, and it is not free: it stops redacting a code
# written as ``код-1234`` or ``OTP-123456``, and it drops both numbers
# in a dash-joined pair. This file's own rule is that a missed number in
# a trace is worse than a mangled id (see PHONE_RE above), so buying an
# id back with a leaked code is the wrong trade.
#
# The narrow fix rejects one shape instead: four digits standing between
# two dashes that each have **four hex characters** on their far side —
# which is what a canonical UUID puts around every one of its middle
# groups, and what ordinary prose essentially never produces.
#
#   ...aaaa-1234-bbbb...   rejected (UUID-shaped neighbourhood)
#   код-1234              redacted (nothing after the digits)
#   OTP-123456             redacted (six digits, and "OTP" is not hex)
#   code-1234-ab           redacted ("code" has a non-hex "o"; "ab" is
#                          two characters, not four)
#
# ### What the narrow fix does NOT catch, named
#
# A genuine four-digit code written between two dashes with four hex
# characters on either side -- ``face-1234-beef``, ``abcd-1234-ef01`` --
# is no longer redacted. That is identifier-shaped text, not prose, and
# it is the whole price: the corpus in
# ``TestNoOtpFormStoppedBeingRedacted`` (код 1234 / код: 1234 /
# код-1234 / OTP-123456 / code=1234 / (1234) / [1234] / bare 1234 and
# 123456) is redacted 20 of 20 before this change and 20 of 20 after.
#
# The ``\d{6}`` alternative is left untouched: a canonical UUID has no
# six-character group, so it was never part of this defect.
_UUID_MIDDLE_GROUP = r"(?<=[0-9a-fA-F]{4}-)\d{4}-[0-9a-fA-F]{4}"

OTP_RE = re.compile(
    r"(?<![\w\d])(?!" + _UUID_MIDDLE_GROUP + r")\d{4}(?![\w\d])"
    r"|(?<![\w\d])\d{6}(?![\w\d])"
)

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

    ### The price this gate used to charge — paid off in DRF-1390

    ``re.sub`` does not retry a shorter match after the callback
    declines one, so a card welded to a short neighbouring number by
    **exactly one** space or dash used to be missed entirely where the
    blanket redaction caught it:

        заказ 99 4111111111111111   ->  unredacted  (was "заказ [CC]")

    Measured on 2 000 synthetic welded lines (13-16 digit Luhn-valid
    card + a 1-3 digit neighbour, one space or dash between):
    **67.15%** of the cards reached the trace with every digit intact.

    :func:`_find_card_span` closes it — see there for the mechanism and
    for what it costs.
    """

    return _luhn_valid(_NON_DIGIT_RE.sub("", matched))


_DIGIT_RUN_RE = re.compile(r"\d+")


def _find_card_span(matched: str) -> tuple[int, int] | None:
    """Character span of a Luhn-valid card welded inside a rejected run.

    DRF-1390. :func:`_is_card_number` answers yes/no about the **whole**
    match, and ``re.sub`` never offers the callback a shorter one. A
    card joined to a 1-3 digit neighbour by a single space or dash lands
    inside one ``CC_RE`` match, fails Luhn as a whole, and is returned to
    the trace untouched.

    ### The search, and why it is this narrow

    The run is cut at its separators into digit groups, and only
    **contiguous spans of whole groups** are re-tested. Nothing else:
    not every 13-19 digit window, which is the mechanism the DRF-1382
    docstring named and rejected for costing on the failure path.

    That bound is what makes it cheap where it runs most:

    * one group (``record_id=1234567890123456``, ``ts_ns=...``) — the
      overwhelmingly common rejected shape — returns ``None`` after one
      scan for digit groups and without a single extra checksum. That
      scan is the whole cost measured below.
    * g groups cost at most g²/2 spans, and the ``{13,19}`` quantifier
      caps g at 19 in the worst case and at 4-5 for anything a human
      wrote. A card written ``4111 1111 1111 1111`` never reaches here
      at all: it passes Luhn whole.

    Measured rather than argued, because "costs on the failure path"
    is exactly why DRF-1382 declined to write this. Arms interleaved in
    one process over 1 500 replay step lines, every one of them
    carrying a 16-digit ``order=`` that the gate rejects and this
    function then declines: :meth:`Redactor.redact_text` costs **+4.2%**
    against the plain bool gate. The OTP_RE change shipped alongside it
    pays that back and more — v2 to v3 end-to-end is **-2.9%** on the
    same corpus, because a UUID that is no longer substituted is a
    string no longer rebuilt. Read the ratios, not the absolutes: the
    host was running four other test suites at the time.

    The longest valid span wins, leftmost on a tie — a longer Luhn-valid
    run is the likelier card, and only one span is taken. A second card
    welded into the same run is not searched for; that shape has never
    been seen and adding it would widen the false-positive surface for
    nothing.

    ### The false positives this adds, named and measured

    Luhn passes one random digit string in ten, so every extra span
    tested is another chance to call an order number a card. Measured
    over 20 000 synthetic lines per shape, ``[CC]`` on text with no
    card in it:

    ==========================================  =======  =======
    shape                                       before   after
    ==========================================  =======  =======
    ``order=<14-19 digits>`` (one group)          9.98%    9.98%
    ``заказ NN <14-19 digits>`` (two groups)      9.68%   14.37%
    ==========================================  =======  =======

    Single-group runs — the overwhelming majority of long ids in a
    trace — are untouched, because the search declines them before the
    first checksum. A long id written next to a short number goes from
    90.3% surviving to 85.6%: real, bounded, and the price of the card
    in that same shape going from 32.9% caught to 100%.

    Returns:
      ``(start, end)`` character offsets into ``matched``, or ``None``
      when no proper sub-span passes Luhn.
    """

    groups = [(m.start(), m.end()) for m in _DIGIT_RUN_RE.finditer(matched)]
    if len(groups) < 2:
        # Single digit run: the only span is the whole match, and the
        # caller has already rejected it.
        return None

    lengths = [end - start for start, end in groups]
    last = len(groups) - 1
    best: tuple[int, int] | None = None
    best_len = 0
    for i in range(len(groups)):
        total = 0
        for j in range(i, len(groups)):
            total += lengths[j]
            if total > 19:
                break
            if total < 13 or (i == 0 and j == last):
                continue
            if total <= best_len:
                continue
            start, end = groups[i][0], groups[j][1]
            if _luhn_valid(_NON_DIGIT_RE.sub("", matched[start:end])):
                best = (start, end)
                best_len = total
    return best


def _redact_card_match(matched: str, placeholder: str) -> str:
    """Replacement text for one ``CC_RE`` match (DRF-1382 / DRF-1390).

    Whole run passes Luhn → the placeholder. Otherwise a welded card is
    looked for and only **it** is replaced, so the neighbour the card was
    stuck to stays readable in the trace:

        заказ 99 4111111111111111  ->  заказ 99 [CC]

    Note the allowlist is applied by the caller to the whole match only.
    An allowlist entry equal to a welded card's inner span is not
    honoured — nobody allowlists a card number, and honouring it would
    mean handing the allowlist down into the span search.
    """

    if _is_card_number(matched):
        return placeholder
    span = _find_card_span(matched)
    if span is None:
        return matched
    start, end = span
    return matched[:start] + placeholder + matched[end:]


# (pattern, placeholder, refine). ``refine`` is an optional
# ``(matched, placeholder) -> replacement`` hook: return ``matched`` to leave
# the match alone, the placeholder to redact it whole, or anything in between
# to redact part of it. Only CC uses one. It replaced a plain ``bool`` guard
# in DRF-1390, because "leave it alone entirely" was not a rich enough answer
# for a card welded to a neighbouring number.
_PATTERNS: list[tuple[re.Pattern[str], str, Callable[[str, str], str] | None]] = [
    # Order matters: URL_TOKEN first (contains everything else), then CC
    # (greedy on digit sequences), then PHONE, then EMAIL, then OTP.
    (URL_TOKEN_RE, "[URL_TOKEN]", None),
    (CC_RE, "[CC]", _redact_card_match),
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
        for pattern, placeholder, refine in _PATTERNS:
            result = self._replace_with_allowlist(pattern, placeholder, result, refine)
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
        refine: Callable[[str, str], str] | None = None,
    ) -> str:
        """Apply `pattern` replacement honoring the allowlist and `refine`.

        The allowlist is checked first and wins outright: an exact
        allowlist entry is returned as-is and `refine` never sees it, so
        a partial redaction cannot reach inside an allowlisted span.
        """

        def _sub(match: re.Match[str]) -> str:
            original = match.group(0)
            if original in self._allowlist:
                return original
            if refine is not None:
                return refine(original, placeholder)
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
