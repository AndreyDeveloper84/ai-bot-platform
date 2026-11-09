"""DRF-2868 — правило предоплаты записи от шага плана (решение владельца 08.11, вариант А).

Запись от шага создаётся только без предоплаты. Для настоящей услуги решает
то же правило, что у обычной записи (``BOOKING_NO_PREPAYMENT_TENANTS``); салон
услуги читается из зеркала по идентификатору услуги каталога. Здесь база
настоящая: строки зеркала, салоны и настройка. Путь нажатий —
``test_plan_step_card_2885`` (b3) и ``test_plan_step_from_screen_2876`` (p9).

* m1 — салон услуги находится по ``ayla_service_id``; чужой салон не подменяет;
* m2 — услуги нет в зеркале, идентификатор не разобран, строк две — салона нет;
* r1 — синтетическая услуга — без предоплаты, зеркало не читается;
* r2 — салон из списка «без предоплаты» — без предоплаты; вне списка — с ней;
* r3 — салон неизвестен — «с предоплатой», даже когда список не пуст;
* r4 — испорченная настройка — «с предоплатой» (как у обычной записи);
* k1 — кнопка обычной записи несёт идентификаторы строк зеркала этого салона;
  мастер находится по id ПРОФИЛЯ в каталоге, а не по id человека;
* k2 — нет мастера в зеркале салона, услуга снята с продажи — кнопки нет.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from apps.catalog.models import CatalogMaster, CatalogService
from apps.orchestrator import plan_step_card as step
from apps.orchestrator.discovery import CALLBACK_DISCOVER_BOOK_PREFIX
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

OFFER = "0ffe2868-1111-4222-8333-444455556666"
MASTER = "aa002868-1111-4222-8333-444455556666"
#: Идентификатор, которого в зеркале нет. Постоянный: идентификаторы узлов
#: обязаны совпадать во всех процессах прогона (DRF-2633).
ABSENT = "ab5e2868-1111-4222-8333-444455556666"


def _ts() -> datetime:
    return datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def _salon(slug: str) -> Tenant:
    return Tenant.objects.create(slug=slug, name=f"Салон {slug}", city="Пенза")


def _service(tenant: Tenant, *, ayla_id: str = OFFER, active: bool = True) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        slug=f"svc-{uuid4().hex[:10]}",
        name="Классический массаж",
        is_active=active,
        ayla_service_id=ayla_id,
    )


def _master(tenant: Tenant, *, specialist_id: str = MASTER) -> CatalogMaster:
    """Строка зеркала мастера, как её пишет синхронизация.

    Два разных идентификатора каталога: ``catalog_specialist_id`` — профиль
    (его каталог называет ``specialist_ref``), ``ayla_user_id`` — человек.
    Они никогда не равны, и фикстура это держит: с равными узел k1 был бы
    зелёным при поиске не по тому полю.
    """
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name="Мастер массажа",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        raw={},
        catalog_specialist_id=specialist_id,
        ayla_user_id=uuid4(),
    )


def _option(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_offer_ref": OFFER,
        "specialist_ref": MASTER,
        "service_name": "Классический массаж",
        "master_name": "Мастер массажа",
        "synthetic": False,
    }
    base.update(over)
    return base


# ─── салон услуги по зеркалу ─────────────────────────────────────────────


def test_m1_the_salon_of_a_service_is_found_by_the_catalogs_service_id() -> None:
    salon = _salon("plan-2868-own")
    row = _service(salon)
    _service(_salon("plan-2868-other"), ayla_id=str(uuid4()))

    found = step._mirror_service(_option())

    assert found is not None
    assert (found.id, found.tenant_id, found.tenant.id) == (row.id, salon.id, salon.id)


@pytest.mark.parametrize("ref", [ABSENT, "не идентификатор", None, ""])
def test_m2_a_service_outside_the_mirror_or_an_unreadable_id_names_no_salon(ref: Any) -> None:
    _service(_salon("plan-2868-own"))

    assert step._mirror_service(_option(tenant_offer_ref=ref)) is None


def test_m2_two_salons_under_one_service_id_name_no_salon() -> None:
    _service(_salon("plan-2868-one"))
    _service(_salon("plan-2868-two"))

    assert step._mirror_service(_option()) is None


# ─── правило предоплаты ──────────────────────────────────────────────────


def test_r1_a_synthetic_service_needs_no_prepayment_and_reads_no_mirror(
    django_assert_num_queries,
) -> None:
    with django_assert_num_queries(0):
        assert step._prepayment_required(_option(synthetic=True)) is False


def test_r2_the_salons_setting_decides_for_a_real_service(settings) -> None:
    salon = _salon("plan-2868-own")
    _service(salon)

    settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset({str(salon.id).lower()})
    assert step._prepayment_required(_option()) is False

    settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset({str(uuid4())})
    assert step._prepayment_required(_option()) is True

    settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset()
    assert step._prepayment_required(_option()) is True


def test_r3_an_unknown_salon_is_not_read_as_no_prepayment(settings) -> None:
    salon = _salon("plan-2868-own")
    _service(salon)
    settings.BOOKING_NO_PREPAYMENT_TENANTS = frozenset({str(salon.id).lower()})

    assert step._prepayment_required(_option(tenant_offer_ref=str(uuid4()))) is True


def test_r4_a_malformed_setting_keeps_the_prepayment(settings) -> None:
    salon = _salon("plan-2868-own")
    _service(salon)
    settings.BOOKING_NO_PREPAYMENT_TENANTS = "*"

    assert step._prepayment_required(_option()) is True


# ─── кнопка обычной записи ───────────────────────────────────────────────


def test_k1_the_regular_booking_button_carries_the_mirror_rows_of_this_salon() -> None:
    salon = _salon("plan-2868-own")
    service = _service(salon)
    master = _master(salon)
    # Тот же мастер каталога в другом салоне — не его строка.
    _master(_salon("plan-2868-other"))

    button = step._regular_booking_button(_option())

    assert button == {
        "label": step.REGULAR_BOOKING_BUTTON,
        "callback": f"{CALLBACK_DISCOVER_BOOK_PREFIX}{salon.id}:{master.id}:{service.id}",
    }


def test_k2_without_a_master_row_in_the_salon_there_is_no_button() -> None:
    _service(_salon("plan-2868-own"))
    _master(_salon("plan-2868-other"))

    assert step._regular_booking_button(_option()) is None


def test_k2_the_persons_id_is_not_the_profiles_id() -> None:
    salon = _salon("plan-2868-own")
    _service(salon)
    master = _master(salon, specialist_id=ABSENT)
    # Каталог назвал мастера идентификатором ЧЕЛОВЕКА — это не specialist_ref.
    option = _option(specialist_ref=str(master.ayla_user_id))

    assert step._regular_booking_button(option) is None


@pytest.mark.parametrize("ref", ["не идентификатор", None])
def test_k2_an_unreadable_master_id_gives_no_button(ref: Any) -> None:
    salon = _salon("plan-2868-own")
    _service(salon)
    _master(salon)

    assert step._regular_booking_button(_option(specialist_ref=ref)) is None


def test_k2_a_service_off_sale_gives_no_button() -> None:
    salon = _salon("plan-2868-own")
    _service(salon, active=False)
    _master(salon)

    assert step._regular_booking_button(_option()) is None


def test_k3_the_redirect_says_the_owners_words_and_never_the_old_code() -> None:
    salon = _salon("plan-2868-own")
    service = _service(salon)
    master = _master(salon)

    result = step._regular_booking_reply(_option())

    assert result.meta["plan_outcome"] == "PLAN_STEP_BOOKING_VIA_REGULAR"
    assert step.REGULAR_BOOKING_TEXT in result.reply_text
    assert "BOOKING_NOT_AVAILABLE" not in result.reply_text
    assert f"{salon.id}:{master.id}:{service.id}" in str(result.action_data)
