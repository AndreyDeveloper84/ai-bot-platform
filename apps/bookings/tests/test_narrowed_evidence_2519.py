"""Доказательством состоявшегося визита стал только `completed` (DRF-2519).

## Почему это не регрессия

Раньше штамп ставился и при зеркале `confirmed` — с доводом «канон присылает
``booking.completed`` не всегда, а требовать ``completed`` значило бы не
закрывать почти ничего». Довод был верен про доставку и неверен про предмет:
**у зеркала нет столбца** ``updated_at``. Строка пишется один раз, при
заведении, и канонические переходы в неё не приезжают (замер стенда 25.09,
окно `ayla-b6`). То есть «зеркало не возражает» означало «копия, снятая при
заведении, не возражает» — заявление гораздо слабее.

Последствие уже наступило: два неоплаченных визита помечены состоявшимися,
канон ушёл в ``awaiting_payment`` за 4 ч 18 мин и за 21 час **до** штампа.

## Цена названа владельцу и принята им

Автозакрытие визитов почти перестаёт работать, мастер увидит разницу. Решение
владельца 25.09 — остановить производство ложных штампов.

**Тому, кто увидит упавшее число: откатывать нечего.** Настоящая починка —
читать канон по ``RemoteBookingProxy.appointment_id``
(``BookingClient.get_appointment_version`` уже возвращает канонический
``status``): идентификатор зеркала не стухает, стухает только статус.

## Что держат узлы ниже

Обе стороны, потому что сузить набор легко и легко же сломать заодно рабочую
ветку: `confirmed` больше не доказательство, `completed` — по-прежнему
доказательство. И причина отказа остаётся **машинной** (`mirror_confirmed`), а
не общим «нет свидетельства»: по ней считают, сколько визитов перестало
закрываться, и это и есть цена решения в штуках.

**Почему тут нет узла вида** ``assert MIRROR_ALLOWS == ("completed",)``.
Такой узел читает ту же константу, которую утверждает: он не может показать,
что решение доехало до функции, и краснеет на подмене вместе с настоящим
свидетелем, разбавляя сигнал. Признаком годится только то, что может
напечатать сам проверяемый код, — здесь это отказ с причиной
``mirror_confirmed``, полученный на настоящем пути. Что набор не должен расти
молча, сказано у самой константы, где это и прочтут.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from apps.booking.models import RemoteBookingProxy
from apps.bookings.completion_evidence import mirror_evidence
from apps.identity.models import BotUser
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant() -> Tenant:
    """Свои фикстуры, а не импорт соседних: имя фикстуры, совпав с именем
    параметра теста, даёт F811 у линтера — репозиторий уже наступал."""
    return Tenant.objects.create(slug="ev-2519", name="Salon 2519")


@pytest.fixture
def customer(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="ev-2519-1")


class _Booking:
    """Минимальная строка заявки: ключ пары и ничего лишнего."""

    def __init__(self, tenant_id, bot_user_id, visit_at) -> None:
        self.tenant_id = tenant_id
        self.bot_user_id = bot_user_id
        self.visit_at = visit_at


@pytest.mark.django_db
class TestTheDecisionShowsUpWhereItIsRead:
    """Проверка на настоящем пути, а не только на константе.

    Узел выше держит словарь; этот — то, что решение доехало до функции,
    которую зовёт детектор. Константу можно поправить и не изменить поведения,
    если между ней и решением окажется ещё одна ветка.
    """

    def _mirror(self, tenant, customer, visit_at, status: str) -> None:
        RemoteBookingProxy.all_tenants.create(
            tenant=tenant,
            bot_user=customer,
            appointment_id="11111111-1111-1111-1111-111111111111",
            start_at=visit_at,
            end_at=visit_at,
            status=status,
        )

    def test_a_confirmed_mirror_row_refuses_with_its_own_reason(self, tenant, customer) -> None:
        visit_at = timezone.now() - dt.timedelta(hours=2)
        self._mirror(tenant, customer, visit_at, RemoteBookingProxy.Status.CONFIRMED)

        allowed, reason = mirror_evidence(_Booking(tenant.id, customer.id, visit_at))

        assert allowed is False
        # Причина машинная и называет прочитанное состояние: по ней считают
        # цену решения в штуках.
        assert reason == "mirror_confirmed"

    def test_a_completed_mirror_row_still_allows(self, tenant, customer) -> None:
        visit_at = timezone.now() - dt.timedelta(hours=2)
        self._mirror(tenant, customer, visit_at, RemoteBookingProxy.Status.COMPLETED)

        allowed, reason = mirror_evidence(_Booking(tenant.id, customer.id, visit_at))

        assert allowed is True
        assert reason == "mirror_completed"
