"""Дверь соло-мастера ведёт в кабинет, а не только сообщает о нём (DRF-1756, срез 3 DRF-1705).

Фриз соло-онбординга §5: после создания рабочего пространства — действие
«Открыть кабинет», и «реализация не завершена, если аккаунт есть, а
мастер не может открыть обычное рабочее пространство». До этого среза оба
ответа соло-пути — ``SOLO_CREATED_PENDING`` и ``SOLO_ALREADY_REGISTERED`` —
уходили **без вложений**: из салонного бота в Mini App у соло-мастера не
было ни одной кнопки (замер `docs/SOLO_PATH_INPUTS_MEASUREMENT_DRF1705.md`
§1 шаг 6). А ``READY``-ветка строила меню по САЛОННОЙ строке человека
(``resolve_role(bot_user)`` = customer) и с ``entry=None`` — то есть тоже
без двери.

Кнопка берётся тем же способом, что у меню сотрудников
(``staff_menu._miniapp_button``): ``web_app`` записи бота, иначе
``miniapp_url``, иначе — ничего. Без записи или без адреса Mini App двери
нет, и тест это тоже стережёт: кнопка, которая не может сработать, хуже её
отсутствия.

§122 при этом не отменён: текст по-прежнему говорит, что клиентам мастера
пока не видно. «Готово 🎉» фриза §5 сюда не переносится — на этот текст
стоит сторож ``test_a_fresh_workspace_is_not_announced_as_ready``, и из двух
решений действует то, у которого есть сторож.
"""

from __future__ import annotations

import pytest

from apps.channels.bot_registry import BotEntry
from apps.channels.max import salon_handler
from apps.channels.max.staff_menu import OPEN_APP_PAYLOAD
from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import SoloSetupState
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


class _Event:
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat_id = "556"
        self.channel = "max"
        self.channel_user_id = "solo-door-2"
        self.raw: dict = {}


@pytest.fixture
def salon(db) -> Tenant:
    return Tenant.objects.create(slug="door-salon-2", name="Дверь 2", is_active=True)


@pytest.fixture
def bot_user(salon) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=salon, channel="max", channel_user_id="solo-door-2", display_name="Ольга"
    )


@pytest.fixture
def said(monkeypatch) -> list[dict]:
    out: list[dict] = []

    def _capture(event, text, attachments=None):
        out.append({"text": text, "attachments": attachments})

    monkeypatch.setattr(salon_handler, "_reply", _capture)
    return out


def _entry(**kw) -> BotEntry:
    base = dict(
        slug="salon",
        webhook_secret="wh-salon",  # pragma: allowlist secret
        api_token="tok-salon",  # pragma: allowlist secret
        tenant_slug="door-salon-2",
        stream="max_salon",
    )
    base.update(kw)
    return BotEntry(**base)


def _buttons(attachments) -> list[dict]:
    """Плоский список кнопок из вложений MAX — независимо от раскладки."""

    out: list[dict] = []
    for att in attachments or []:
        payload = att.get("payload") or {}
        for row in payload.get("buttons") or []:
            out.extend(row)
    return out


def _door(attachments) -> dict | None:
    for btn in _buttons(attachments):
        if btn.get("type") == "open_app" or "url" in btn:
            return btn
    return None


class TestTheFreshWorkspaceGetsADoor:
    def test_created_pending_carries_open_cabinet(self, bot_user, said):
        """Красный до правки: ``attachments is None``."""

        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK),
            entry=_entry(miniapp_url="https://app.example/solo"),
        )

        assert said[0]["text"] == salon_handler.SOLO_CREATED_PENDING
        assert "не видно клиентам" in said[0]["text"], "§122 не отменён кнопкой"
        door = _door(said[0]["attachments"])
        assert door is not None, said[0]["attachments"]
        assert door.get("url") == "https://app.example/solo"

    def test_web_app_wins_over_the_link(self, bot_user, said):
        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK),
            entry=_entry(miniapp_url="https://app.example/solo", web_app="solo-app"),
        )

        door = _door(said[0]["attachments"])
        assert door is not None and door.get("type") == "open_app", door
        assert door.get("payload") == OPEN_APP_PAYLOAD


