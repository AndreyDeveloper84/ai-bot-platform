"""Чат: непродаваемое предложение называется, а не превращается в «0 ₽» или поломку (DRF-1989).

Где чат узнаёт от каталога, что предложение не продаётся, и что было до правки:

* котировка превью (ребро ``sellable=false``) — «• Цена: 0 ₽» и pending на
  запись, которую каталог откажет;
* расчёт цены с выбранным мастером — «<услуга>: 0 ₽.»;
* ✅ по превью, каталог ``422 SERVICE_NOT_ACTIVE`` + ``details.reason`` —
  безымянный ``YClientsAPIError`` → «Не удалось создать запись — переключаю
  на менеджера»;
* ворота здоровья по ребру — «консультация» вместо причины.

Теперь каждая точка — ``offer_not_sellable`` с текстом причины: без передачи
менеджеру и без перефраза моделью (слова — на подтверждении владельца).
Положительные стражи: ребро без ключа ``sellable`` котируется как раньше;
перенос существующей записи продаваемость не спрашивает (решение владельца R6).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from django.utils import timezone as dj_timezone

from apps.booking.models import PendingBookingAction
from apps.bookings.callbacks import BookingGateCallbackSkill
from apps.bookings.pending_actions import create_pending
from apps.bookings.tests.test_booking_callbacks import FakeYClients as GateFakeYClients
from apps.bookings.tests.test_booking_callbacks import _confirm_payload, _ctx
from apps.bookings.tests.test_booking_callbacks import _patch_yclients as _patch_gate_yclients
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.ayla.booking_client import AylaBookingRecord, BookingBadRequestError
from apps.integrations.ayla.tests.test_offer_refusal_1989 import CLIENT_PRICE
from apps.llm.protocol import ToolCall
from apps.skills.base import SkillContext
from apps.skills.booking import provider
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.skill import BookingSkill, _handle_pick_slot_callback
from apps.skills.booking.tests.test_ayla_write_lifecycle import _APPT, _SPEC, _SVC, FakeAyla
from apps.skills.booking.tests.test_skill import (  # noqa: F401 — _isolated_env: настройки LLM
    BOOKING_DATE,
    FakeYClients,
    _completion,
    _isolated_env,
    _patch_provider_complete,
    _patch_yclients,
    _staff,
)
from apps.skills.booking.tests.test_skill import _service as _yc_service
from apps.skills.booking.tools import (
    BookingToolResult,
    calc_price,
    confirm_booking,
    execute_confirm,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SLOT = "2026-11-01T16:00:00+03:00"
_TS = datetime(2026, 9, 16, tzinfo=timezone.utc)

UNSELLABLE_EDGE = {
    "price": "0.00",
    "duration_minutes": 60,
    "sellable": False,
    "unsellable_reason": "price_below_minimum",
}


@pytest.fixture
def flag_on(settings) -> None:
    settings.BOOKING_VIA_AYLA_REST = True


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(
        slug="offer-chat-1989", name="Offer Chat 1989", timezone="Europe/Moscow"
    )


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="bu-1989",
        chat_id="bu-1989",
        phone="79991234567",
        client_name="Anna",
        ayla_user_id=uuid.uuid4(),
    )


@pytest.fixture
def conversation(tenant: Tenant, bot_user: BotUser) -> Conversation:
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def ayla_service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=19890,
        external_updated_at=_TS,
        slug="massage-1989",
        name="Массаж",
        price_from=Decimal("1500.00"),
        duration_min=60,
        ayla_service_id=uuid.UUID(_SVC),
    )


class EdgeFake(FakeAyla):
    """FakeAyla + ребро по запросу + счёт обращений к ребру + отказ создания."""

    def __init__(self) -> None:
        super().__init__()
        self.edges: list[dict[str, Any]] = []
        self.edge_calls = 0
        self.create_exc: Exception | None = None

    def get_specialist_service_edges(
        self, *, specialist_id: str, service_id: str
    ) -> list[dict[str, Any]]:
        self.edge_calls += 1
        return list(self.edges)

    def create_appointment(self, **kwargs: Any) -> AylaBookingRecord:
        if self.create_exc is not None:
            raise self.create_exc
        return AylaBookingRecord(appointment_id=_APPT, raw={})


def _adapter(fake: FakeAyla) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(
        client=fake, external_user_id="bot:max:bu-1989", client_id="client-uuid"
    )


def _named_error() -> type[Exception]:
    named = getattr(provider, "YClientsOfferNotSellableError", None)
    assert named is not None, "нет provider.YClientsOfferNotSellableError"
    return named


def _service_not_active() -> BookingBadRequestError:
    return BookingBadRequestError(
        "http_422_SERVICE_NOT_ACTIVE",
        status_code=422,
        code="SERVICE_NOT_ACTIVE",
        details={"reason": "price_below_minimum"},
    )


READS = {
    "quote": lambda a: a.get_specialist_service_quote(staff_id=_SPEC, service_id=_SVC),
    "price": lambda a: a.get_specialist_service_price(staff_id=_SPEC, service_id=_SVC),
}


# ── провайдер ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("read", list(READS))
def test_edge_reads_name_an_unsellable_offer(read: str) -> None:
    fake = EdgeFake()
    fake.edges = [dict(UNSELLABLE_EDGE)]
    named = _named_error()

    with pytest.raises(named) as exc:
        READS[read](_adapter(fake))

    assert getattr(exc.value, "reason", None) == "price_below_minimum"


def test_edge_without_the_sellable_key_quotes_as_before() -> None:
    """Положительная стража: каталог до #477 молчит о продаже — котировка прежняя."""
    fake = EdgeFake()
    fake.edges = [{"price": "1500.00", "duration_minutes": 60}]

    quote = _adapter(fake).get_specialist_service_quote(staff_id=_SPEC, service_id=_SVC)

    assert quote == (Decimal("1500.00"), 60)


