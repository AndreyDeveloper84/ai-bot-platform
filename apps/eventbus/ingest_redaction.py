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
* **Coarse categoricals**: status enums, source enums, reason enums
  from the contract §3 closed sets only. Round-3 NEW-4 — the
  previous ``[a-z0-9_.]{1,40}`` regex was a bypass surface; a
  field like ``customer_name="annaivanova"`` matched it. Replaced
  with a STRICT allowlist of literal contract values.
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

Same applies to the enum allowlist: a v2 event adding a new enum
value must explicitly extend :data:`ALLOWED_ENUM_VALUES` — that's
the v2-schema review forcing function.

### Field shape detection

* UUID/ULID-ish: regex match.
* ISO 8601 timestamp: regex match.
* Numeric (int/float/decimal-string): isinstance + string-to-Decimal.
* Boolean: isinstance(bool).
* Enum value: literal membership in
  :data:`ALLOWED_ENUM_VALUES` (case-sensitive). NO regex —
  unknown enum-shaped strings are redacted.

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

# Round-3 NEW-4 — strict literal allowlist from `event-contract.md`
# §3. Replaces the previous ``[a-z0-9_.]{1,40}$`` regex which
# accepted free-text names like "annaivanova" as enum-shaped.
# Adding a new enum value requires editing this set, which forces
# a code-review checkpoint on every v2 schema change.
ALLOWED_ENUM_VALUES: Final[frozenset[str]] = frozenset(
    {
        # actor (§2)
        "system",
        "user",
        "admin",
        # booking.created.status
        "confirmed",
        "pending_payment",
        "awaiting_payment",
        "tentative",
        # booking.created.source
        "mobile_app",
        "admin_console",
        "automation",
        "yclients_sync",
        # booking.cancelled.cancelled_by + rescheduled_by
        "master",
        # booking.cancelled.reason_code
        "user_changed_plans",
        "user_no_show",
        "master_unavailable",
        "tenant_closed_slot",
        "payment_hold_expired",
        "policy_violation",
        "other",
        # payment.authorized.provider
        "yookassa",
        "manual",
        # payment.failed.reason
        "card_declined",
        "insufficient_funds",
        "hold_expired",
        "provider_error",
        "cancelled_by_user",
        # payment.refunded.reason
        "customer_request",
        "no_show_dispute",
        "service_issue",
        "admin_correction",
        # master.schedule.updated.change_type
        "recurring_block",
        "exception_added",
        "exception_removed",
        "vacation_set",
        "vacation_cleared",
        # Round-4 R3-1 — PII-FIELD-NAME strings DELIBERATELY EXCLUDED
        # from the global allowlist. The contract §3 schema allows
        # values like ``changed_fields=["name", "phone", "email"]``,
        # but those literal strings are also valid attacker-controlled
        # leaf values (e.g. customer_name="phone") that would pass
        # redaction if the enum check accepted them.
        #
        # Tradeoff: ``changed_fields`` lists redact to
        # ``[REDACTED, ...]`` — operator sees the change occurred,
        # not which fields. Forensic cost is bounded; PII leak risk
        # is eliminated. Per round-1 «conservative — better to lose
        # forensic detail than leak free-text PII for 90 days».
        #
        # NOT in the allowlist (any of these strings as values
        # → REDACTED):
        #   name, price, duration, is_active, category_id,
        #   requires_consultation, display_name, avatar_url, language,
        #   phone, email, birthday
        # service.updated.changed_fields — NUMERIC values that ARE
        # actual data (not field names) MAY still pass via the
        # decimal-string path below.
        # currency (ISO-4217 — limited set used by Ayla)
        "RUB",
        "USD",
        "EUR",
    }
)


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
        # Round-3 NEW-4 — strict literal allowlist (NO regex). An
        # attacker-controlled `customer_name="annaivanova"` no
        # longer passes the enum check; only the documented contract
        # §3 values survive.
        if value in ALLOWED_ENUM_VALUES:
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
