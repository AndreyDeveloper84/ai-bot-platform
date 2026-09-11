"""Approving a day-off writes it into Ayla, not just locally (DRF-1062).

Before this, approval materialised ``ScheduleException`` rows in the bot's
own ``apps.scheduling``. Once the customer picker reads slots from Ayla
that store no longer decides anything a client can see, so the
administrator would approve a day off, be told it succeeded, and the day
would stay on sale. Two stores, one of them decorative — the exact
"промежуточное состояние" the brief calls the worst outcome.

Flag OFF is left alone on purpose: that deployment computes slots locally
and books locally, so its schedule genuinely is the local one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.admin_api.services.availability import (
    AvailabilityDecisionError,
    approve_availability_request,
)
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.booking_client import (
    BookingUnavailableError,
    ScheduleBlockConflictError,
)
from apps.scheduling.models import ScheduleChangeRequest, ScheduleException
from apps.tenancy.models import Tenant

CLIENT_PATH = "apps.integrations.ayla.booking_client.get_ayla_booking_client"
START = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _ayla_path(settings) -> None:
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="av-1062",
        name="Салон заявок",
        timezone="Europe/Moscow",
    )


@pytest.fixture
def master(tenant: Tenant) -> CatalogMaster:
    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=7,
        external_updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        name="Ольга",
        is_active=True,
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def pending(tenant: Tenant, master: CatalogMaster) -> ScheduleChangeRequest:
    return ScheduleChangeRequest.all_tenants.create(
        tenant=tenant,
        master=master,
        requested_start=START,
        requested_end=END,
        reason_class="sick_leave",
        reason_text="болезнь",
        status=ScheduleChangeRequest.Status.PENDING,
    )


def _fake_client(*, raises=None):
    calls: list[dict] = []

    def create_specialist_time_off(**kwargs):
        calls.append(kwargs)
        if raises is not None:
            raise raises
        return {"id": str(uuid.uuid4())}

    return SimpleNamespace(create_specialist_time_off=create_specialist_time_off), calls


def _named_actor(tenant):
    """Человек, от чьего имени идёт одобрение.

    Появился 11.09.2026 вместе с §143: одобрение БЕЗ названного актора
    больше не законный сценарий, а отказ. Живые вызывающие — мини-апп и
    канал MAX — человека знают всегда; фикстура приводит тесты в
    соответствие с ними, а не подгоняет их под новый отказ.
    """
    from apps.identity.models import BotUser

    return BotUser.objects.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"admin-{uuid.uuid4().hex[:8]}",
        display_name="Карина",
    )


def _approve(tenant, request_id):
    actor = _named_actor(tenant)
    return approve_availability_request(
        request_id=request_id,
        tenant_id=tenant.id,
        actor=None,
        actor_bot_user_id=actor.id,
        actor_bot_user=actor,
        actor_role="admin",
    )


class TestApprovalReachesAyla:
    def test_approval_blocks_the_time_in_ayla(self, tenant, master, pending):
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert len(calls) == 1
        assert calls[0]["specialist_id"] == str(master.id)
        assert calls[0]["tenant_id"] == str(tenant.id)

    def test_sends_the_requested_interval_not_whole_days(
        self,
        tenant,
        master,
        pending,
    ):
        """The local model is date-keyed and rounds a part-day request up to
        full days. Ayla holds an interval, so it gets what was actually
        asked for — blocking more would quietly cost the salon bookings."""
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert calls[0]["start_at"] == START.isoformat()
        assert calls[0]["end_at"] == END.isoformat()

    def test_addresses_the_specialist_id_not_the_user_id(
        self,
        tenant,
        master,
        pending,
    ):
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert calls[0]["specialist_id"] != str(master.ayla_user_id)


class TestRefusalsLeaveNothingHalfDone:
    def test_active_bookings_refuse_with_409_and_request_stays_pending(
        self,
        tenant,
        master,
        pending,
    ):
        """The administrator must learn the time is booked, not watch
        "approve" silently do nothing."""
        fake, _ = _fake_client(raises=ScheduleBlockConflictError("has_active"))

        with patch(CLIENT_PATH, return_value=fake):
            with pytest.raises(AvailabilityDecisionError) as err:
                _approve(tenant, pending.id)

        assert err.value.status == 409
        assert err.value.slug == "has_active_appointments"

        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.PENDING
        assert not ScheduleException.all_tenants.filter(master=master).exists()

    def test_outage_refuses_rather_than_approving_into_the_void(
        self,
        tenant,
        master,
        pending,
    ):
        fake, _ = _fake_client(raises=BookingUnavailableError("circuit_open"))

        with patch(CLIENT_PATH, return_value=fake):
            with pytest.raises(AvailabilityDecisionError) as err:
                _approve(tenant, pending.id)

        assert err.value.status == 503

        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.PENDING
        assert not ScheduleException.all_tenants.filter(master=master).exists()


class TestFlagOffUnchanged:
    def test_local_only_deployment_never_calls_ayla(
        self,
        settings,
        tenant,
        master,
        pending,
    ):
        settings.BOOKING_VIA_AYLA_REST = False
        fake, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=fake):
            _approve(tenant, pending.id)

        assert calls == []
        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.APPROVED


class TestTheBlockIsAttributedToAHuman:
    """§117, третья проверка: кто закрыл график, видно НА ТОЙ стороне.

    Замер 10.09.2026: из четырёх записывающих вызовов ``booking_client``
    три несут ``X-External-User-ID`` — создание записи, отмена, перенос, —
    а закрытие графика было единственным без него. Ayla видела «сервис»
    там, где закрыли чужой рабочий день.

    Следствие сильнее неудобства в логе: раз человек не передаётся, никакая
    проверка ЕГО прав наверху невозможна в принципе. Пока единственная дверь
    — одобрение заявки самого мастера, объект выбирает заявка. Открой салону
    закрывать график по своему выбору (DRF-1240), и между админом и любым
    мастером тенанта остался бы только наш декоратор.
    """

    def test_the_wire_carries_the_person_who_approved(
        self, tenant: Tenant, master: CatalogMaster, pending: ScheduleChangeRequest
    ) -> None:
        from apps.identity.models import BotUser

        actor = BotUser.objects.create(
            tenant=tenant,
            channel="max",
            channel_user_id="7788",
            display_name="Карина",
        )
        client, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=client):
            approve_availability_request(
                request_id=pending.id,
                tenant_id=tenant.id,
                actor=None,
                actor_bot_user_id=actor.id,
                actor_bot_user=actor,
                actor_role="admin",
            )

        assert len(calls) == 1
        # Именно тот формат, что понимает Ayla: bot:{channel}:{channel_user_id}.
        assert calls[0]["external_user_id"] == "bot:max:7788"

    def test_without_a_named_person_the_operation_is_refused(
        self, tenant: Tenant, master: CatalogMaster, pending: ScheduleChangeRequest
    ) -> None:
        """Перевёрнут 11.09.2026 решением владельца §143.

        Прежняя редакция этого теста утверждала, что запись УХОДИТ, просто
        без имени: ``external_user_id is None``, предупреждение в лог, а
        вопрос «отказывать ли» вынесен владельцу. Владелец ответил:

            «Для чувствительной операции действует fail-closed: если автора
            нельзя надёжно определить, операция НЕ ВЫПОЛНЯЕТСЯ. Запись
            "автор неизвестен" недопустима, потому что создаёт ложную
            видимость полноценного аудита.»

        Тест перевёрнут на месте, а не удалён: он и раньше стерёг границу,
        только граница передвинулась. Из диффа видно, ЧТО именно изменилось
        в правиле, а молчаливое удаление показало бы лишь, что тест исчез.

        Проверяется не только отказ, но и то, что до провода ничего не
        доехало: отказ, после которого запись всё же ушла, — худший из
        исходов, потому что и журнал пуст, и время закрыто.
        """
        client, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=client):
            with pytest.raises(AvailabilityDecisionError) as excinfo:
                approve_availability_request(
                    request_id=pending.id,
                    tenant_id=tenant.id,
                    actor=None,
                    actor_role="admin",
                )

        assert excinfo.value.slug == "actor_required"
        assert calls == [], "операция отказана, но запись всё равно ушла на провод"

        # Ничего не изменено: отказ стоит ДО транзакции.
        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.PENDING
        assert not ScheduleException.all_tenants.filter(master=master).exists()

    def test_the_helper_refuses_on_its_own_not_only_behind_the_entry_check(
        self, tenant: Tenant, master: CatalogMaster
    ) -> None:
        """Вторая половина fail-closed, проверенная НАПРЯМУЮ.

        Проверок две: на входе ``approve_availability_request`` и перед
        записью в Ayla. Со входа вторая недостижима — подмена, снявшая её,
        промолчала, и это не бесполезный сторож, а сторож без пути к нему.

        Путь даётся здесь: помощник зовётся напрямую, как его позовёт
        будущий второй вызывающий (салонная инициатива §142). Без этого
        теста глубокая проверка была бы непроверяемым кодом, то есть
        совпадением, а не правилом.
        """
        from apps.admin_api.services.availability import _block_time_in_ayla

        client, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=client):
            with pytest.raises(AvailabilityDecisionError) as excinfo:
                _block_time_in_ayla(
                    master=master,
                    tenant_id=tenant.id,
                    start_at=START,
                    end_at=END,
                    reason="болезнь",
                    actor_bot_user=None,
                )

        assert excinfo.value.slug == "actor_required"
        assert calls == [], "помощник отказал, но запись всё равно ушла"

    def test_the_refusal_holds_with_the_flag_off_too(
        self, settings, tenant: Tenant, master: CatalogMaster, pending: ScheduleChangeRequest
    ) -> None:
        # Одобрение закрывает время мастера при ЛЮБОМ состоянии флага: при
        # включённом в Ayla, при выключенном локально, и в обоих случаях
        # пишет строку аудита. Проверка только в ayla-ветке оставила бы
        # вторую дверь открытой.
        settings.BOOKING_VIA_AYLA_REST = False
        client, calls = _fake_client()

        with patch(CLIENT_PATH, return_value=client):
            with pytest.raises(AvailabilityDecisionError) as excinfo:
                approve_availability_request(
                    request_id=pending.id,
                    tenant_id=tenant.id,
                    actor=None,
                    actor_role="admin",
                )

        assert excinfo.value.slug == "actor_required"
        pending.refresh_from_db()
        assert pending.status == ScheduleChangeRequest.Status.PENDING


class TestTheOnlyDoorIsTheMastersOwnRequest:
    """Отрицательный эталон §114: сдерживание держится ФОРМОЙ ВХОДА.

    §117 велит проверить object authorization на этом пути записи. Проверка
    дала такой ответ: сегодня он безопасен, но **не правами**. Единственная
    дверь — одобрение заявки, которую подал сам мастер, и объект выбирает
    строка заявки, а не вызывающий. Наш декоратор гейтит роль, заявка гейтит
    объект.

    Прав на объект вверху по течению нет и быть не может: до правки
    attribution человека туда вообще не передавали, а теперь передают — но
    что с ним делает Ayla, нам не видно, и полагаться на это нельзя.

    Значит второй вызывающий, выбирающий мастера сам — а это ровно то, чего
    просит DRF-1240 для салонной инициативы, — снимает сдерживание молча.
    Между админом и любым мастером тенанта остался бы только декоратор.

    **Этот тест обязан покраснеть в день, когда появится вторая дверь.**
    Краснота здесь не значит «сломано»: она значит «вернитесь к проверке
    object authorization прежде, чем этот вызов появится», и ответ на неё —
    решение владельца, а не правка теста.
    """

    def test_exactly_one_caller_reaches_the_block(self) -> None:
        from pathlib import Path

        from apps.admin_api.services import availability as mod

        src = Path(mod.__file__).read_text(encoding="utf-8")
        callers = [
            line
            for line in src.splitlines()
            if "_block_time_in_ayla(" in line and not line.lstrip().startswith("def ")
        ]

        # Положительный контроль: известный вызов обязан найтись ЭТИМ же
        # поиском. Ноль здесь читался бы как «второй двери нет», а значил бы
        # «ищу не то» — ровно тот ложный ноль, о котором §119.
        assert len(callers) >= 1, "поиск не нашёл даже известный вызов — ищу не там"
        assert len(callers) == 1, (
            "появился второй вызывающий _block_time_in_ayla. Если он выбирает "
            "мастера сам, а не берёт его из заявки, сдерживание object "
            "authorization снято: вернитесь к трём проверкам §117 прежде, чем "
            f"оставлять это. Найдено: {callers}"
        )
