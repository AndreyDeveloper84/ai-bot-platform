"""Domain exceptions for the memory layer.

Per spec `docs/specs/memory-entry-schema.md` §10–§11 and ADR-0011
§9.1 (cross-tenant red carve-out), §10.2 (minor protection fail-closed),
§11.2 (zone promotion as fresh consent event).

These exceptions are raised by:
  - apps.identity.services.red_zone_reader.RedZoneReader.read
  - apps.identity.services.memory_writer.write_entry / promote_zone
"""

from __future__ import annotations


class MinorProtectionLookupFailed(Exception):
    """Raised when Ayla REST DOB lookup is unavailable or returns «unknown».

    Per ADR-0011 §10.2: yellow/red writes MUST fail-closed when the
    user's date of birth cannot be verified — we may not write
    sensitive data about someone we can't confirm is an adult.

    Callers catch this and silently drop the write while appending a
    `write_rejected_dob_lookup` row to RedZoneAccessLog so the
    forensic trail records the rejected attempt.

    Phase 0: the DOB endpoint does not exist yet (#597), so the
    `_check_minor_protection` stub raises this for every yellow/red
    write. Effect: in production, yellow/red writes never succeed
    until #597 lands.
    """


class ZonePromotionRequiresConsent(Exception):
    """Raised when promoting green→yellow/red without a consent_token.

    Per ADR-0011 §11.2: changing an entry's sensitivity_zone upward is
    a fresh consent event — the user's original consent (or implicit
    consent for green) does not extend to higher sensitivity. The
    writer requires an explicit `consent_token` argument that proves
    the user re-consented for the new zone.

    Demotion (yellow→green, red→yellow) does NOT require a token —
    moving toward less sensitivity is always allowed.
    """


class TenantScopeViolation(Exception):
    """Raised when red-zone entry source_tenant_id mismatches caller scope.

    Per ADR-0011 §9.1: even when STRICT_TENANT_REFUSE is False
    (default-permissive transitional regime), the red zone has a
    carve-out — surfacing a red fact to a tenant that didn't source
    it is forbidden, full stop.

    The app-layer check runs AFTER the SELECT (we need source_tenant_id
    to compare) but inside the same atomic block, so the audit row
    that would have been written for the access also rolls back —
    no record of «attempted to read» leaks per spec §13 row 14.22.
    """
