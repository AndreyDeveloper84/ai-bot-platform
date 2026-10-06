"""Phase 2.2 PR-C — concrete domain-bus subscribers.

Subscribers consume :class:`apps.eventbus.envelope.Envelope` instances
handed over by the dispatcher and write side-effect rows (audit log,
billing ledger, loyalty points — depending on subscriber).

Phase 2.2 ships exactly one real subscriber here:
:class:`AuditSubscriber`. It mirrors every dispatched event into
:mod:`apps.audit` as an AuditLog row — universal, low-risk, doesn't
depend on any new domain modules.

Registration:
  settings.DOMAIN_EVENT_SUBSCRIBERS = [
      "apps.eventbus.subscribers.AuditSubscriber",
      # …future real subscribers added here as they land
  ]

Or via env:
  DOMAIN_EVENT_SUBSCRIBERS="apps.eventbus.subscribers.AuditSubscriber"
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from apps.audit.services import write_audit
from apps.tenancy.context import tenant_scope

if TYPE_CHECKING:
    from apps.eventbus.envelope import Envelope

logger = logging.getLogger(__name__)


class AuditSubscriber:
    """Mirrors every dispatched domain event to an :class:`AuditLog` row.

    Mapping (envelope → AuditLog):
      - ``action``    ← ``envelope.event_name``                ("booking.created")
      - ``target``    ← ``event_name.split('.', 1)[0]``         ("booking")
      - ``target_id`` ← ``data['<domain>_id']`` if present,
                        else ``envelope.event_id`` as fallback
      - ``actor_id``  ← ``envelope.actor.id``
      - ``payload``   ← envelope-summary dict (see _build_payload)
      - tenant via :func:`tenant_scope` from ``envelope.tenant_id``

    Why a real subscriber here (not just analytics fan-out):
      - taxonomy §4 «Cross-cutting subscribers» lists Audit log as
        consuming every event with ``actor.type=human`` or admin domain;
        we go broader and audit ALL dispatched events for traceability.
      - It's the smallest real subscriber — no new module, no domain
        bridge — so it can ship as the first real consumer.

    Idempotency: ``event_id`` is the de-dup key (see taxonomy §4 subscriber
    contract). AuditLog has no unique constraint on ``payload->event_id``,
    so we defend against re-dispatch with an existence check before
    write_audit — see :meth:`handle` for the scenario. Loyalty / billing
    subscribers still need stricter dedup (DB-level unique constraints)
    because their writes carry state changes (balances, ledgers) that a
    pre-check race would corrupt; the audit row is observational so a
    pre-check is enough.
    """

    def handle(self, envelope: "Envelope") -> None:
        # Phase 2.2 cleanup (retro review #6): the dispatcher re-claims
        # any row that didn't reach ``is_dispatched=True`` on the previous
        # tick. When a multi-subscriber chain has one failing subscriber,
        # the transaction commits AuditLog rows from successful
        # subscribers while leaving the event pending → next tick writes
        # another AuditLog row. Pre-check + skip prevents the duplicate.
        #
        # JSON-field lookup cost is bounded by AuditLog volume (admin
        # writes only), not user traffic. No dedicated index needed at
        # current scale; add a GIN index on payload->>event_id if dispatch
        # latency regresses.
        from apps.audit.models import AuditLog

        if AuditLog.all_tenants.filter(payload__event_id=envelope.event_id).exists():
            logger.info(
                "audit.subscriber.skip_duplicate event_id=%s event=%s",
                envelope.event_id,
                envelope.event_name,
            )
            return

        domain = envelope.event_name.split(".", 1)[0]
        target_id_raw = envelope.data.get(f"{domain}_id")

        # AuditLog.actor_id and target_id are UUIDField — only set when
        # the envelope value is shaped like a UUID. Domain-specific IDs
        # (e.g. "bk-123", "ai_persona_v2") flow through `payload` instead;
        # the row stays queryable via action + payload__data lookups.
        target_uuid = _to_uuid_or_none(target_id_raw)
        actor_uuid = _to_uuid_or_none(envelope.actor.id)

        payload = _build_payload(envelope)
        if target_id_raw and target_uuid is None:
            payload["target_id_raw"] = target_id_raw
        if envelope.actor.id and actor_uuid is None:
            payload["actor_id_raw"] = envelope.actor.id

        tenant = _resolve_tenant(envelope.tenant_id)
        with tenant_scope(tenant):
            write_audit(
                action=envelope.event_name,
                target=domain,
                target_id=target_uuid,
                actor_id=actor_uuid,
                payload=payload,
            )


def _to_uuid_or_none(value):
    """Return a UUID if ``value`` parses as one, else None.

    Accepts UUID instances directly. String forms are validated via
    ``uuid.UUID(value)``; anything else returns None silently.
    """

    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _resolve_tenant(tenant_id):
    """Look up the Tenant ORM row from an envelope's tenant_id.

    Returns None if tenant_id is None (system.* events per taxonomy §8)
    or if the row no longer exists (tenant deleted while event was
    in-flight — unlikely but defensible).
    """

    if tenant_id is None:
        return None
    from apps.tenancy.models import Tenant

    return Tenant.objects.filter(pk=tenant_id).first()


def _build_payload(envelope: "Envelope") -> dict:
    """Flatten the envelope into a payload dict for AuditLog.

    Keeps the fields that matter for audit replay (event_id for
    correlation, version for schema diff, both correlation/causation
    chains, actor type/role, full data/metadata) while skipping
    duplicates (tenant_id is already the AuditLog.tenant FK).

    PII safety: the envelope's `data` and `metadata` are already PII-
    rejected at emit time (taxonomy §6, services.EventbusPiiViolation),
    so passing them through is safe.
    """

    return {
        "event_id": envelope.event_id,
        "event_version": envelope.event_version,
        "occurred_at": envelope.occurred_at.isoformat(),
        "actor_type": envelope.actor.type,
        "actor_role": envelope.actor.role,
        "correlation_id": envelope.correlation_id,
        "causation_id": envelope.causation_id,
        "data": envelope.data,
        "metadata": envelope.metadata,
    }


class CatalogConsentSubscriber:
    """Смена согласия — в каталог (DRF-2776, решение владельца D от 05.10).

    Каталог хранит данные под согласиями (параметры тела под
    ``personal_calculation``, health_flags под ``health``) и стирает их при
    отзыве; по ``food_diary_processing`` перестаёт слать бьюти-инсайт. Реестр
    согласий живёт в боте, поэтому весть должна уехать отсюда — клиент
    :mod:`apps.integrations.ayla.consent_events_client`.

    Остальные события — не его: ``handle`` возвращается без исключения, иначе
    каждое чужое событие уходило бы в DLQ. Какие события он доставляет,
    объявлено в :attr:`delivers` — по нему сторож живого режима диспетчера
    отличает «доставлено потребителю» от «записано в журнал».

    Субъект каталогу называет ``X-External-User-ID``, а не тело: в событии
    ``customer_id`` — UUID ``BotUser``, каталогу он ничего не говорит. Если
    ``BotUser`` уже стёрт (forget-all), заголовок собрать не из чего, и
    доставлять некому: строка помечается доставленной с исходом
    ``no_subject_on_bot`` — симметрично ``no_subject`` каталога. Стирание
    по удалению человека идёт своим путём (DRF-1699), не через это событие.

    Отказ каталога — исключение, и подписчик его не ловит: диспетчер
    оставляет строку недоставленной, ретраит и кладёт в DLQ с текстом. Это и
    есть «результат доставки» в журнале. Успех пишется отдельной строкой
    аудита ``consent.delivery.catalog`` с исходом каталога и ИМЕНАМИ
    стёртых полей. Ключ строки — ``consent_event_id``, а не ``event_id``:
    :class:`AuditSubscriber` пропускает событие, если ``event_id`` в журнале
    уже есть, и наша строка, записанная раньше его, отняла бы у журнала
    само изменение.

    Идемпотентность — у каталога (квитанция по ``event_id``, повтор отвечает
    ``duplicate``); строка аудита пишется один раз на событие.
    """

    delivers = frozenset({"customer.consent.changed"})

    def handle(self, envelope: "Envelope") -> None:
        if envelope.event_name not in self.delivers:
            return

        from apps.audit.models import AuditLog
        from apps.identity.models import BotUser
        from apps.integrations.ayla.consent_events_client import post_consent_event
        from apps.integrations.ayla.user_proxy import external_user_id_for

        data = envelope.data
        bot_user = BotUser.all_tenants.filter(pk=_to_uuid_or_none(data.get("customer_id"))).first()
        erased: tuple[str, ...] = ()
        if bot_user is None:
            outcome = "no_subject_on_bot"
            logger.info("consent.delivery.catalog.no_subject_on_bot event_id=%s", envelope.event_id)
        else:
            body = {
                "event_id": envelope.event_id,
                "consent_type": data.get("consent_type"),
                "granted": data.get("granted"),
                "granted_at": data.get("granted_at"),
            }
            if data.get("granted_via"):
                body["granted_via"] = data["granted_via"]
            receipt = post_consent_event(external_user_id=external_user_id_for(bot_user), body=body)
            outcome, erased = receipt.outcome, receipt.erased
            logger.info(
                "consent.delivery.catalog event_id=%s type=%s granted=%s outcome=%s erased=%d",
                envelope.event_id,
                data.get("consent_type"),
                data.get("granted"),
                outcome,
                len(erased),
            )

        if AuditLog.all_tenants.filter(
            action="consent.delivery.catalog", payload__consent_event_id=envelope.event_id
        ).exists():
            return
        with tenant_scope(_resolve_tenant(envelope.tenant_id)):
            write_audit(
                action="consent.delivery.catalog",
                target="consent",
                payload={
                    "consent_event_id": envelope.event_id,
                    "consent_type": data.get("consent_type"),
                    "granted": data.get("granted"),
                    "outcome": outcome,
                    "erased": list(erased),
                },
            )
