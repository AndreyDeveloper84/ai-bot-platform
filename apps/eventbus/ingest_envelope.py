"""Cross-service event envelope — `docs/architecture/event-contract.md` §2.

DISTINCT from :mod:`apps.eventbus.envelope` which models the
in-process taxonomy (SemVer versions, structured `Actor` object). The
cross-service contract has its own shape:

  - integer ``event_version`` (not SemVer string),
  - enum ``actor`` (string: system | user | admin),
  - bare UUID/ULID string IDs (no prefixes),
  - 12 closed-set ``event_name`` values.

See `event-contract.md` §10 for the rationale.

This module ONLY parses + validates the envelope. PII concerns
(§7), HMAC verification (§6.2), and dispatch (§3 consumer contracts)
are layered on top in :mod:`apps.eventbus.ingest_security` and
:mod:`apps.eventbus.ingest_dispatcher`.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from typing import Any, Final, Literal


# event-contract.md §3 — the closed set of cross-service event names.
# Unknown name → 422 + DLQ per §8.5.
ALLOWED_EVENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        "booking.created",
        "booking.confirmed",
        "booking.cancelled",
        "booking.rescheduled",
        "booking.completed",
        "payment.authorized",
        "payment.captured",
        "payment.failed",
        "payment.refunded",
        "review.created",
        "service.updated",
        "master.schedule.updated",
        "user.profile.updated",
    }
)

# event-contract.md §2 — three-value actor enum.
ActorEnum = Literal["system", "user", "admin"]
ALLOWED_ACTORS: Final[frozenset[str]] = frozenset({"system", "user", "admin"})

# event-contract.md §2 — the only event_name where tenant_id MAY be null.
TENANT_NULLABLE_EVENT_NAMES: Final[frozenset[str]] = frozenset({"user.profile.updated"})


class IngestEnvelopeError(ValueError):
    """Raised when an inbound payload fails envelope-shape validation.

    Carries a ``reason`` slug suitable for the 400/422 audit row + the
    Prometheus counter label. The slug taxonomy is finite (one per
    shape violation) so the consumer-side dashboard can chart it.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class IngestEnvelope:
    """Validated cross-service event envelope.

    Construct via :func:`parse_envelope`. Direct construction skips
    validation — only use from tests that have already produced a
    valid shape (e.g. via fixture factories).

    All ID fields are bare strings (the wire shape) — we do NOT
    coerce ULID/UUID into typed objects here because (a) callers
    that need typed IDs convert themselves once and (b) DB writes
    accept the string form regardless.
    """

    event_id: str
    event_name: str
    event_version: int
    occurred_at: dt.datetime
    tenant_id: str | None
    user_id: str
    actor: ActorEnum
    correlation_id: str
    causation_id: str | None
    data: dict[str, Any]


_REQUIRED_TOP_LEVEL: Final[tuple[str, ...]] = (
    "event_id",
    "event_name",
    "event_version",
    "occurred_at",
    "tenant_id",
    "user_id",
    "actor",
    "correlation_id",
    "causation_id",
    "data",
)


def parse_envelope(raw_body: bytes | str | dict[str, Any]) -> IngestEnvelope:
    """Parse + validate a cross-service event envelope.

    Args:
      raw_body: HTTP request body (bytes / str) OR a pre-parsed dict
                (test convenience).

    Returns:
      An :class:`IngestEnvelope` with the parsed fields.

    Raises:
      :class:`IngestEnvelopeError` with a ``reason`` slug from:

      - ``invalid_json`` — body is not JSON.
      - ``not_object`` — JSON is not a top-level object.
      - ``missing_field`` — one of the 10 required envelope fields is absent.
      - ``invalid_event_name`` — name not in §3 closed set.
      - ``invalid_event_version`` — version is not a positive integer.
      - ``invalid_actor`` — actor not in {system, user, admin}.
      - ``invalid_occurred_at`` — not ISO8601 / non-UTC-convertible.
      - ``invalid_tenant_id`` — tenant_id is null for an event that requires it.
      - ``invalid_data`` — ``data`` is not an object.

    The mapping from ``reason`` → HTTP status is intentionally NOT
    encoded here. The view layer (:mod:`apps.eventbus.views`) maps
    according to event-contract.md §8 (400 for malformed, 422 for
    unknown name/version, etc).
    """
    if isinstance(raw_body, (bytes, str)):
        try:
            payload = json.loads(raw_body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise IngestEnvelopeError("invalid_json", str(exc)) from exc
    else:
        payload = raw_body

    if not isinstance(payload, dict):
        raise IngestEnvelopeError("not_object", type(payload).__name__)

    # Required-fields presence check — missing field is a §8.6 malformed
    # body, not §8.5 unknown name.
    missing = [f for f in _REQUIRED_TOP_LEVEL if f not in payload]
    if missing:
        raise IngestEnvelopeError("missing_field", ", ".join(missing))

    event_name = payload["event_name"]
    if not isinstance(event_name, str) or event_name not in ALLOWED_EVENT_NAMES:
        raise IngestEnvelopeError("invalid_event_name", str(event_name))

    event_version = payload["event_version"]
    if not isinstance(event_version, int) or isinstance(event_version, bool) or event_version < 1:
        # bool is a subclass of int — guard so True doesn't parse as 1.
        raise IngestEnvelopeError("invalid_event_version", str(event_version))

    actor = payload["actor"]
    if not isinstance(actor, str) or actor not in ALLOWED_ACTORS:
        raise IngestEnvelopeError("invalid_actor", str(actor))

    occurred_at_raw = payload["occurred_at"]
    if not isinstance(occurred_at_raw, str):
        raise IngestEnvelopeError("invalid_occurred_at", type(occurred_at_raw).__name__)
    try:
        # Python 3.11+ datetime.fromisoformat accepts the "Z" suffix.
        occurred_at = dt.datetime.fromisoformat(occurred_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IngestEnvelopeError("invalid_occurred_at", str(exc)) from exc
    if occurred_at.tzinfo is None:
        raise IngestEnvelopeError("invalid_occurred_at", "missing_timezone")

    tenant_id = payload["tenant_id"]
    if tenant_id is not None and not isinstance(tenant_id, str):
        raise IngestEnvelopeError("invalid_tenant_id", type(tenant_id).__name__)
    if tenant_id is None and event_name not in TENANT_NULLABLE_EVENT_NAMES:
        raise IngestEnvelopeError("invalid_tenant_id", f"null_not_allowed_for_{event_name}")

    user_id = payload["user_id"]
    if not isinstance(user_id, str) or not user_id:
        raise IngestEnvelopeError("missing_field", "user_id")

    event_id = payload["event_id"]
    if not isinstance(event_id, str) or not event_id:
        raise IngestEnvelopeError("missing_field", "event_id")

    correlation_id = payload["correlation_id"]
    if not isinstance(correlation_id, str) or not correlation_id:
        raise IngestEnvelopeError("missing_field", "correlation_id")

    causation_id = payload["causation_id"]
    if causation_id is not None and not isinstance(causation_id, str):
        raise IngestEnvelopeError("invalid_causation_id", type(causation_id).__name__)

    data = payload["data"]
    if not isinstance(data, dict):
        raise IngestEnvelopeError("invalid_data", type(data).__name__)

    return IngestEnvelope(
        event_id=event_id,
        event_name=event_name,
        event_version=int(event_version),
        occurred_at=occurred_at,
        tenant_id=tenant_id,
        user_id=user_id,
        actor=actor,  # type: ignore[arg-type]  # validated against ALLOWED_ACTORS above
        correlation_id=correlation_id,
        causation_id=causation_id,
        data=data,
    )
