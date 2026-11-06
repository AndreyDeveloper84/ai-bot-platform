"""Зеркало подписки каталога на тенанте бота (DRF-2984, PR-B1).

Канон подписки — ``billing/`` каталога (ADR-0009 правило 1: бот зеркалит, не
владеет; решение главного окна 20.10, В-1). Здесь одна пишущая функция; её
зовёт только потребитель событий ``apps.eventbus.consumers.billing``.

Свойства, ради которых модуль существует:

* **Старое событие не перетирает новое.** Outbox доставляет не по порядку и с
  повторами; запись идёт одним условным ``UPDATE … WHERE subscription_status_at
  IS NULL OR subscription_status_at < occurred_at``, без чтения-потом-записи —
  две параллельные доставки не гонятся.
* **Закрытый словарь.** Статус вне :class:`SubscriptionStatus` и тариф вне
  ``solo``/``salon`` не пишутся: неизвестное значение не становится «активна».
* **Тенант не найден — не ошибка доставки.** Возвращается ``False``, потребитель
  логирует; повтор события тенанта не создаст.

Гейтов поведения здесь нет — они PR-B2, за флагом ``BILLING_ENABLED``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from django.db.models import Case, F, Q, Value, When

from apps.tenancy.models import SubscriptionStatus, Tenant

#: Тарифы каталога (``TariffPlan.Code``) — то, что зеркалится в ``Tenant.plan``.
MIRRORED_PLANS: frozenset[str] = frozenset({"solo", "salon"})


class BillingMirrorError(ValueError):
    """Значение вне закрытого словаря зеркала. Ошибка производителя события."""


@dataclass(frozen=True)
class MirrorUpdate:
    """Что событие сообщает о подписке. ``None`` — поле событие не несёт, не трогать."""

    status: SubscriptionStatus
    plan: str | None = None
    trial_ends_at: dt.date | None = None
    current_period_end: dt.date | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.status, SubscriptionStatus)
            or self.status is SubscriptionStatus.NONE
        ):
            # "none" — состояние до первого события, событие его не сообщает.
            raise BillingMirrorError(f"статус вне словаря событий: {self.status!r}")
        if self.plan is not None and self.plan not in MIRRORED_PLANS:
            raise BillingMirrorError(f"тариф вне словаря: {self.plan!r}")


def apply_subscription_event(
    tenant_id: str, update: MirrorUpdate, *, occurred_at: dt.datetime
) -> bool:
    """Записать ``update`` в зеркало тенанта, если событие новее записанного.

    Возвращает ``True``, если строка обновлена; ``False`` — тенанта нет или
    зеркало уже записано более новым событием.
    """

    if occurred_at.tzinfo is None or occurred_at.utcoffset() is None:
        raise BillingMirrorError("occurred_at — с часовым поясом")
    fields: dict[str, object] = {
        "subscription_status": update.status.value,
        "subscription_status_at": occurred_at,
    }
    if update.plan is not None:
        fields["plan"] = update.plan
        # Тариф вступил в силу — ожидание той же смены снимается (B2: plan_changed).
        reached = Q(pending_plan=update.plan)
        fields["pending_plan"] = Case(When(reached, then=Value("")), default=F("pending_plan"))
        fields["pending_plan_from"] = Case(
            When(reached, then=Value(None)), default=F("pending_plan_from")
        )
    if update.trial_ends_at is not None:
        fields["trial_ends_at"] = update.trial_ends_at
    if update.current_period_end is not None:
        fields["current_period_end"] = update.current_period_end
    # all_objects: событие о выключенном тенанте тоже зеркалится.
    updated = (
        Tenant.all_objects.filter(pk=tenant_id)
        .filter(Q(subscription_status_at__isnull=True) | Q(subscription_status_at__lt=occurred_at))
        .update(**fields)
    )
    return updated == 1


def apply_pending_plan(tenant_id: str, *, plan: str, effective_from: dt.date) -> bool:
    """Записать подтверждённую смену тарифа, которая вступит в силу ``effective_from``.

    ``plan`` не трогается: до начала периода человек платит по прежнему
    тарифу, и экран не должен говорить иное. ``plan`` перепишет ``renewed``
    первого периода после смены (:func:`apply_subscription_event` снимет и
    ожидание). Из двух смен действует та, что вступает позже.
    """

    if plan not in MIRRORED_PLANS:
        raise BillingMirrorError(f"тариф вне словаря: {plan!r}")
    updated = (
        Tenant.all_objects.filter(pk=tenant_id)
        .filter(Q(pending_plan_from__isnull=True) | Q(pending_plan_from__lte=effective_from))
        .update(pending_plan=plan, pending_plan_from=effective_from)
    )
    return updated == 1


__all__ = [
    "MIRRORED_PLANS",
    "BillingMirrorError",
    "MirrorUpdate",
    "apply_pending_plan",
    "apply_subscription_event",
]
