"""Identity event consumer (#446 — user.profile.updated).

Per event-contract.md §3.12. One handler:

* ``user.profile.updated`` — for the slice of ``changed_fields`` that
  intersects ``{display_name, avatar_url}``, fetch the current
  profile from Ayla via :func:`apps.integrations.ayla.profile_client`
  and update the matching ``BotUser`` rows.

### Hard rules

* **PII rule §7 — closed safe-sync list.** Only ``display_name`` and
  ``avatar_url`` ever leave Ayla through this consumer. ``phone``,
  ``email``, ``birthday``, ``language`` in ``changed_fields`` cause
  ZERO REST traffic and ZERO writes. The closed enum is enforced at
  TWO layers:
  1. This handler intersects ``changed_fields`` with the allowlist
     before deciding whether to fetch.
  2. :class:`profile_client.ProfileFields` is a fixed-shape dataclass
     so even if Ayla's response carries phone/email, they cannot
     reach the DB.

* **ADR-0009 hard rule #5** — bot-platform never DB-writes canonical
  PII. The mirror fields (``display_name``, ``avatar_url``) are a
  projection for UI rendering, not the source of truth. Ayla owns the
  canonical user profile.

* **Idempotency** — new-vs-current value comparison. If REST returns
  the same values we already have, no UPDATE fires. ``IngestDedupe``
  at the dispatcher layer is the primary guard against same-event_id
  replays.

* **Tenant-nullable** — ``user.profile.updated`` is the ONE event
  where ``envelope.tenant_id`` may be null (display_name + avatar are
  user-global, not tenant-scoped). ONLY on that null path does the
  handler iterate over ALL BotUser rows for that ``ayla_user_id``
  across tenants — same Ayla user typically has one BotUser per
  (tenant, channel) pair. When the envelope IS tenant-scoped, the write
  is narrowed to that tenant: the authorization helper verified exactly
  that pair, and nothing authorizes touching another tenant's rows.

* **Subject = envelope, not payload** — the acted-upon user is
  ``envelope.user_id``, the field the tenant-authorization helper
  verified. A ``data["user_id"]`` that disagrees is a contract
  violation and the event is dropped with no REST call and no write.

### Failure modes

* REST timeout / 5xx / circuit-open → handler raises
  ``ProfileFetchError`` → dispatcher returns ``HANDLER_EXCEPTION`` →
  view returns 500 → Ayla retries per §6.3. Eventually consistent.
* 404 from Ayla → also raises (user gone or wrong ID); dead-letters.
* Migration ordering (W4 ``avatar_url`` field not yet applied) →
  handler falls back to display_name-only sync and logs a warning.
  This ensures the consumer can ship + run before the W4 migration
  lands without crashing.

### Handler registration

Module-level :func:`register_identity_handlers` — called from
``apps/eventbus/apps.py::EventBusConfig.ready``.
"""

from __future__ import annotations

import logging
from typing import Final
from uuid import UUID

from apps.eventbus.ingest_dispatcher import register
from apps.eventbus.ingest_envelope import IngestEnvelope
from apps.eventbus.ingest_tenancy import assert_envelope_tenant_authorized
from apps.identity.models import BotUser
from apps.integrations.ayla.profile_client import (
    fetch_profile_fields,
)


logger = logging.getLogger(__name__)


# Closed allowlist per §3.12 consumer contract: only these fields are
# safe to sync. ANY other ``changed_fields`` entry is a no-op (no REST
# call, no DB write). Enforced at this boundary AND at the REST
# client's response parser (defence-in-depth for PII §7).
_SAFE_SYNC_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "display_name",
        "avatar_url",
    }
)


# ─── handlers ──────────────────────────────────────────────────────────────


