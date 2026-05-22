"""DLQ envelope redaction — Round-2 adversarial pass AS4.

The DLQ persists ``envelope.data`` for 90 days (§6.4 retention).
Without redaction, a publisher bug or a v2 event that adds new
PII-shaped fields = 90 days of unredacted PII sitting in the DLQ.
Ops triages DLQ via Sentry / log aggregator → unredacted PII on
screens. ADR-0011 §3.4 + 152-ФЗ violation.

This module owns the «what can land in DLQ» allowlist:

### Allowlist

* **IDs** (UUID-shaped or ULID-shaped strings): event_id payload
  identifiers like ``appointment_id``, ``master_id``, ``service_id``,
  ``payment_id``. Per §7 PII rules, IDs are safe.
* **Coarse categoricals**: status enums, source enums, reason enums.
* **Numerics + timestamps**: prices, durations, ISO datetime strings.
* **Booleans**: ``has_text``, ``is_anonymous``.

### Denylist (stripped)

Anything that's not in the above categories. Conservative —
better to lose forensic detail than leak free-text PII for 90 days.

### Why a positive allowlist vs a denylist

A denylist of "known PII fields" goes stale the moment a v2 event
adds a new field. The adversarial bypass is precisely that
slippage. An allowlist forces every NEW field to be explicitly
classified before it can reach the DLQ. The v2 schema review
becomes mandatory.

### Field shape detection

Without a per-event-name schema (deferred to the consumer family),
we use shape heuristics:

* UUID/ULID-ish: regex match.
* ISO 8601 timestamp: regex match.
* Numeric (int/float/decimal-string): isinstance + string-to-Decimal.
* Boolean: isinstance(bool).
* Enum-shaped short string: ≤ 40 chars + all-lowercase + no spaces +
  contains only ``[a-z0-9_.]``.

Anything else is redacted to the placeholder sentinel.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Final


REDACTED_SENTINEL: Final[str] = "[REDACTED]"

# UUID/ULID-shaped IDs. UUID = 8-4-4-4-12 hex; ULID = 26 base32 chars.
_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ULID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# ISO 8601 timestamp (lenient — covers Z suffix + offset forms).
_ISO_DT_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,9})?"
    r"(?:Z|[+\-]\d{2}:\d{2})?$"
)

# Enum-shaped short string.
_ENUM_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_.]{1,40}$")


def _safe_value(value: Any) -> Any:
    """Return ``value`` if shape-allowed, else :data:`REDACTED_SENTINEL`.

    Recursive for dict/list — applies to leaf values only. Keys are
    not redacted (they identify the structure for operator triage,
    which is the point of a DLQ row).
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        # ID shape?
        if _UUID_RE.match(value) or _ULID_RE.match(value):
            return value
        # Timestamp?
        if _ISO_DT_RE.match(value):
            return value
        # Enum-shaped short string?
        if _ENUM_RE.match(value):
            return value
        # Numeric string (e.g. "1800.00")?
        try:
            Decimal(value)
        except (InvalidOperation, ValueError):
            pass
        else:
            return value
        return REDACTED_SENTINEL
    if isinstance(value, list):
        return [_safe_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _safe_value(v) for k, v in value.items()}
    # Unknown type — redact for safety.
    return REDACTED_SENTINEL


def redact_data_for_dlq(data: dict[str, Any]) -> dict[str, Any]:
    """Return a redacted copy of an envelope ``data`` payload.

    Free-text PII strings (names, addresses, emails, phone numbers
    in any format) collapse to :data:`REDACTED_SENTINEL`. IDs,
    timestamps, enums, and numeric values pass through.

    The function is pure — input dict is not mutated. Caller should
    pass the redacted dict to ``IngestDLQ.raw_body``.
    """
    if not isinstance(data, dict):
        return {}
    return {k: _safe_value(v) for k, v in data.items()}
