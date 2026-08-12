"""Pilot-scoped tenant/event allowlists for the cross-service ingest (T-02).

### Why this module exists

``apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized`` verifies
``(user_id, tenant_id)`` against the canonical ``TenantUserRelationship``
(ADR-0009 §Hard rule #6). That model lives in Ayla, not in bot-platform, so
the import fails here and the helper takes its **fail-closed** branch: every
tenant-scoped envelope raises ``TenantAuthorizationError`` → 500 → Ayla
retries → DLQ. Wave-1 pilot cannot ingest a single ``booking.created``.

The only pre-existing escape was the global
``EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN`` flag, which disables tenant
verification for *every* tenant and *every* event — an unbounded blast
radius that staging was silently running with.

Owner decision **OD-T02-1**: no full ``TenantUserRelationship`` in bot for
Wave 1, and no global fail-open. Instead: an explicit, empty-by-default
allowlist of *(pilot tenant UUID, pilot event name)* pairs. An envelope
passes the pilot branch only when **both** dimensions are allowlisted and
the tenant actually exists in the bot DB.

### Security model (Controlled Pilot ONLY)

This is a *scope limiter*, not a relationship proof. It answers "is this
tenant one of the handful we deliberately onboarded, and is this event one
of the handful we deliberately consume?" It does **not** prove that
``envelope.user_id`` genuinely belongs to ``envelope.tenant_id`` — only the
canonical relationship contract can do that. Residual risk is documented in
``docs/runbooks/eventbus-subscriber-activation.md``; Public MVP MUST replace
this with the real relationship check.

### Parsing contract

Both settings are CSV strings in the environment, normalized **once** at
settings load into a ``frozenset``. Normalization is deliberately strict —
a malformed value must never widen access:

* empty / unset → empty frozenset → **deny all**
* surrounding whitespace trimmed per element
* empty elements (``"a,,b"``, trailing comma) rejected
* duplicates collapsed
* wildcards (``*``, ``all``, ``any``) rejected — there is no "allow
  everything" spelling by construction
* tenants: canonical 8-4-4-4-12 hyphenated UUID only, lowercased
* events: ``lower.dotted.name`` shape only, lowercased

Anything else raises :class:`AllowlistConfigurationError`, which
``config/settings/base.py`` surfaces as ``ImproperlyConfigured`` — a
startup failure, per the T-02 preference for production-like environments.
"""

from __future__ import annotations

import re
from typing import Any


# Canonical hyphenated UUID, nothing else. ``uuid.UUID()`` would also accept
# brace-wrapped, URN and dash-less spellings; we reject those so the
# configured value is byte-comparable with what operators paste from the
# Ayla tenant table and so no exotic spelling slips past a review.
_UUID_RE = re.compile(r"\A[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")

# Dotted lower-case event name, >=2 dot-separated segments
# (``booking.created``, ``master.schedule.updated``). Underscores are legal
# WITHIN a segment (``booking.no_show``) but are NOT a segment separator:
# ``booking_created`` must not parse as a "dotted event name", or a typo'd
# underscore would silently deny the real event while the boot log reports
# the allowlist as active. No wildcards, no whitespace, no uppercase after
# normalization.
_EVENT_SEGMENT = r"[a-z0-9]+(?:_[a-z0-9]+)*"
_EVENT_NAME_RE = re.compile(rf"\A{_EVENT_SEGMENT}(?:\.{_EVENT_SEGMENT})+\Z")

# NOTE: the allowlist has no ``event_version`` dimension — entries match on
# name alone. When a ``booking.created@v2`` handler is registered it is
# admitted by an existing v1 pilot configuration with no operator action.
# Acceptable while the contract is frozen at v1 for the pilot; revisit
# (``name@vN`` entries) before any consumer ships a second version.

# Spellings that would mean "everything". Rejected outright rather than
# silently treated as a literal name — an operator typing one of these
# expects it to widen access, and we must never grant that.
_WILDCARD_TOKENS: frozenset[str] = frozenset({"*", "**", "all", "any", ".*", "%"})


class AllowlistConfigurationError(ValueError):
    """Raised when an allowlist setting cannot be parsed safely.

    Callers MUST treat this as *deny all*, never as *allow all*. At
    settings-load time it is re-raised as ``ImproperlyConfigured`` so the
    process refuses to boot; at authorization time (a value injected via
    ``override_settings`` or a live settings reload) it maps to the
    ``malformed_configuration`` reject reason.
    """