def handle_user_profile_updated(envelope: IngestEnvelope) -> None:
    """``user.profile.updated`` — event-contract.md §3.12.

    Steps:
      1. Verify envelope tenant authorization (A3). Note: tenant_id MAY
         be null for this event family — auth helper handles that.
      2. Parse ``user_id`` (UUID) from the ENVELOPE, and drop the event
         if ``data["user_id"]`` names a different subject.
      3. Compute safe-sync slice: ``changed_fields ∩ _SAFE_SYNC_FIELDS``.
         If empty → no-op (skip REST + DB).
      4. Fetch profile via Ayla REST.
      5. Locate BotUser rows for this ayla_user_id across tenants.
      6. For each row: apply field updates only where the fetched
         value differs from the current one (idempotency by value
         comparison).
    """
    assert_envelope_tenant_authorized(envelope)

    data = envelope.data

    # The SUBJECT of the event is ``envelope.user_id`` — the field the
    # envelope parser requires, the field every other consumer family keys
    # off, and the field the tenant-authorization helper just verified.
    # This handler used to key off ``data["user_id"]`` instead: an
    # unverified payload field that authorization never looks at. On the
    # tenant-null path that made the pair (authorized subject, acted-upon
    # subject) divergeable at will, turning the handler into a REST fan-out
    # against arbitrary Ayla user UUIDs. Use the envelope; treat a payload
    # that disagrees as a contract violation.
    try:
        user_id = UUID(envelope.user_id)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "eventbus.consumer.identity.profile_updated.bad_user_id event_id=%s exc=%s",
            envelope.event_id,
            type(exc).__name__,
        )
        return

    payload_user_id = data.get("user_id") if isinstance(data, dict) else None
    if payload_user_id is not None:
        try:
            payload_matches = UUID(str(payload_user_id)) == user_id
        except (ValueError, TypeError):
            payload_matches = False
        if not payload_matches:
            # Drop without side effects rather than raising: a raise
            # dead-letters and pages (§6.3), which hands anyone holding the
            # shared HMAC secret a pager trigger. Nothing is fetched and
            # nothing is written, so the primitive is dead either way.
            logger.error(
                "eventbus.consumer.identity.profile_updated.subject_mismatch "
                "security=high event_id=%s envelope_user_id=%s — data.user_id "
                "disagrees with the authorized envelope subject; dropping "
                "without REST or DB side effects.",
                envelope.event_id,
                envelope.user_id,
            )
            return

    # Closed-shape parse of changed_fields. Defence against publisher
    # contract drift (non-list, non-string entries).
    changed_fields_raw = data.get("changed_fields", [])
    if not isinstance(changed_fields_raw, list):
        logger.warning(
            "eventbus.consumer.identity.profile_updated.bad_changed_fields event_id=%s type=%s",
            envelope.event_id,
            type(changed_fields_raw).__name__,
        )
        return
    changed_fields = {field for field in changed_fields_raw if isinstance(field, str)}

    # PII §7 enforcement at the consumer boundary: only the closed
    # allowlist triggers REST + writes. phone/email/birthday/language
    # in changed_fields are dropped HERE before any network call.
    safe_slice = changed_fields & _SAFE_SYNC_FIELDS
    if not safe_slice:
        # Count only — ``changed_fields`` entries are publisher-supplied
        # strings, so rendering them into a key=value log line is the same
        # log-injection vector the tenancy helper sanitizes against. The
        # names carry no diagnostic value here beyond "none were safe".
        logger.info(
            "eventbus.consumer.identity.profile_updated.no_safe_fields "
            "event_id=%s changed_field_count=%d — skipping REST (PII §7 enforcement)",
            envelope.event_id,
            len(changed_fields),
        )
        return

    # Fetch from Ayla. ProfileFetchError → dispatcher dead-letters
    # → Ayla retries. NEVER swallow + skip (stale projection forever).
    profile = fetch_profile_fields(user_id)

    # Locate the BotUser rows for this Ayla user. ``display_name`` and
    # ``avatar_url`` are user-global, so a genuinely tenant-null envelope
    # legitimately fans out across tenants + channels (one user may have
    # MAX + Telegram BotUsers under the same tenant, OR BotUsers under
    # several tenants).
    #
    # But when the publisher DID scope the envelope to a tenant, that scope
    # is the authorized one — the tenant-authorization helper verified
    # exactly that pair. Writing outside it was an unnecessary widening:
    # a tenant-scoped envelope has no business mutating rows belonging to
    # other tenants. Narrow to the authorized tenant in that case.
    user_rows = BotUser.all_tenants.filter(ayla_user_id=user_id)
    if envelope.tenant_id is not None:
        user_rows = user_rows.filter(tenant_id=envelope.tenant_id)
    bot_users = list(user_rows)
    if not bot_users:
        logger.info(
            "eventbus.consumer.identity.profile_updated.no_bot_users "
            "ayla_user_id=%s event_id=%s — Ayla-mobile-only user, "
            "no channel projection to sync",
            user_id,
            envelope.event_id,
        )
        return

    updated_count = 0
    skipped_count = 0
    for bot_user in bot_users:
        update_fields: list[str] = []

        if "display_name" in safe_slice:
            if bot_user.display_name != profile.display_name:
                bot_user.display_name = profile.display_name
                update_fields.append("display_name")

        if "avatar_url" in safe_slice:
            # Migration ordering defence: W4 may not have shipped the
            # avatar_url field yet. ``hasattr`` check lets us run
            # safely against either schema state. Once W4's migration
            # lands, this branch fires normally.
            if hasattr(bot_user, "avatar_url"):
                current_avatar = getattr(bot_user, "avatar_url", "")
                if current_avatar != profile.avatar_url:
                    setattr(bot_user, "avatar_url", profile.avatar_url)
                    update_fields.append("avatar_url")
            else:
                logger.warning(
                    "eventbus.consumer.identity.profile_updated.avatar_field_missing "
                    "bot_user_id=%s — W4 migration not yet applied; "
                    "skipping avatar_url sync until field lands",
                    bot_user.pk,
                )

        if update_fields:
            bot_user.save(update_fields=update_fields)
            updated_count += 1
        else:
            skipped_count += 1

    # ``tenant_scope`` makes the fan-out auditable: ``all_tenants`` says
    # this write crossed tenant boundaries on the authority of a tenant-null
    # envelope, which is the accepted Controlled-Pilot residual risk and the
    # thing an operator needs to be able to enumerate. ``safe_slice`` is a
    # closed two-value enum, so it is safe to render verbatim.
    logger.info(
        "eventbus.consumer.identity.profile_updated.synced "
        "ayla_user_id=%s event_id=%s safe_slice=%s tenant_scope=%s "
        "bot_users_updated=%d bot_users_skipped_idempotent=%d",
        user_id,
        envelope.event_id,
        sorted(safe_slice),
        "all_tenants" if envelope.tenant_id is None else "envelope_tenant",
        updated_count,
        skipped_count,
    )


# ─── registration ──────────────────────────────────────────────────────────


def register_identity_handlers() -> None:
    """Register identity consumer handlers with the ingest dispatcher.

    Called from :func:`apps.eventbus.apps.EventBusConfig.ready`.
    """
    try:
        register(
            event_name="user.profile.updated",
            event_version=1,
            handler=handle_user_profile_updated,
        )
    except ValueError:
        # Duplicate registration — silently OK on autoreload.
        pass
