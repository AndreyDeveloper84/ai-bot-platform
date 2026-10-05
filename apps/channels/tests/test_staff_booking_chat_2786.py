"""DRF-2786 — «✍️ Записать клиента»: a new booking in the salon bot's chat.

Through the admin Mini App's own services (``admin_api.services.booking``);
Ayla's two clients are recording stubs at the module attribute those
services read lazily.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.identity.services.staff_invites import issue_staff_invite, redeem_staff_invite
from apps.integrations.ayla.salon_client import SalonSlotTaken, SalonUnavailable
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, Tenant

pytestmark = pytest.mark.django_db

MSK = ZoneInfo("Europe/Moscow")
CHANNEL_USER_ID = "702786"

ENTRY = BotEntry(
    slug="salon",
    webhook_secret="wh",  # pragma: allowlist secret
    api_token="tok-salon",  # pragma: allowlist secret
    tenant_slug="booking-chat",
    stream="max_salon",
    web_app="id583_salon_bot",
)


@pytest.fixture(autouse=True)
def _env(settings, monkeypatch):
    monkeypatch.setattr("apps.channels.max.salon_entry.unlinked_reason", lambda *a, **kw: "")
    settings.MAX_BOT_REGISTRY = (ENTRY,)
    settings.MAX_BOT_TOKEN = "tok-client"  # pragma: allowlist secret
    cache.clear()


@pytest.fixture
def tenant() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug="booking-chat", defaults={"name": "Запись в чате", "timezone": "Europe/Moscow"}
    )
    return obj


@pytest.fixture
def sent():
    with patch("apps.channels.max.outbound.send_message") as mock:
        yield mock


class _Booking:
    """``get_available_times`` — two starts, the second without a timestamp."""

    def __init__(self, day_iso: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self.day = day_iso

    def get_available_times(self, **kw):
        self.calls.append(kw)
        return [
            SimpleNamespace(time="10:00", datetime=f"{kw['date']}T10:00:00+03:00", duration_s=3600),
            SimpleNamespace(time="11:00", datetime=None, duration_s=3600),
            SimpleNamespace(time="12:00", datetime=f"{kw['date']}T12:00:00+03:00", duration_s=3600),
        ]


class _Salon:
    def __init__(self, *, create_exc: list[Exception] | None = None) -> None:
        self.created: list[dict[str, Any]] = []
        self.searched: list[str] = []
        self.create_exc = list(create_exc or [])

    def search_customers(self, *, actor_external_id, tenant_slug, query):
        self.searched.append(query)
        if "Мар" in query:
            return [{"id": "c-1", "name": "Мария Иванова"}, {"id": "c-2", "name": "bot:max:99"}]
        return []

    def create_appointment(self, **kw):
        self.created.append(kw)
        if self.create_exc:
            raise self.create_exc.pop(0)
        return {"id": "appt-1"}


@pytest.fixture
def ayla(monkeypatch):
    def _install(**kw):
        b = _Booking("")
        s = _Salon(**kw)
        monkeypatch.setattr(
            "apps.integrations.ayla.booking_client.get_ayla_booking_client", lambda: b
        )
        monkeypatch.setattr("apps.integrations.ayla.salon_client.get_salon_client", lambda: s)
        return b, s

    return _install


def _staff(tenant, role=StaffInvite.Role.ADMIN) -> BotUser:
    person = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id=CHANNEL_USER_ID
    )
    _, code = issue_staff_invite(tenant=tenant, role=role)
    redeem_staff_invite(code=code, bot_user=person, tenant=tenant)
    return person


def _catalog(tenant) -> tuple[CatalogMaster, CatalogService]:
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Анна",
        external_id=None,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
    )
    CatalogMaster.all_tenants.filter(pk=master.pk).update(catalog_specialist_id=master.pk)
    master.refresh_from_db()
    service = CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=None,
        external_updated_at=timezone.now(),
        name="Маникюр",
        duration_min=60,
        is_active=True,
        ayla_service_id=uuid.uuid4(),
    )
    MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master, service


def _event(payload: str, *, callback: bool) -> dict[str, Any]:
    if callback:
        return {
            "update_type": "message_callback",
            "timestamp": 1_700_000_000_000,
            "callback": {
                "callback_id": f"cb-{uuid.uuid4().hex[:8]}",
                "payload": payload,
                "timestamp": 1_700_000_000_000,
                "user": {"user_id": int(CHANNEL_USER_ID), "name": "Админ"},
            },
            "message": {
                "body": {"mid": "m1", "seq": 1, "text": ""},
                "sender": {"user_id": 999, "name": "bot", "is_bot": True},
                "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
            },
        }
    return {
        "update_type": "message_created",
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": int(CHANNEL_USER_ID), "name": "Админ", "is_bot": False},
            "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
            "body": {
                "mid": f"mid-{uuid.uuid4().hex[:8]}",
                "seq": 1,
                "text": payload,
                "attachments": [],
            },
        },
    }


def _tap(tenant, payload: str) -> None:
    from apps.channels.max.salon_handler import handle_salon_max_event

    with tenant_scope(tenant):
        handle_salon_max_event(_event(payload, callback=True))


def _type(tenant, text: str) -> None:
    from apps.channels.max.salon_handler import handle_salon_max_event

    with tenant_scope(tenant):
        handle_salon_max_event(_event(text, callback=False))


def _text(sent) -> str:
    return sent.call_args.kwargs["text"]


def _buttons(sent) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for attachment in sent.call_args.kwargs.get("attachments") or []:
        for row in (attachment.get("payload") or {}).get("buttons") or []:
            out.extend((b.get("text", ""), b.get("payload", "")) for b in row)
    return out


def _payload_of(sent, label: str) -> str:
    matches = [p for t, p in _buttons(sent) if t == label]
    assert matches, (label, _buttons(sent))
    return matches[0]


def _today() -> str:
    return timezone.now().astimezone(MSK).date().isoformat()


def _to_review_with_new_client(tenant, sent, master, service) -> None:
    _tap(tenant, "cb:staff:bk_new")
    _tap(tenant, f"cb:staff:bk_m:{master.id}")
    _tap(tenant, f"cb:staff:bk_s:{service.id}")
    _tap(tenant, f"cb:staff:bk_d:{_today()}")
    _tap(tenant, _payload_of(sent, "10:00"))
    _tap(tenant, "cb:staff:bk_nc")
    _type(tenant, "Ольга")
    _type(tenant, "+79990001122")


class TestFlow:
    def test_b1_menu_carries_the_chat_entry_beside_the_mini_app(self, tenant, sent):
        _staff(tenant)

        _tap(tenant, "cb:staff:nonexistent")  # shows the menu

        labels = [t for t, _ in _buttons(sent)]
        assert "✍️ Записать клиента" in labels
        assert _payload_of(sent, "✍️ Записать клиента") == "cb:staff:bk_new"

    def test_b2_the_whole_path_ends_in_one_created_booking(self, tenant, sent, ayla):
        admin = _staff(tenant)
        master, service = _catalog(tenant)
        booking, salon = ayla()

        _tap(tenant, "cb:staff:bk_new")
        assert _text(sent).startswith("*Новая запись*\nМастер: выбрать")
        _tap(tenant, _payload_of(sent, "Анна"))
        _tap(tenant, _payload_of(sent, "Маникюр"))
        _tap(tenant, f"cb:staff:bk_d:{_today()}")
        # Only stamped starts are offered — 11:00 came without a timestamp.
        times = [t for t, _ in _buttons(sent) if t[:2].isdigit()]
        assert times == ["10:00", "12:00"]
        _tap(tenant, _payload_of(sent, "10:00"))
        assert "Поиск клиента — напишите имя или телефон." in _text(sent)
        _tap(tenant, _payload_of(sent, "Новый клиент"))
        _type(tenant, "Ольга")
        assert _text(sent).endswith("Телефон клиента")
        _type(tenant, "+79990001122")

        review = _text(sent)
        assert review.startswith("*Проверьте запись*")
        assert "Мастер: Анна" in review and "Услуга: Маникюр" in review
        assert "Клиент: Ольга (новый)" in review
        _tap(tenant, _payload_of(sent, "Создать запись"))

        assert _text(sent) == "Запись создана."
        (created,) = salon.created
        assert created["actor_external_id"] == f"bot:max:{admin.channel_user_id}"
        assert created["specialist_id"] == str(master.catalog_specialist_id)
        assert created["service_id"] == str(service.ayla_service_id)
        assert created["start_datetime"] == f"{_today()}T10:00:00+03:00"
        assert (created["client_id"], created["client_name"], created["client_phone"]) == (
            None,
            "Ольга",
            "+79990001122",
        )

    def test_b3_an_existing_client_is_chosen_by_search(self, tenant, sent, ayla):
        _staff(tenant)
        master, service = _catalog(tenant)
        _, salon = ayla()

        _tap(tenant, "cb:staff:bk_new")
        _tap(tenant, f"cb:staff:bk_m:{master.id}")
        _tap(tenant, f"cb:staff:bk_s:{service.id}")
        _tap(tenant, f"cb:staff:bk_d:{_today()}")
        _tap(tenant, _payload_of(sent, "12:00"))
        _type(tenant, "Мар")

        assert salon.searched == ["Мар"]
        labels = [t for t, _ in _buttons(sent)]
        # A channel handle is not a name — the Mini App's rule, reused.
        assert "Мария Иванова" in labels and "Без имени" in labels
        _tap(tenant, _payload_of(sent, "Мария Иванова"))
        assert "Клиент: Мария Иванова" in _text(sent)
        _tap(tenant, "cb:staff:bk_ok")

        (created,) = salon.created
        assert (created["client_id"], created["client_name"], created["client_phone"]) == (
            "c-1",
            None,
            None,
        )

    def test_b4_no_matches_says_so_and_offers_a_new_client(self, tenant, sent, ayla):
        _staff(tenant)
        master, service = _catalog(tenant)
        ayla()

        _tap(tenant, "cb:staff:bk_new")
        _tap(tenant, f"cb:staff:bk_m:{master.id}")
        _tap(tenant, f"cb:staff:bk_s:{service.id}")
        _tap(tenant, f"cb:staff:bk_d:{_today()}")
        _tap(tenant, _payload_of(sent, "10:00"))
        _type(tenant, "Пётр")

        assert _text(sent) == (
            "Совпадений нет. Возможно, клиент записан под другим именем или телефоном."
        )
        assert _payload_of(sent, "Новый клиент") == "cb:staff:bk_nc"


class TestOutcomes:
    def test_o1_taken_time_keeps_the_draft_and_offers_fresh_starts(self, tenant, sent, ayla):
        _staff(tenant)
        master, service = _catalog(tenant)
        _, salon = ayla(create_exc=[SalonSlotTaken("taken")])
        _to_review_with_new_client(tenant, sent, master, service)

        _tap(tenant, "cb:staff:bk_ok")

        assert _text(sent).startswith(
            "Это время уже занято. Выберите другое. Введённые данные сохранены."
        )
        _tap(tenant, _payload_of(sent, "12:00"))
        # The client was kept — straight back to the review.
        assert _text(sent).startswith("*Проверьте запись*")
        assert "Клиент: Ольга (новый)" in _text(sent)

    def test_o2_no_answer_retries_with_the_same_key(self, tenant, sent, ayla):
        _staff(tenant)
        master, service = _catalog(tenant)
        _, salon = ayla(create_exc=[SalonUnavailable("timeout")])
        _to_review_with_new_client(tenant, sent, master, service)

        _tap(tenant, "cb:staff:bk_ok")
        assert _text(sent).startswith("Ответ от расписания не пришёл.")
        _tap(tenant, _payload_of(sent, "Создать запись"))

        assert _text(sent) == "Запись создана."
        first, second = salon.created
        # One key on both — Ayla cannot make a second booking of the retry.
        assert first["idempotency_key"] == second["idempotency_key"]


class TestDraftLifecycle:
    def test_l1_a_gone_draft_says_so_and_offers_to_start_again(self, tenant, sent, ayla):
        _staff(tenant)
        master, _ = _catalog(tenant)
        ayla()

        _tap(tenant, f"cb:staff:bk_m:{master.id}")

        assert _text(sent) == "Черновик записи устарел — начните заново."
        assert _payload_of(sent, "✍️ Записать клиента") == "cb:staff:bk_new"

    def test_l2_another_button_drops_the_draft_so_typing_is_not_a_search(self, tenant, sent, ayla):
        _staff(tenant)
        master, service = _catalog(tenant)
        _, salon = ayla()
        _tap(tenant, "cb:staff:bk_new")
        _tap(tenant, f"cb:staff:bk_m:{master.id}")
        _tap(tenant, f"cb:staff:bk_s:{service.id}")
        _tap(tenant, f"cb:staff:bk_d:{_today()}")
        _tap(tenant, _payload_of(sent, "10:00"))  # now waiting for a client

        _tap(tenant, "cb:staff:day")
        _type(tenant, "Мар")

        assert salon.searched == []

    def test_l3_the_front_desk_has_no_entry_and_cannot_step(self, tenant, sent, ayla):
        # Control: the booking write is owner / administrator only.
        _staff(tenant, role=StaffInvite.Role.RECEPTIONIST)
        _catalog(tenant)
        _, salon = ayla()

        _tap(tenant, "cb:staff:bk_new")

        assert "✍️ Записать клиента" not in [t for t, _ in _buttons(sent)]
        # What came back is the menu — the step did not advance.
        assert _text(sent).startswith("Салон «Запись в чате».")
        assert "Новая запись" not in _text(sent)
        assert salon.created == []


def test_ttl_is_fifteen_minutes() -> None:
    from apps.channels.max.staff_booking import DRAFT_TTL_SECONDS

    assert DRAFT_TTL_SECONDS == int(timedelta(minutes=15).total_seconds())