def _split_raw(raw: Any) -> list[str]:
    """Normalize the setting value into a list of trimmed string elements.

    Accepts the two shapes a setting can legitimately have:

    * ``str`` — the raw CSV straight from the environment.
    * ``list`` / ``tuple`` / ``set`` / ``frozenset`` — an already-normalized
      value (the settings module's output) or a container set by a test.

    ``None`` is treated as unset (empty). Anything else is a configuration
    error, not something to coerce. The container check is deliberately an
    explicit type list rather than ``isinstance(raw, Iterable)``: a ``dict``
    is iterable, so the loose check silently accepted ``{uuid: "note"}`` as
    a one-element allowlist keyed on its keys, and a generator would be
    consumed by the first read — leaving the startup check and the request
    path disagreeing about what is configured.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        if not raw.strip():
            return []
        return [element.strip() for element in raw.split(",")]
    if isinstance(raw, (bytes, bytearray)):  # noqa: UP038 — explicit tuple, py<3.10 safe
        raise AllowlistConfigurationError(
            f"allowlist must be a CSV string or an iterable of strings, got {type(raw).__name__}"
        )
    if isinstance(raw, (list, tuple, set, frozenset)):  # noqa: UP038 — explicit tuple
        elements: list[str] = []
        for element in raw:
            if not isinstance(element, str):
                raise AllowlistConfigurationError(
                    f"allowlist elements must be strings, got {type(element).__name__}"
                )
            elements.append(element.strip())
        return elements
    raise AllowlistConfigurationError(
        f"allowlist must be a CSV string or an iterable of strings, got {type(raw).__name__}"
    )


def _reject_wildcard(element: str, *, setting_name: str) -> None:
    if element.lower() in _WILDCARD_TOKENS or "*" in element:
        raise AllowlistConfigurationError(
            f"{setting_name}: wildcard entry {element!r} is not permitted — "
            "the pilot allowlist must enumerate every allowed value explicitly"
        )


def parse_tenant_allowlist(
    raw: Any, *, setting_name: str = "EVENT_INGEST_ALLOWED_TENANTS"
) -> frozenset[str]:
    """Parse a tenant-UUID allowlist setting into canonical tenant UUIDs.

    Returns an empty frozenset for an unset/empty value — **deny all**.
    Raises :class:`AllowlistConfigurationError` on any malformed element;
    partial acceptance is deliberately not offered, because "3 of the 4
    UUIDs parsed" is exactly the state where an operator believes a tenant
    is onboarded and it silently is not (or worse, believes one is excluded
    when the typo landed elsewhere).

    ``setting_name`` only labels error messages; the default keeps the
    original ``EVENT_INGEST_ALLOWED_TENANTS`` wording. DRF-1005 reuses this
    parser for ``BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS``.
    """
    normalized: set[str] = set()
    for element in _split_raw(raw):
        if not element:
            raise AllowlistConfigurationError(
                f"{setting_name}: empty element (check for a stray or trailing comma)"
            )
        _reject_wildcard(element, setting_name=setting_name)
        candidate = element.lower()
        if not _UUID_RE.match(candidate):
            raise AllowlistConfigurationError(
                f"{setting_name}: {element!r} is not a canonical hyphenated UUID"
            )
        normalized.add(candidate)
    return frozenset(normalized)


def parse_event_allowlist(raw: Any) -> frozenset[str]:
    """Parse ``EVENT_INGEST_ALLOWED_EVENTS`` into canonical event names.

    Shape-validated only — membership in the dispatcher's ``_KNOWN_NAMES``
    is checked by the startup check (:mod:`apps.eventbus.startup_checks`),
    which runs after the app registry is ready. Validating it here would
    force a settings-load-time import of the dispatcher.

    Names are lowercased so ``Booking.Created`` in the environment cannot
    become a second, differently-cased entry. The comparison against
    ``envelope.event_name`` stays exact: the envelope parser already pins
    names to the closed lower-case §3 vocabulary, so a case-tricked
    envelope name never matches an allowlist entry.
    """
    setting_name = "EVENT_INGEST_ALLOWED_EVENTS"
    normalized: set[str] = set()
    for element in _split_raw(raw):
        if not element:
            raise AllowlistConfigurationError(
                f"{setting_name}: empty element (check for a stray or trailing comma)"
            )
        _reject_wildcard(element, setting_name=setting_name)
        candidate = element.lower()
        if not _EVENT_NAME_RE.match(candidate):
            raise AllowlistConfigurationError(
                f"{setting_name}: {element!r} is not a valid dotted event name"
            )
        normalized.add(candidate)
    return frozenset(normalized)


def resolve_allowed_tenants(raw: Any) -> frozenset[str]:
    """Defensive re-normalization of the tenant allowlist at read time.

    ``config/settings/base.py`` already stores a normalized frozenset, so
    on the common path this re-validates an already-canonical 1–3 element
    set (a few regex matches — negligible per event). We deliberately do
    NOT fast-path on ``isinstance(raw, frozenset)``: a value injected after
    settings load (``override_settings`` in a test, a live settings reload,
    a future code path assigning the setting directly) would then skip
    validation entirely, and an unvalidated entry is exactly the "malformed
    configuration widens access" failure this module exists to prevent.
    """
    return parse_tenant_allowlist(raw)


def resolve_allowed_events(raw: Any) -> frozenset[str]:
    """Defensive re-normalization of the event allowlist at read time.

    See :func:`resolve_allowed_tenants`.
    """
    return parse_event_allowlist(raw)
