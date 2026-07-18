"""C5 — customer personal-data export/delete aggregation (152-ФЗ, pilot).

Implements the bot-side half of ``PILOT_CONTRACTS_2026-08-15`` §6:

* **Export** — one JSON aggregating the Ayla export (verbatim upstream
  payload, C5.1) + bot-side green ``MemoryEntry`` rows + ``ConsentRecord``
  history for the person (cross-tenant — the person is keyed by
  ``ayla_user_id`` and may have a ``BotUser`` per tenant).
* **Delete** — the cascade: Ayla personal-data delete (C5.2 upstream),
  bot ``MemoryEntry`` erasure (immediate green soft-delete +
  ``forget_all`` UPC tombstone), consent withdraw cascade
  (:func:`apps.consent.services.withdraw_personal_data`).

### Contract obligations honoured

* **Idempotent delete** — every step is naturally re-runnable: upstream
  404 counts as already-deleted, green soft-delete skips tombstoned
  rows, ``forget_all``/``withdraw_personal_data`` are no-ops on repeat.
  A repeated DELETE therefore returns the same success.
* **Audit without personal values** — both operations append an audit
  row naming the actor, timestamp and *scope* of the action; fact
  values, phones and names never enter the audit payload.
* **Honest partials** — a failed cascade step is reported, not hidden:
  the view maps ``all_ok=False`` to 502 so the miniapp can offer a retry
  (already-done steps no-op on that retry).
* **Pilot scope (C5.2)** — personal context + memory + consents.
  Transactional records (bookings, payments) follow statutory retention;
  their anonymisation is explicitly post-pilot.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from apps.audit.services import write_audit
from apps.consent.models import ConsentRecord
from apps.consent.services import withdraw_personal_data
from apps.identity.models import BotUser
from apps.identity.services.memory_deleter import (
    request_forget_all,
    soft_delete_green_entries,
)
from apps.identity.services.memory_reader import read_green_entries
from apps.integrations.ayla.personal_context_client import (
    PersonalContextError,
    PersonalContextHttpClient,
    PersonalContextNotFoundError,
)

logger = logging.getLogger(__name__)


class PrivacyUpstreamError(Exception):
    """Ayla-side export/delete failed — the view maps this to 502."""


@dataclass(frozen=True)
class DeleteStep:
    """One cascade step outcome. ``detail`` is a slug, never a value."""

    step: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class DeleteCascadeResult:
    steps: tuple[DeleteStep, ...] = field(default_factory=tuple)

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def failed_steps(self) -> list[str]:
        return [s.step for s in self.steps if not s.ok]


def _resolve_ayla_user_id(bot_user: BotUser) -> uuid.UUID | None:
    raw = getattr(bot_user, "ayla_user_id", None)
    if not raw:
        return None
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


def _bot_user_ids_for(ayla_user_id: uuid.UUID) -> list[uuid.UUID]:
    """Every BotUser of the person across tenants (memory is global)."""
    return list(BotUser.all_tenants.filter(ayla_user_id=ayla_user_id).values_list("id", flat=True))


# ---------------------------------------------------------------------------
# Export (C5.1)
# ---------------------------------------------------------------------------


def export_personal_data(
    bot_user: BotUser,
    *,
    client: PersonalContextHttpClient | None = None,
) -> dict[str, Any]:
    """Aggregate the person's export payload. Raises PrivacyUpstreamError
    when the Ayla leg fails (an export silently missing its Ayla half
    would be a compliance lie — better an honest 502).

    Bot-side sections are always present; the ``ayla`` section is ``None``
    when the user has no Ayla link yet (nothing exists upstream).
    """
    ayla_user_id = _resolve_ayla_user_id(bot_user)

    ayla_section: dict[str, Any] | None = None
    if ayla_user_id is not None:
        owns = client is None
        client = client or PersonalContextHttpClient()
        try:
            ayla_section = client.get_personal_data_export(ayla_user_id=str(ayla_user_id))
        except PersonalContextError as exc:
            raise PrivacyUpstreamError(f"ayla export failed: {exc}") from exc
        finally:
            if owns:
                client.close()

    memory_section: list[dict[str, Any]] = []
    if ayla_user_id is not None:
        memory_section = [
            {
                "id": str(entry.id),
                "kind": entry.kind,
                "source": entry.source,
                "content": entry.content if isinstance(entry.content, dict) else {},
                "last_inferred_at": entry.last_inferred_at.isoformat()
                if entry.last_inferred_at
                else None,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in read_green_entries(ayla_user_id)
        ]

    consents_qs = ConsentRecord.all_tenants.order_by("captured_at")
    if ayla_user_id is not None:
        consents_qs = consents_qs.filter(bot_user_id__in=_bot_user_ids_for(ayla_user_id))
    else:
        consents_qs = consents_qs.filter(bot_user_id=bot_user.id)
    consents_section = [
        {
            "consent_type": row.consent_type,
            "granted": row.granted,
            "document_version": row.document_version,
            "source": row.source,
            "captured_at": row.captured_at.isoformat(),
            "withdrawn_at": row.withdrawn_at.isoformat() if row.withdrawn_at else None,
        }
        for row in consents_qs
    ]

    write_audit(
        "privacy.personal_data_exported",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "actor": "customer",
            "scope": ["ayla_export", "memory_green", "consents"],
        },
    )

    return {
        "generated_at": timezone.now().isoformat(),
        "subject": {
            "ayla_user_id": str(ayla_user_id) if ayla_user_id else None,
        },
        "ayla": ayla_section,
        "memory": memory_section,
        "consents": consents_section,
    }


# ---------------------------------------------------------------------------
# Delete (C5.2)
# ---------------------------------------------------------------------------


def delete_personal_data(
    bot_user: BotUser,
    *,
    client: PersonalContextHttpClient | None = None,
) -> DeleteCascadeResult:
    """Run the C5 delete cascade for the person. Every step is
    idempotent; per-step outcomes are reported, never hidden."""
    ayla_user_id = _resolve_ayla_user_id(bot_user)
    steps: list[DeleteStep] = []

    # Step 1 — Ayla personal-data delete (upstream, C5.2).
    if ayla_user_id is None:
        steps.append(DeleteStep("ayla_delete", True, "not_linked"))
    else:
        owns = client is None
        client = client or PersonalContextHttpClient()
        try:
            client.delete_personal_data(ayla_user_id=str(ayla_user_id))
            steps.append(DeleteStep("ayla_delete", True))
        except PersonalContextNotFoundError:
            # Already gone upstream — idempotent success.
            steps.append(DeleteStep("ayla_delete", True, "already_deleted"))
        except PersonalContextError:
            logger.exception("identity.privacy.ayla_delete_failed")
            steps.append(DeleteStep("ayla_delete", False))
        finally:
            if owns:
                client.close()

    # Steps 2+3 — bot memory erasure + consent cascade need the person id.
    if ayla_user_id is None:
        steps.append(DeleteStep("memory_delete", True, "not_linked"))
        steps.append(DeleteStep("consent_withdraw", True, "not_linked"))
    else:
        try:
            live_green_ids = [e.id for e in read_green_entries(ayla_user_id)]
            soft_delete_green_entries(ayla_user_id, live_green_ids)
            request_forget_all(ayla_user_id)
            steps.append(DeleteStep("memory_delete", True))
        except Exception:  # noqa: BLE001 — per-step isolation, reported below
            logger.exception("identity.privacy.memory_delete_failed")
            steps.append(DeleteStep("memory_delete", False))

        try:
            withdraw_personal_data(ayla_user_id, source="privacy_delete")
            steps.append(DeleteStep("consent_withdraw", True))
        except Exception:  # noqa: BLE001 — per-step isolation
            logger.exception("identity.privacy.consent_withdraw_failed")
            steps.append(DeleteStep("consent_withdraw", False))

    result = DeleteCascadeResult(steps=tuple(steps))
    # Audit: actor + scope only — never the deleted values (C5 §6.2).
    write_audit(
        "privacy.personal_data_deleted",
        target="BotUser",
        target_id=bot_user.id,
        payload={
            "actor": "customer",
            "scope": [s.step for s in result.steps],
            "all_ok": result.all_ok,
            "failed_steps": result.failed_steps,
        },
    )
    return result