class TestTheReturningOwnerGetsTheSameDoor:
    def test_already_registered_carries_open_cabinet(self, bot_user, said):
        """Красный до правки: второй визит отвечал текстом без вложений."""

        entry = _entry(miniapp_url="https://app.example/solo")
        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK), entry=entry
        )
        said.clear()

        salon_handler._ask_for_code_with_solo_offer(_Event("привет"), entry=entry)

        assert said[0]["text"] == salon_handler.SOLO_ALREADY_REGISTERED
        assert _door(said[0]["attachments"]) is not None, said[0]["attachments"]

    def test_a_second_tap_on_register_also_carries_it(self, bot_user, said):
        entry = _entry(miniapp_url="https://app.example/solo")
        event = _Event(salon_handler.SOLO_REGISTER_CALLBACK)

        salon_handler._register_solo_provider(event, entry=entry)
        salon_handler._register_solo_provider(event, entry=entry)

        assert said[1]["text"] == salon_handler.SOLO_ALREADY_REGISTERED
        assert _door(said[1]["attachments"]) is not None


class TestNoDoorThatCannotOpen:
    """Отрицательные контроли: без адреса Mini App кнопки нет — как у меню сотрудников."""

    def test_entry_without_an_app_gives_text_only(self, bot_user, said):
        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK), entry=_entry()
        )

        assert said[0]["text"] == salon_handler.SOLO_CREATED_PENDING
        assert not said[0]["attachments"]

    def test_no_entry_gives_text_only(self, bot_user, said):
        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK), entry=None
        )

        assert not said[0]["attachments"]

    def test_the_first_visit_offer_is_unchanged(self, bot_user, said):
        """Предложение завести кабинет — по-прежнему одна кнопка «Я работаю сам», без двери в несуществующий кабинет."""

        salon_handler._ask_for_code_with_solo_offer(
            _Event("привет"), entry=_entry(miniapp_url="https://app.example/solo")
        )

        buttons = _buttons(said[0]["attachments"])
        assert [b.get("payload") for b in buttons] == [salon_handler.SOLO_REGISTER_CALLBACK]
        assert _door(said[0]["attachments"]) is None


class TestTheReadyBranchBuildsTheMenuForTheSoloRow:
    """``READY`` сегодня недостижимо (ключа взяться неоткуда), но ветка есть — и строила меню не по той строке."""

    def test_menu_uses_the_solo_row_and_the_entry(self, bot_user, said, monkeypatch, salon):
        """Красный до правки: роль читалась по салонной строке (customer), entry=None."""

        from apps.identity.services import solo_onboarding

        solo = Tenant.objects.create(slug="solo-max-ready", name="Соло", is_active=True)
        solo_row = BotUser.all_tenants.create(
            tenant=solo, channel="max", channel_user_id="solo-door-2"
        )

        class _Ready:
            tenant = solo
            bot_user = solo_row
            created = False
            blocked_by = None
            setup_state = SoloSetupState.READY

        monkeypatch.setattr(
            solo_onboarding, "create_solo_provider", lambda **kw: _Ready(), raising=True
        )
        seen: list[tuple] = []
        monkeypatch.setattr(
            salon_handler,
            "_send_menu",
            lambda event, role_ctx, tenant, entry: seen.append((role_ctx, tenant, entry)),
        )
        entry = _entry(miniapp_url="https://app.example/solo")

        salon_handler._register_solo_provider(
            _Event(salon_handler.SOLO_REGISTER_CALLBACK), entry=entry
        )

        assert len(seen) == 1, said
        role_ctx, tenant, passed_entry = seen[0]
        assert tenant == solo
        assert passed_entry is entry
        # Роль — по строке соло-тенанта, не по салонной: resolve_role читает
        # TenantStaff/CatalogMaster в тенанте СВОЕЙ строки.
        assert role_ctx.bot_user_id == solo_row.id