def test_create_refused_as_price_below_minimum_is_translated_by_name() -> None:
    fake = EdgeFake()
    fake.create_exc = _service_not_active()
    named = _named_error()

    with pytest.raises(named) as exc:
        _adapter(fake).create_record(
            staff_id=_SPEC,
            services=[_SVC],
            datetime=SLOT,
            client_phone="79991234567",
            client_name="Anna",
        )

    assert getattr(exc.value, "reason", None) == "price_below_minimum"


# ── инструменты ─────────────────────────────────────────────────────────────


def test_preview_of_an_unsellable_offer_is_the_refusal_not_a_pending(
    flag_on, tenant: Tenant, bot_user: BotUser
) -> None:
    fake = EdgeFake()
    fake.edges = [dict(UNSELLABLE_EDGE)]

    with tenant_scope(tenant):
        result = confirm_booking(
            client=_adapter(fake),
            arguments={"master_id": _SPEC, "service_id": _SVC, "slot_datetime": SLOT},
            tenant=tenant,
            bot_user=bot_user,
            allowed_master_ids={_SPEC},
            allowed_service_ids={_SVC},
            master_lookup={_SPEC: "Ольга"},
            service_lookup={_SVC: "Массаж"},
        )

    assert (result.error, result.text) == ("offer_not_sellable", CLIENT_PRICE)
    assert result.pending is None
    assert PendingBookingAction.all_tenants.count() == 0


def test_confirm_tap_on_price_below_minimum_is_the_refusal(
    flag_on, tenant: Tenant, bot_user: BotUser
) -> None:
    fake = EdgeFake()
    fake.create_exc = _service_not_active()

    with tenant_scope(tenant):
        result = execute_confirm(
            client=_adapter(fake),
            payload={
                "master_id": _SPEC,
                "service_id": _SVC,
                "slot_datetime": SLOT,
                "client_phone": "79991234567",
                "client_name": "Anna",
                "master_name": "Ольга",
                "service_name": "Массаж",
                "quoted_price": "1500.00",
            },
            tenant=tenant,
            bot_user=bot_user,
        )

    assert (result.error, result.text) == ("offer_not_sellable", CLIENT_PRICE)
    assert result.confirmation is not None and not result.confirmation.ok


def test_price_with_a_master_on_an_unsellable_offer_is_the_refusal(
    flag_on, tenant: Tenant, ayla_service: CatalogService
) -> None:
    fake = EdgeFake()
    fake.edges = [dict(UNSELLABLE_EDGE)]

    result = calc_price(
        tenant=tenant,
        client=_adapter(fake),
        arguments={"service_id": _SVC, "master_id": _SPEC},
        allowed_service_ids={_SVC},
        service_lookup={_SVC: "Массаж"},
    )

    assert (result.error, result.text) == ("offer_not_sellable", CLIENT_PRICE)


def test_price_below_one_rouble_is_not_quoted(
    flag_on, tenant: Tenant, ayla_service: CatalogService
) -> None:
    CatalogService.all_tenants.filter(pk=ayla_service.pk).update(price_from=Decimal("0.00"))

    result = calc_price(
        tenant=tenant,
        arguments={"service_id": _SVC},
        allowed_service_ids={_SVC},
        service_lookup={_SVC: "Массаж"},
    )

    assert "точную цену озвучит администратор" in result.text
    assert "0 ₽" not in result.text


# ── навык и колбэк ──────────────────────────────────────────────────────────


def test_confirm_tap_reply_is_the_refusal_not_the_manager_handoff(
    tenant: Tenant, bot_user: BotUser, conversation: Conversation
) -> None:
    future_iso = (dj_timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CONFIRM,
        payload=_confirm_payload(future_iso),
    )
    ctx = _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)

    with (
        _patch_gate_yclients(GateFakeYClients()),
        patch(
            "apps.bookings.callbacks.execute_confirm",
            return_value=BookingToolResult(error="offer_not_sellable", text=CLIENT_PRICE),
        ),
    ):
        result = BookingGateCallbackSkill().handle(ctx)

    assert (result.should_handoff, result.reply_text) == (False, CLIENT_PRICE)


