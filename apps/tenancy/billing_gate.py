"""Гейты подписки платформы на стороне бота (DRF-2984, PR-B2).

Читает только зеркало подписки на тенанте (:mod:`apps.tenancy.billing_mirror`,
PR-B1); канон — ``billing/`` каталога. Всё за ``settings.BILLING_ENABLED``:
выключено — каждый гейт молчит, поведение как до биллинга.

Два вопроса, решения владельца 20.10 (В-2, В-3, В-4):

* **Можно ли тенанту получить ещё одного активного мастера** —
  :func:`master_add_verdict`. Жёсткого лимита у салона нет (сверх трёх —
  доплата, её считает каталог). Отказ только у замороженной или отменённой
  подписки. Соло со вторым мастером — не отказ, а предложение перейти на
  «Салон» с подтверждением цены: ``NEEDS_PLAN_CHANGE``. После подтверждения
  (зеркало ``pending_plan == "salon"``) второй мастер разрешён сразу —
  каталог меняет вид тенанта в момент подтверждения, тариф — со следующего
  периода.
* **Открыт ли Mini App персонала** — :func:`frozen_refusal`. Заморозка — статус
  подписки, не ``Tenant.is_active=False`` (В-4): закрыто всё, кроме
  биллинга, профиля и входа, — иначе замороженный не смог бы заплатить.

Каждая дверь, через которую у тенанта появляется активный мастер, зовёт
:func:`assert_can_add_master`; перепись дверей держит
``apps/tenancy/tests/test_billing_gate_doors_2984.py``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Final

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from apps.tenancy.models import SubscriptionStatus, Tenant

#: Статусы, при которых тенант закрыт: новых мастеров нет, Mini App персонала — только оплата.
CLOSED_STATUSES: Final = frozenset({SubscriptionStatus.FROZEN, SubscriptionStatus.CANCELED})

#: Маршруты Mini App персонала, открытые при заморозке: (namespace, url_name).
#: Биллинг — чтобы заплатить; вход и онбординг — чтобы дойти до биллинга;
#: профиль — то, что остаётся человеку и при закрытом салоне.
OPEN_WHEN_FROZEN: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("master_api", "billing_status"),
        ("master_api", "billing_card_setup"),
        ("master_api", "billing_pay_debt"),
        ("master_api", "payout_preview"),
        ("master_api", "me"),
        ("master_api", "onboarding_claim"),
        ("master_api", "onboarding_accept"),
        ("master_api", "onboarding_reject"),
        ("master_api", "onboarding_readiness"),
        ("master_api", "onboarding_profile"),
        ("master_api", "profile"),
        ("master_api", "profile_card"),
        ("master_api", "profile_portfolio"),
        ("master_api", "profile_portfolio_item"),
    }
)


def billing_enabled() -> bool:
    return bool(getattr(settings, "BILLING_ENABLED", False))


class MasterAddVerdict(StrEnum):
    ALLOWED = "allowed"
    REFUSED_FROZEN = "subscription_frozen"
    REFUSED_CANCELED = "subscription_canceled"
    NEEDS_PLAN_CHANGE = "plan_change_required"


#: Что видит человек у двери. «Вы» — канон персонала.
_DETAIL: Final[dict[MasterAddVerdict, str]] = {
    MasterAddVerdict.REFUSED_FROZEN: "Подписка салона заморожена — добавить мастера можно "
    "после оплаты в разделе «Биллинг».",
    MasterAddVerdict.REFUSED_CANCELED: "Подписка салона отменена — подключите её снова в "
    "разделе «Биллинг», чтобы добавлять мастеров.",
    MasterAddVerdict.NEEDS_PLAN_CHANGE: "Второй мастер — это тариф «Салон» (990 ₽ в месяц). "
    "Подтвердите переход в разделе «Биллинг».",
}


class MasterAddRefused(Exception):
    """Тенанту сейчас нельзя получить ещё одного активного мастера."""

    def __init__(self, verdict: MasterAddVerdict) -> None:
        super().__init__(verdict.value)
        self.verdict = verdict

    @property
    def slug(self) -> str:
        return self.verdict.value

    @property
    def detail(self) -> str:
        return _DETAIL[self.verdict]


def _active_masters_besides(tenant: Tenant, master_id: Any) -> int:
    from apps.catalog.master_state import ADMITTED
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.context import tenant_scope

    # Менеджер по умолчанию держит границу тенанта сам (MKT1: all_tenants —
    # только для marketplace).
    with tenant_scope(tenant):
        others = CatalogMaster.objects.filter(ADMITTED)
        if master_id is not None:
            others = others.exclude(pk=master_id)
        return others.count()


def master_add_verdict(tenant: Tenant, *, master_id: Any = None) -> MasterAddVerdict:
    """Может ли ``tenant`` получить ещё одного активного мастера.

    ``master_id`` — мастер, которого делают активным (если строка уже есть):
    он сам себе не «второй», возврат единственного мастера соло разрешён.
    """

    if not billing_enabled():
        return MasterAddVerdict.ALLOWED
    status = tenant.subscription_status
    if status == SubscriptionStatus.FROZEN:
        return MasterAddVerdict.REFUSED_FROZEN
    if status == SubscriptionStatus.CANCELED:
        return MasterAddVerdict.REFUSED_CANCELED
    if (
        tenant.plan == "solo"
        and tenant.pending_plan != "salon"
        and _active_masters_besides(tenant, master_id) >= 1
    ):
        return MasterAddVerdict.NEEDS_PLAN_CHANGE
    return MasterAddVerdict.ALLOWED


def assert_can_add_master(tenant: Tenant, *, master_id: Any = None) -> None:
    """Отказ :class:`MasterAddRefused`, если гейт не пускает. Зовёт каждая дверь."""

    verdict = master_add_verdict(tenant, master_id=master_id)
    if verdict is not MasterAddVerdict.ALLOWED:
        raise MasterAddRefused(verdict)


def master_add_refusal_response(exc: MasterAddRefused) -> JsonResponse:
    """409 для HTTP-дверей: ``{"error", "detail"}`` — форма ответов admin/master API."""

    return JsonResponse({"error": exc.slug, "detail": exc.detail}, status=409)


def is_closed(tenant: Tenant) -> bool:
    return billing_enabled() and tenant.subscription_status in CLOSED_STATUSES


def frozen_refusal(request: HttpRequest, tenant: Tenant) -> JsonResponse | None:
    """403 ``subscription_frozen`` для закрытого тенанта вне :data:`OPEN_WHEN_FROZEN`."""

    if not is_closed(tenant):
        return None
    match = getattr(request, "resolver_match", None)
    if match is not None and (match.namespace, match.url_name) in OPEN_WHEN_FROZEN:
        return None
    return JsonResponse(
        {
            "error": "subscription_frozen",
            "detail": "Подписка салона приостановлена. Оплатить можно в разделе «Биллинг».",
            "details": {"subscription_status": tenant.subscription_status},
        },
        status=403,
    )


__all__ = [
    "CLOSED_STATUSES",
    "OPEN_WHEN_FROZEN",
    "MasterAddRefused",
    "MasterAddVerdict",
    "assert_can_add_master",
    "billing_enabled",
    "frozen_refusal",
    "is_closed",
    "master_add_refusal_response",
    "master_add_verdict",
]
