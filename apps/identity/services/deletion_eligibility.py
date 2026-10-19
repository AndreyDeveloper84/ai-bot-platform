"""Можно ли удалить человека — оценка по операциям, не роль (DRF-2919 п.2).

Решение владельца 19.10 (#2359, п.2): ограничение удаления держится на
**реальных неурегулированных обязательствах**, а не на факте роли. Запрещено
как единственное основание блока: ``primary_role``, ``is_solo_provider``,
``Tenant.last_catalog_sync_ok_at``, ``Tenant.is_active``. Модуль их не читает.
``resolve_role`` не трогается: это отдельная серверная проверка.

### Контракт

Не один boolean, а результат по каждой из четырёх операций — они друг друга
не подменяют: выход из кабинета (``STAFF_EXIT``), закрытие кабинета
(``CABINET_CLOSURE``), деактивация личного аккаунта
(``ACCOUNT_DEACTIVATION``), удаление ПДн (``PERSONAL_DATA_ERASURE``). У
каждой — исход, коды причин, следующие действия и чьё это обязательство.
``request_accepted`` всегда ``True``: запрос принят к рассмотрению и получил
мотивированный ответ. Необратимое при этом начинается только там, где исход
это разрешает (:func:`DeletionEligibility.erasure_may_start`).

### Сигналы

* **Свои будущие записи мастера** — зеркало Ayla
  (``master_deactivation._count_future_mirror_bookings``) и локальные
  подтверждённые ``BookingRequest`` (тем же модулем — владелец чтения
  ``BookingRequest`` вне ``apps/booking`` один); берётся большее. Отставание зеркала
  без ``last_catalog_sync_ok_at`` не измерить, поэтому ноль в зеркале —
  не доказательство пустоты. Допущение сознательное: где у мастера есть
  хоть какой-то след деятельности, а проверить нечем, — ``VERIFICATION_REQUIRED``,
  не молчаливый ``ELIGIBLE``.
* **Свои деньги мастера** — Ayla C2 по ``CatalogMaster.ayla_user_id``:
  ``past_due`` или начисленные комиссии — долг самого мастера (SELF).
  C3 ``pending_amount`` — выплата, которую должна мастеру организация.
* **Деньги клиента** — ``PaymentMirror`` по живым записям человека как
  клиента: удержание или оплата, которую, возможно, вернёт салон.
* **Состав кабинета** — прямые запросы, не ``is_solo_provider``: другой
  действующий ``TenantStaff`` и другие привязанные мастера.
* **Хранение** — у человека есть записи или платежи: история переживает
  удаление в обезличенном виде (``privacy.delete_personal_data``). Это
  ``PARTIAL_WITH_RETENTION``, никогда не блок.

Мастер без ``ayla_user_id`` и без следа деятельности (ни одной строки
зеркала, ни одной локальной будущей записи) — брошенная регистрация: у Ayla нет
специалиста, значит нет подписки и выплат, финансы ``NOT_APPLICABLE``.
С таким следом — ``VERIFICATION_REQUIRED``: деньги есть где-то, а
проверить их не по чему.

Человек — все его оболочки, как у каскада стирания
(``privacy._person_shell_ids``), а карточки мастера — по двум фактам
идентичности: ``linked_bot_user`` в боте и ``ayla_user_id`` из каталога.

### Политика

Что из найденного останавливает удаление ПДн и деактивацию — решает одна
функция, :func:`blocks_personal_erasure`. Свои обязательства и
непроверяемость останавливают всегда. Два вопроса ждут владельца, и их
база (§5: личное не блокируется чужим) — в двух константах рядом с ней.

Модуль только читает. Закрытие кабинета не исполняется: для пустого соло
он отдаёт путь ``CLOSE_EMPTY_CABINET``, а сам teardown — отдельная задача.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


class Operation(StrEnum):
    STAFF_EXIT = "STAFF_EXIT"
    CABINET_CLOSURE = "CABINET_CLOSURE"
    ACCOUNT_DEACTIVATION = "ACCOUNT_DEACTIVATION"
    PERSONAL_DATA_ERASURE = "PERSONAL_DATA_ERASURE"


class Outcome(StrEnum):
    IMMEDIATE = "IMMEDIATE"
    SETTLE_FIRST = "SETTLE_FIRST"
    PARTIAL_WITH_RETENTION = "PARTIAL_WITH_RETENTION"
    REFUSED_IN_PART = "REFUSED_IN_PART"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class Reason(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    ACTIVE_BOOKINGS = "ACTIVE_BOOKINGS"
    UNFINISHED_SERVICES = "UNFINISHED_SERVICES"
    STAFF_TRANSFER_REQUIRED = "STAFF_TRANSFER_REQUIRED"
    FINANCIAL_OBLIGATIONS = "FINANCIAL_OBLIGATIONS"
    RETENTION_REQUIRED = "RETENTION_REQUIRED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"


class Action(StrEnum):
    PROCEED = "PROCEED"
    SETTLE_BOOKINGS = "SETTLE_BOOKINGS"
    SETTLE_FINANCES = "SETTLE_FINANCES"
    TRANSFER_MANAGEMENT = "TRANSFER_MANAGEMENT"
    CLOSE_EMPTY_CABINET = "CLOSE_EMPTY_CABINET"
    CONTACT_SUPPORT = "CONTACT_SUPPORT"
    EXPLAIN_RETAINED_DATA = "EXPLAIN_RETAINED_DATA"


class ObligationOwner(StrEnum):
    SELF = "SELF"
    CABINET_OWNER = "CABINET_OWNER"
    ORGANIZATION = "ORGANIZATION"
    UNKNOWN = "UNKNOWN"


class Topic(StrEnum):
    """О чём находка — по этому ключу решает политика."""

    OWN_BOOKINGS = "own_bookings"
    OWN_DEBT = "own_debt"
    MONEY_OWED_TO_PERSON = "money_owed_to_person"
    CABINET_TRANSFER = "cabinet_transfer"
    CABINET_BOOKINGS = "cabinet_bookings"
    IDENTITY = "identity"


#: Ждёт владельца (развилка 2). База §5: владелец кабинета с другим
#: персоналом удаляет свои ПДн; роль OWNER исполнитель каталога оставляет
#: («нужна передача салона»). ``True`` — такой человек сначала передаёт
#: управление (``STAFF_TRANSFER_REQUIRED`` на удалении ПДн).
BLOCK_ERASURE_ON_CABINET_TRANSFER = False

#: Ждёт владельца (развилка 3). База: деньги, которые должна человеку
#: организация (возврат клиенту, выплата мастеру), удаление не останавливают
#: — записи о них хранятся по закону (``PARTIAL_WITH_RETENTION``). ``True`` —
#: сначала урегулировать (``SETTLE_FINANCES`` на удалении ПДн).
BLOCK_ERASURE_ON_MONEY_OWED_TO_PERSON = False


@dataclass(frozen=True)
class Finding:
    """Одно найденное обстоятельство."""

    topic: Topic
    reason: Reason
    owner: ObligationOwner
    action: Action


def blocks_personal_erasure(finding: Finding) -> bool:
    """Останавливает ли находка удаление ПДн и деактивацию аккаунта.

    Единственная точка политики. Своё обязательство и непроверяемость
    своего — всегда. Чужое (кабинета, организации) — только если владелец
    так решит; см. две константы выше.
    """
    if finding.topic is Topic.CABINET_TRANSFER:
        return BLOCK_ERASURE_ON_CABINET_TRANSFER
    if finding.topic is Topic.MONEY_OWED_TO_PERSON:
        return BLOCK_ERASURE_ON_MONEY_OWED_TO_PERSON
    if finding.topic is Topic.CABINET_BOOKINGS:
        return False
    return True


@dataclass(frozen=True)
class OperationResult:
    outcome: Outcome
    reasons: tuple[Reason, ...]
    next_actions: tuple[Action, ...]
    obligation_owner: ObligationOwner

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reasons": [r.value for r in self.reasons],
            "next_actions": [a.value for a in self.next_actions],
            "obligation_owner": self.obligation_owner.value,
        }


#: Исходы, при которых удаление ПДн может начаться прямо сейчас.
ERASURE_MAY_START = frozenset({Outcome.IMMEDIATE, Outcome.PARTIAL_WITH_RETENTION})


@dataclass(frozen=True)
class DeletionEligibility:
    operations: dict[Operation, OperationResult]
    request_accepted: bool = True

    @property
    def erasure(self) -> OperationResult:
        return self.operations[Operation.PERSONAL_DATA_ERASURE]

    @property
    def erasure_may_start(self) -> bool:
        return self.erasure.outcome in ERASURE_MAY_START

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_accepted": self.request_accepted,
            "operations": {op.value: res.as_dict() for op, res in self.operations.items()},
        }


# --- сигналы ---------------------------------------------------------------


@dataclass
class _Scan:
    findings: list[Finding] = field(default_factory=list)
    #: Строки ``TenantStaff`` и карточки — есть ли у человека кабинет вообще.
    has_working_role: bool = False
    #: Салоны, где человек — действующий владелец.
    owned_tenant_ids: set[uuid.UUID] = field(default_factory=set)
    #: Есть история, которая переживёт удаление (записи, платежи).
    has_history: bool = False

    def add(self, topic: Topic, reason: Reason, owner: ObligationOwner, action: Action) -> None:
        finding = Finding(topic, reason, owner, action)
        if finding not in self.findings:
            self.findings.append(finding)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _call_billing(fn, card) -> Any:
    """Вызов C2/C3; ``None`` — ответа нет (любой сбой, fail-closed)."""
    from apps.master_api.services.billing import ProxyStatus

    try:
        result = fn(card)
    except Exception:  # noqa: BLE001 — клиент без настройки бросает в конструкторе
        logger.warning("identity.deletion_eligibility.billing_unavailable card=%s", card.pk)
        return None
    if result.status is ProxyStatus.OK:
        return result.payload
    if result.status is ProxyStatus.NOT_FOUND:
        # Ayla этого специалиста не знает — ни подписки, ни выплат.
        return {}
    return None


def _scan_master_card(scan: _Scan, card) -> None:
    from apps.admin_api.services.master_deactivation import (
        _count_future_mirror_bookings,
        _query_future_confirmed_bookings,
    )
    from apps.booking.models import RemoteBookingProxy
    from apps.catalog.specialist_ref import specialist_keys
    from apps.master_api.services.billing import (
        billing_status_for_master,
        payout_preview_for_master,
    )

    mirror_future = _count_future_mirror_bookings(card)
    local_future = len(_query_future_confirmed_bookings(card))
    if max(mirror_future, local_future) > 0:
        scan.add(
            Topic.OWN_BOOKINGS, Reason.ACTIVE_BOOKINGS, ObligationOwner.SELF, Action.SETTLE_BOOKINGS
        )

    has_trace = (
        local_future > 0
        or RemoteBookingProxy.all_tenants.filter(
            tenant_id=card.tenant_id, specialist_id__in=specialist_keys(card)
        ).exists()
    )
    if has_trace:
        scan.has_history = True

    if not card.ayla_user_id:
        if has_trace:
            # Деятельность была, а ключа к деньгам нет — проверить нечем.
            scan.add(
                Topic.OWN_DEBT,
                Reason.VERIFICATION_REQUIRED,
                ObligationOwner.UNKNOWN,
                Action.CONTACT_SUPPORT,
            )
        return

    status = _call_billing(billing_status_for_master, card)
    if status is None:
        scan.add(
            Topic.OWN_DEBT,
            Reason.VERIFICATION_REQUIRED,
            ObligationOwner.UNKNOWN,
            Action.CONTACT_SUPPORT,
        )
    else:
        subscription = status.get("subscription") or {}
        fees = status.get("fees") or {}
        if subscription.get("status") == "past_due" or _decimal(fees.get("pending_total")) > 0:
            scan.add(
                Topic.OWN_DEBT,
                Reason.FINANCIAL_OBLIGATIONS,
                ObligationOwner.SELF,
                Action.SETTLE_FINANCES,
            )

    payout = _call_billing(payout_preview_for_master, card)
    if payout is None:
        scan.add(
            Topic.MONEY_OWED_TO_PERSON,
            Reason.VERIFICATION_REQUIRED,
            ObligationOwner.ORGANIZATION,
            Action.CONTACT_SUPPORT,
        )
    elif _decimal(payout.get("pending_amount")) > 0:
        scan.add(
            Topic.MONEY_OWED_TO_PERSON,
            Reason.FINANCIAL_OBLIGATIONS,
            ObligationOwner.ORGANIZATION,
            Action.SETTLE_FINANCES,
        )


def _scan_cabinet(scan: _Scan, tenant_id: uuid.UUID, shell_ids: set[uuid.UUID]) -> None:
    """Обязательства кабинета, где человек — владелец. Не личные (§5)."""
    from apps.admin_api.services.master_deactivation import MIRROR_LIVE_STATUSES
    from apps.booking.models import RemoteBookingProxy
    from apps.catalog.models import CatalogMaster
    from apps.catalog.specialist_ref import specialist_keys
    from apps.tenancy.models import TenantStaff
    from django.utils import timezone

    other_staff = (
        TenantStaff.all_tenants.filter(
            tenant_id=tenant_id, deactivated_at__isnull=True, bot_user__deleted_at__isnull=True
        )
        .exclude(bot_user_id__in=shell_ids)
        .exists()
    )
    other_masters = (
        CatalogMaster.all_tenants.filter(
            tenant_id=tenant_id, archived_at__isnull=True, linked_bot_user_id__isnull=False
        )
        .exclude(linked_bot_user_id__in=shell_ids)
        .exists()
    )
    if other_staff or other_masters:
        scan.add(
            Topic.CABINET_TRANSFER,
            Reason.STAFF_TRANSFER_REQUIRED,
            ObligationOwner.CABINET_OWNER,
            Action.TRANSFER_MANAGEMENT,
        )

    # Записи кабинета минус свои: свои уже учтены как личные (SELF).
    live = RemoteBookingProxy.all_tenants.filter(
        tenant_id=tenant_id, status__in=MIRROR_LIVE_STATUSES, start_at__gte=timezone.now()
    )
    own = sum(
        live.filter(specialist_id__in=specialist_keys(card)).count()
        for card in CatalogMaster.all_tenants.filter(
            tenant_id=tenant_id, linked_bot_user_id__in=shell_ids
        )
    )
    if live.count() > own:
        scan.add(
            Topic.CABINET_BOOKINGS,
            Reason.ACTIVE_BOOKINGS,
            ObligationOwner.ORGANIZATION,
            Action.SETTLE_BOOKINGS,
        )


def _scan_customer_money(scan: _Scan, shell_ids: set[uuid.UUID]) -> None:
    from apps.admin_api.services.master_deactivation import MIRROR_LIVE_STATUSES
    from apps.booking.models import PaymentMirror, RemoteBookingProxy

    # Зеркало платежей ключится записью, не человеком: платежи человека —
    # платежи по его записям.
    visits = RemoteBookingProxy.all_tenants.filter(bot_user_id__in=shell_ids)
    if visits.exists():
        scan.has_history = True
    live = visits.filter(status__in=MIRROR_LIVE_STATUSES).values_list("tenant_id", "appointment_id")
    for tenant_id, appointment_id in live:
        if PaymentMirror.all_tenants.filter(
            tenant_id=tenant_id,
            appointment_id=appointment_id,
            capture_state__in=(
                PaymentMirror.CaptureState.AUTHORIZED,
                PaymentMirror.CaptureState.CAPTURED,
            ),
        ).exists():
            scan.add(
                Topic.MONEY_OWED_TO_PERSON,
                Reason.FINANCIAL_OBLIGATIONS,
                ObligationOwner.ORGANIZATION,
                Action.SETTLE_FINANCES,
            )
            return


def _scan(bot_user: BotUser) -> _Scan:
    from apps.catalog.models import CatalogMaster
    from apps.identity.services.privacy import _person_shell_ids, resolve_person_link
    from apps.tenancy.models import TenantStaff
    from django.db.models import Q

    scan = _Scan()
    # Чистое чтение: без ``resolve_upstream`` — не заводить прокси в Ayla
    # ради вопроса «можно ли».
    link = resolve_person_link(bot_user, resolve_upstream=False)
    if link.conflict:
        scan.add(
            Topic.IDENTITY,
            Reason.VERIFICATION_REQUIRED,
            ObligationOwner.UNKNOWN,
            Action.CONTACT_SUPPORT,
        )
    shell_ids = set(
        BotUser.all_tenants.filter(
            id__in=_person_shell_ids(bot_user, link), deleted_at__isnull=True
        ).values_list("id", flat=True)
    ) | {bot_user.id}

    for row in TenantStaff.all_tenants.filter(
        bot_user_id__in=shell_ids, deactivated_at__isnull=True
    ):
        scan.has_working_role = True
        if row.role == TenantStaff.Role.OWNER:
            scan.owned_tenant_ids.add(row.tenant_id)

    # Два факта идентичности: привязка в боте и ключ Ayla из каталога.
    cards_q = Q(linked_bot_user_id__in=shell_ids)
    if link.ayla_user_id is not None and not link.conflict:
        cards_q |= Q(ayla_user_id=link.ayla_user_id)
    for card in CatalogMaster.all_tenants.filter(cards_q, archived_at__isnull=True):
        scan.has_working_role = True
        _scan_master_card(scan, card)

    for tenant_id in sorted(scan.owned_tenant_ids, key=str):
        _scan_cabinet(scan, tenant_id, shell_ids)

    _scan_customer_money(scan, shell_ids)
    return scan


# --- сведение в результат ---------------------------------------------------


def _owner_of(findings: list[Finding]) -> ObligationOwner:
    owners = {f.owner for f in findings}
    for candidate in (
        ObligationOwner.SELF,
        ObligationOwner.CABINET_OWNER,
        ObligationOwner.ORGANIZATION,
    ):
        if candidate in owners:
            return candidate
    return ObligationOwner.UNKNOWN


def _blocked(findings: list[Finding]) -> OperationResult:
    only_unverified = all(f.reason is Reason.VERIFICATION_REQUIRED for f in findings)
    return OperationResult(
        outcome=Outcome.REFUSED_IN_PART if only_unverified else Outcome.SETTLE_FIRST,
        reasons=tuple(dict.fromkeys(f.reason for f in findings)),
        next_actions=tuple(dict.fromkeys(f.action for f in findings)),
        obligation_owner=_owner_of(findings),
    )


def _clear(*, extra: tuple[Action, ...] = ()) -> OperationResult:
    return OperationResult(
        outcome=Outcome.IMMEDIATE,
        reasons=(Reason.ELIGIBLE,),
        next_actions=(Action.PROCEED, *extra),
        obligation_owner=ObligationOwner.SELF,
    )


_NOT_APPLICABLE = OperationResult(
    outcome=Outcome.NOT_APPLICABLE,
    reasons=(),
    next_actions=(),
    obligation_owner=ObligationOwner.SELF,
)


def _personal(scan: _Scan, *, retention_applies: bool) -> OperationResult:
    """Деактивация аккаунта и удаление ПДн — личные операции (§5)."""
    blocking = [f for f in scan.findings if blocks_personal_erasure(f)]
    if blocking:
        return _blocked(blocking)
    kept = [f for f in scan.findings if not blocks_personal_erasure(f)]
    if retention_applies and (kept or scan.has_history):
        reasons = [Reason.RETENTION_REQUIRED, *(f.reason for f in kept)]
        return OperationResult(
            outcome=Outcome.PARTIAL_WITH_RETENTION,
            reasons=tuple(dict.fromkeys(reasons)),
            next_actions=(Action.PROCEED, Action.EXPLAIN_RETAINED_DATA),
            obligation_owner=_owner_of(kept) if kept else ObligationOwner.SELF,
        )
    return _clear()


def _staff_exit(scan: _Scan) -> OperationResult:
    if not scan.has_working_role:
        return _NOT_APPLICABLE
    # Уйти из кабинета мешают свои дела и, у владельца, некому передать
    # управление. Деньги, которые должны человеку, уходу не мешают.
    blocking = [
        f
        for f in scan.findings
        if f.topic in (Topic.OWN_BOOKINGS, Topic.OWN_DEBT, Topic.CABINET_TRANSFER, Topic.IDENTITY)
    ]
    return _blocked(blocking) if blocking else _clear()


def _cabinet_closure(scan: _Scan) -> OperationResult:
    if not scan.owned_tenant_ids:
        return _NOT_APPLICABLE
    blocking = [f for f in scan.findings if f.topic is not Topic.MONEY_OWED_TO_PERSON]
    if blocking:
        result = _blocked(blocking)
        # Кабинет — дело организации, даже когда препятствие — свои записи.
        return OperationResult(
            outcome=result.outcome,
            reasons=result.reasons,
            next_actions=result.next_actions,
            obligation_owner=ObligationOwner.ORGANIZATION,
        )
    # Пустой кабинет: путь есть, но само закрытие — отдельная задача.
    return OperationResult(
        outcome=Outcome.IMMEDIATE,
        reasons=(Reason.ELIGIBLE,),
        next_actions=(Action.CLOSE_EMPTY_CABINET,),
        obligation_owner=ObligationOwner.ORGANIZATION,
    )


def revocation_retention(eligibility: DeletionEligibility) -> OperationResult:
    """Исход для отзыва согласия на хранение — пятая дверь, решение владельца 19.10 (б).

    Отзыв согласия по 152-ФЗ нельзя «не выполнить», поэтому здесь нет
    ``SETTLE_FIRST``/``REFUSED_IN_PART``: что на четырёх дверях удаления
    остановило бы стирание, здесь становится основанием удержать законный
    минимум, а не отказом. ``IMMEDIATE`` остаётся ``IMMEDIATE``.

    Законный минимум каскад уже держит сам и для каждого: транзакционные
    зеркала (записи, платежи — без ПДн), оболочку ``BotUser`` с
    ``ayla_user_id`` и карточку мастера — ключ биллинга Ayla, где живёт долг.
    Стирается остальное. Этот результат называет основания удержания
    кодами, без сумм и без названий салонов.
    """
    erasure = eligibility.erasure
    if erasure.outcome is Outcome.IMMEDIATE:
        return erasure
    reasons = [Reason.RETENTION_REQUIRED, *(r for r in erasure.reasons if r is not Reason.ELIGIBLE)]
    return OperationResult(
        outcome=Outcome.PARTIAL_WITH_RETENTION,
        reasons=tuple(dict.fromkeys(reasons)),
        next_actions=(Action.PROCEED, Action.EXPLAIN_RETAINED_DATA),
        obligation_owner=erasure.obligation_owner,
    )


def deletion_eligibility(bot_user: BotUser) -> DeletionEligibility:
    """Оценить удаление человека по четырём операциям. Только чтение."""
    scan = _scan(bot_user)
    result = DeletionEligibility(
        operations={
            Operation.STAFF_EXIT: _staff_exit(scan),
            Operation.CABINET_CLOSURE: _cabinet_closure(scan),
            Operation.ACCOUNT_DEACTIVATION: _personal(scan, retention_applies=False),
            Operation.PERSONAL_DATA_ERASURE: _personal(scan, retention_applies=True),
        }
    )
    logger.info(
        "identity.deletion_eligibility bot_user=%s erasure=%s reasons=%s",
        bot_user.id,
        result.erasure.outcome.value,
        [r.value for r in result.erasure.reasons],
    )
    return result


__all__ = [
    "BLOCK_ERASURE_ON_CABINET_TRANSFER",
    "BLOCK_ERASURE_ON_MONEY_OWED_TO_PERSON",
    "ERASURE_MAY_START",
    "Action",
    "DeletionEligibility",
    "Finding",
    "ObligationOwner",
    "Operation",
    "OperationResult",
    "Outcome",
    "Reason",
    "Topic",
    "blocks_personal_erasure",
    "deletion_eligibility",
    "revocation_retention",
]