def test_confirm_tap_on_a_health_check_handoff_keeps_todays_reply(
    tenant: Tenant, bot_user: BotUser, conversation: Conversation
) -> None:
    """Закрепление СЕГОДНЯШНЕГО поведения, не желаемого — исправляется в листе P1
    главного окна (ключ: TODO-1989-P1).

    На ✅ ``_dispatch_confirm`` превращает любой ``result.error`` в «Не удалось
    создать запись — переключаю на менеджера», и именованный медицинский отказ
    (DRF-1614) теряет свой текст. DRF-1989 чинит на этом пути только
    ``offer_not_sellable`` и не трогает health-исход — этот тест следит, чтобы
    правка его и не ухудшила. Лист P1 сделает тест красным и снимет пометку.
    """
    future_iso = (dj_timezone.now() + timedelta(days=2)).replace(microsecond=0).isoformat()
    token = create_pending(
        tenant=tenant,
        bot_user=bot_user,
        kind=PendingBookingAction.Kind.CONFIRM,
        payload=_confirm_payload(future_iso),
    )
    ctx = _ctx(f"cb:book:confirm:{token}", bot_user=bot_user, conversation=conversation)

    with (
        _patch_gate_yclients(GateFakeYClients()),
        patch(
            "apps.bookings.callbacks.execute_confirm",
            return_value=BookingToolResult(
                error="health_check_handoff", text="Текст медицинского отказа"
            ),
        ),
    ):
        result = BookingGateCallbackSkill().handle(ctx)

    assert (result.should_handoff, result.handoff_reason, result.reply_text) == (
        True,
        "booking_yclients_failure",
        "Не удалось создать запись — переключаю на менеджера.",
    )


@pytest.mark.parametrize("tool", ["confirm_booking", "calc_price"])
def test_skill_replies_with_the_refusal_without_rephrasing_or_handoff(
    tool: str, tenant: Tenant, bot_user: BotUser, conversation: Conversation
) -> None:
    client = FakeYClients()
    client.services_rows = [_yc_service(22)]
    client.staff_rows = [_staff(11)]
    arguments: dict[str, Any] = (
        {"master_id": 11, "service_id": 22, "slot_datetime": f"{BOOKING_DATE}T14:00:00"}
        if tool == "confirm_booking"
        else {"service_id": 22}
    )
    context = SkillContext(
        conversation=conversation, bot_user=bot_user, message_text="запиши", trace_id="t-1989"
    )
    completions = [
        _completion(tool_calls=[ToolCall(id="c1", name=tool, arguments=arguments)]),
        _completion(text="Перефраз модели"),
    ]

    with (
        _patch_yclients(client),
        _patch_provider_complete(completions) as complete,
        patch(
            f"apps.skills.booking.skill.{tool}",
            return_value=BookingToolResult(error="offer_not_sellable", text=CLIENT_PRICE),
        ),
        tenant_scope(tenant),
    ):
        result = BookingSkill().handle(context)

    assert (result.should_handoff, result.reply_text) == (False, CLIENT_PRICE)
    assert complete.call_count == 1


def test_pick_slot_on_an_unsellable_edge_is_the_refusal_not_a_consultation(
    flag_on, tenant: Tenant, bot_user: BotUser, conversation: Conversation
) -> None:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=19891,
        external_updated_at=_TS,
        name="Анна",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=19892,
        external_updated_at=_TS,
        slug="manicure-1989",
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.UUID(_SVC),
    )
    # Вердикт здоровья «нужен скрининг» — до правки ответом была консультация.
    MasterService.all_tenants.create(
        tenant=tenant,
        master=master,
        service=service,
        resolved_requires_health_check=True,
        sellable=False,
        unsellable_reason="price_below_minimum",
    )
    roster = FakeYClients()
    roster.staff_rows = [_staff(str(master.pk))]
    ctx = SkillContext(
        conversation=conversation, bot_user=bot_user, message_text="", trace_id="t-1989"
    )

    with tenant_scope(tenant):
        result = _handle_pick_slot_callback(
            text=f"cb:book:pick_slot:{master.pk}:{_SVC}:{BOOKING_DATE}T14:00:00",
            context=ctx,
            tenant=tenant,
            tenant_id=str(tenant.id),
            yclients=roster,
            allowed_service_ids={_SVC},
            service_lookup={_SVC: "Маникюр"},
        )

    assert (result.should_handoff, result.reply_text) == (False, CLIENT_PRICE)
    assert roster.times_calls == []
    assert PendingBookingAction.all_tenants.count() == 0


# ── R6 ──────────────────────────────────────────────────────────────────────


def test_chat_reschedule_does_not_ask_whether_the_offer_sells(flag_on) -> None:
    """Решение владельца R6: перенос существующей записи не перепроверяет цену.

    Закрепление текущего поведения, не изменение: ребро непродаваемое, а
    перенос идёт без обращения к нему.
    """
    fake = EdgeFake()
    fake.edges = [dict(UNSELLABLE_EDGE)]

    record = _adapter(fake).reschedule_record(record_id=_APPT, datetime=SLOT)

    assert record is not None
    assert fake.edge_calls == 0
    assert [call["appointment_id"] for call in fake.reschedule_calls] == [_APPT]
