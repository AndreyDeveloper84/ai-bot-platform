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
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# --- Pinned patterns (redaction_method = "regex_v1") ----------------------

REDACTION_METHOD = "regex_v1"

# Phone: +7/8/+anything followed by 10 digits, optionally with spaces / dashes
# / parens. Catches:
#   +7 (495) 123-45-67   8 800 123 45 67   +12345678901
PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)

# Email: simple RFC-5322 lite. Don't try to validate, just match the shape.
EMAIL_RE = re.compile(r"[\w\.\-+]+@[\w\-]+\.[\w]{2,}")

# Credit card: 13-19 digit groups separated by space/dash. Cheap-and-cheerful;
# don't run Luhn check (too expensive for redactor hot path).
CC_RE = re.compile(r"(?<!\d)(?:\d[\s\-]?){13,19}(?!\d)")

# OTP: standalone 4- or 6-digit sequences (not embedded in longer numbers).
# Negative lookbehind+lookahead prevent matching the inside of phone numbers
# (those are already matched by PHONE_RE).
OTP_RE = re.compile(r"(?<![\w\d])\d{4}(?![\w\d])|(?<![\w\d])\d{6}(?![\w\d])")

# URLs with sensitive query params: ?token= / ?key= / ?secret= / ?auth=
# Captures the URL up to the next whitespace.
URL_TOKEN_RE = re.compile(
    r"https?://\S*?[?&](?:token|key|secret|auth|api_key|access_token)=\S+",
    re.IGNORECASE,
)


_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Order matters: URL_TOKEN first (contains everything else), then CC
    # (greedy on digit sequences), then PHONE, then EMAIL, then OTP.
    (URL_TOKEN_RE, "[URL_TOKEN]"),
    (CC_RE, "[CC]"),
    (PHONE_RE, "[PHONE]"),
    (EMAIL_RE, "[EMAIL]"),
    (OTP_RE, "[OTP]"),
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
        for pattern, placeholder in _PATTERNS:
            result = self._replace_with_allowlist(pattern, placeholder, result)
        return result

    def redact_steps(self, steps: list[Any]) -> list[Any]:
        """Recursive walk over pipeline steps — redacts every str leaf.

        Preserves non-string leaves (ints, floats, None, bools)
        unchanged.
        """

        return [self._redact_value(s) for s in steps]

    # --- Internals --------------------------------------------------------

    def _replace_with_allowlist(self, pattern: re.Pattern[str], placeholder: str, text: str) -> str:
        """Apply `pattern` replacement honoring the allowlist."""

        def _sub(match: re.Match[str]) -> str:
            original = match.group(0)
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
