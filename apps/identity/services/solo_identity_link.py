"""Operator-assisted identity linking соло-мастера — Phase 0 (§6 пакета 12.09).

Владелец: identity-токен боту не выдаём (NO-GO). Связь личности делает
**оператор контролируемым действием**, не правкой БД; бот — канал и UX,
identity-сервис — «кто этот человек», каталог — «кем он является как
мастер». Здесь — бот-половина: состояния, аудит-пакет, провенанс.

### Как связь на самом деле происходит

Ключ личности (``CatalogMaster.ayla_user_id``) записывает единственная
дверь — :func:`solo_ayla_link.link_solo_provider_to_ayla`, и только
настоящим (не прокси) ключом из ответа каталога. Оператор действует в
КАТАЛОГЕ: действие «Связать с Ayla» (beautygo_backend#339) привязывает
внешнюю личность ``bot:max:<id>`` к настоящему специалисту. После этого
каталог на ``resolve_identity`` отвечает настоящим ключом — и оператор в
админке БОТА нажимает «Проверить связь с Ayla»: :func:`confirm_by_operator`
повторяет попытку, и если ключ настоящий — записывает ``LINKED`` с
``provenance=OPERATOR_VERIFIED``, ``operator_id``, временем. Ни одно из
двух действий не даёт боту identity-полномочий: бот по-прежнему только
читает ответ каталога.

### Состояния

``PENDING`` — с момента регистрации (аудит-пакет собран тогда же);
``LINKED`` — ключ настоящий и записан; ``REJECTED`` — оператор отказал с
причиной из таксономии; человеку уходит безопасное сообщение, причина —
нет. Публикация (продажа) требует ``LINKED``: ``master_state.sale_block``
отвечает ``ayla_unlinked`` и на PENDING, и на REJECTED — у отклонённого
ключа нет и не будет, а единственная дверь записи отказывает прокси.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from apps.audit.services import write_audit
from apps.identity.models import SoloIdentityLink

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OperatorLinkOutcome:
    """Что случилось после нажатия оператора — словами для экрана."""

    status: str
    refusal: str | None = None

    @property
    def linked(self) -> bool:
        return self.status == SoloIdentityLink.Status.LINKED


def open_link(master, *, bot_user, tenant, phone: str = "") -> SoloIdentityLink:
    """Завести запись ``PENDING`` с аудит-пакетом. Идемпотентно по мастеру."""
    link, created = SoloIdentityLink.objects.get_or_create(
        master=master,
        defaults={
            "solo_registration_id": tenant.id,
            "channel": bot_user.channel,
            "channel_user_id": bot_user.channel_user_id,
            "tenant_id_snapshot": tenant.id,
            "phone": phone or getattr(bot_user, "phone", "") or "",
        },
    )
    if created:
        write_audit(
            "identity.solo_link.pending",
            target="CatalogMaster",
            target_id=master.pk,
            payload={
                "solo_registration_id": str(tenant.id),
                "channel": bot_user.channel,
                "channel_user_id": bot_user.channel_user_id,
                "tenant_id": str(tenant.id),
                "master_id": str(master.pk),
                "phone_present": bool(link.phone),
            },
        )
    return link


def record_attempt(link: SoloIdentityLink, *, refusal: str | None, ayla_user_id=None) -> None:
    """Зафиксировать исход автопопытки (при регистрации)."""
    link.last_attempt_at = timezone.now()
    link.last_attempt_refusal = refusal or ""
    if refusal is None and link.status != SoloIdentityLink.Status.LINKED:
        link.status = SoloIdentityLink.Status.LINKED
        link.provenance = SoloIdentityLink.Provenance.CATALOG_RESOLVED
        link.decided_at = link.last_attempt_at
        link.ayla_user_id = ayla_user_id
    link.save()


def confirm_by_operator(link: SoloIdentityLink, *, bot_user, operator) -> OperatorLinkOutcome:
    """Контролируемое действие «Проверить связь с Ayla».

    Повторяет автопопытку (тот же :func:`solo_link_attempt.attempt_solo_link`
    — тот же сторож против прокси-ключа). Настоящий ключ → ``LINKED`` с
    ``OPERATOR_VERIFIED`` и ``operator_id``; отказ → остаётся ``PENDING``, а
    причина записана машинным именем, чтобы оператор видел, чего не
    хватает (обычно — действия #339 в каталоге).
    """
    from apps.identity.services.solo_link_attempt import attempt_solo_link

    if link.status == SoloIdentityLink.Status.REJECTED:
        return OperatorLinkOutcome(status=link.status, refusal="rejected")

    refusal = attempt_solo_link(link.master, bot_user)
    link.last_attempt_at = timezone.now()
    link.last_attempt_refusal = refusal or ""
    if refusal is not None:
        link.save(update_fields=["last_attempt_at", "last_attempt_refusal"])
        return OperatorLinkOutcome(status=link.status, refusal=refusal)

    link.master.refresh_from_db(fields=["ayla_user_id"])
    link.status = SoloIdentityLink.Status.LINKED
    link.provenance = SoloIdentityLink.Provenance.OPERATOR_VERIFIED
    link.operator_id = _int_pk(operator)
    link.operator_username = getattr(operator, "username", "") or ""
    link.decided_at = link.last_attempt_at
    link.ayla_user_id = link.master.ayla_user_id
    link.save()
    write_audit(
        "identity.solo_link.linked",
        target="CatalogMaster",
        target_id=link.master_id,
        payload={
            "provenance": link.provenance,
            "operator": link.operator_username,
            "solo_registration_id": str(link.solo_registration_id),
        },
        actor_id=str(link.operator_id) if link.operator_id is not None else None,
    )
    logger.info(
        "identity.solo_link.linked master=%s operator=%s", link.master_id, link.operator_username
    )
    return OperatorLinkOutcome(status=link.status)


def reject_by_operator(link: SoloIdentityLink, *, operator, reason: str, note: str = "") -> None:
    """Контролируемый отказ: причина — из таксономии, человеку — не она."""
    if reason not in SoloIdentityLink.RejectReason.values:
        raise ValueError(f"unknown reject reason {reason!r}")
    link.status = SoloIdentityLink.Status.REJECTED
    link.reject_reason = reason
    link.reject_note = note[:500]
    link.operator_id = _int_pk(operator)
    link.operator_username = getattr(operator, "username", "") or ""
    link.decided_at = timezone.now()
    link.save()
    write_audit(
        "identity.solo_link.rejected",
        target="CatalogMaster",
        target_id=link.master_id,
        payload={"reason": reason, "operator": link.operator_username},
        actor_id=str(link.operator_id) if link.operator_id is not None else None,
    )


def _int_pk(operator) -> int | None:
    pk = getattr(operator, "pk", None)
    return pk if isinstance(pk, int) else None


#: Безопасное сообщение человеку при REJECTED — без причины (§6):
#: причина — внутренняя таксономия для оператора.
REJECTED_RECOVERY_TEXT = (
    "Кабинет мастера пока не подтверждён. Напишите в поддержку — мы "
    "разберёмся и ответим, что делать дальше. Записи клиентов до этого "
    "не принимаются."
)
