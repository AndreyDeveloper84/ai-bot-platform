"""DRF-2769 (фаза 1) — утренний итог несёт кнопки персонала, а не одну дверь.

До правки итог владельцу / администратору шёл с единственной кнопкой
«Открыть салон» (ссылка в Mini App) — а под приветствием того же человека
уже жили и день салона, и заявки, и готовность, и экраны Mini App. Решение
владельца: «набор ок». Кнопки — существующие: колбэки меню персонала
(``cb:staff:*``, их обработчики — ``salon_handler._handle_button``, узлы —
``test_staff_menu`` и ``test_salon_readiness_2117``) и экраны из
``salon_greeting.admin_buttons``.

* d1 — итог уходит с чат-кнопками «день / заявки / готовность» и экранами
  Mini App; «Сегодня» — один раз;
* d2 — у салонного бота нет Mini App: экранов нет, чат-кнопки есть, итог
  уходит (прежде без Mini App у итога не было ни одной кнопки);
* d3 — колбэки итога — ровно те, что ловит обработчик персонала;
* d4 — без переданной клавиатуры уведомление прежнее: одна дверь.

Мастерам итог в этой фазе не идёт (адресаты — управляющие) — фаза 2.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from apps.channels.bot_registry import BotEntry
from apps.channels.max.tests import test_salon_morning_digest_2118 as _digest

pytestmark = pytest.mark.django_db

_salon = _digest._salon
_at = _digest._at
_data = _digest._data


def _registry(settings, **salon_entry) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="client",
            webhook_secret="wh-client",  # pragma: allowlist secret
            api_token="token-client",  # pragma: allowlist secret
            stream="max_global",
        ),
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token=_digest.SALON_TOKEN,
            stream="max_salon",
            **salon_entry,
        ),
    )
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret
    settings.SALON_MORNING_DIGEST_ENABLED = True


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    """Каждое сообщение провода — текст и кнопки, как они ушли бы в MAX."""
    seen: list[dict] = []

    def _fake_send(*, text, chat_id=None, user_id=None, attachments=None, **_):
        buttons = [
            b
            for a in (attachments or [])
            for row in a.get("payload", {}).get("buttons", [])
            for b in row
        ]
        seen.append({"text": text, "buttons": buttons})
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", _fake_send)
    return seen


def _send_digest(slug: str) -> None:
    from apps.channels.max import salon_digest as sd

    _salon(slug)
    with patch.object(sd, "gather_digest", return_value=_data()):
        sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)})


def _payloads(buttons: list[dict]) -> list[str]:
    return [b.get("payload", "") for b in buttons if b.get("type") == "callback"]


def _labels(buttons: list[dict]) -> list[str]:
    return [b.get("text", "") for b in buttons]


class TestD1TheAdminSet:
    def test_the_digest_carries_the_chat_actions_and_the_app_screens(self, settings, sent) -> None:
        from apps.channels.max import staff_menu
        from apps.channels.max.salon_greeting import (
            BUTTON_ASK_AYLA,
            BUTTON_CHECK_READINESS,
            BUTTON_NEW_BOOKING,
            BUTTON_OPEN_SALON,
            BUTTON_SCHEDULE,
            BUTTON_TODAY,
        )

        _registry(settings, miniapp_url="https://app.example")
        _send_digest("d1")

        assert len(sent) == 1  # положительно: итог ушёл, один управляющий
        labels = _labels(sent[0]["buttons"])
        assert labels == [
            staff_menu.LABEL_DAY_ADMIN,
            staff_menu.LABEL_REQUESTS,
            BUTTON_CHECK_READINESS,
            BUTTON_OPEN_SALON,
            BUTTON_SCHEDULE,
            BUTTON_ASK_AYLA,
            BUTTON_NEW_BOOKING,
        ], labels
        # «Сегодня» один раз: день салона — первой кнопкой, экран дня не дублирует её.
        assert BUTTON_TODAY not in labels

    def test_the_labels_are_the_menu_and_greeting_words(self) -> None:
        """Своих слов у итога нет. Литералом — подпись есть решение, константа её не держит."""
        from apps.channels.max import staff_menu

        assert staff_menu.LABEL_DAY_ADMIN == "📅 Сегодня"
        assert staff_menu.LABEL_REQUESTS == "🗒 Заявки от мастеров"
        assert staff_menu.LABEL_DAY_MASTER == "📅 Мой день"


class TestD2WithoutAMiniApp:
    def test_chat_buttons_stay_and_the_digest_still_goes(self, settings, sent) -> None:
        from apps.channels.max import staff_menu

        _registry(settings)  # ни web_app, ни miniapp_url
        _send_digest("d2")

        assert len(sent) == 1
        assert _labels(sent[0]["buttons"]) == [
            staff_menu.LABEL_DAY_ADMIN,
            staff_menu.LABEL_REQUESTS,
            "Проверить готовность",
        ]
        assert all(b.get("type") == "callback" for b in sent[0]["buttons"])


class TestD3TheCallbacksAreTheStaffHandlers:
    def test_every_digest_callback_is_one_the_staff_handler_answers(self, settings, sent) -> None:
        from apps.channels.max import staff_menu

        _registry(settings, miniapp_url="https://app.example")
        _send_digest("d3")

        payloads = _payloads(sent[0]["buttons"])
        assert payloads == [staff_menu.CB_DAY, staff_menu.CB_REQUESTS, staff_menu.CB_READINESS]
        assert payloads == ["cb:staff:day", "cb:staff:requests", "cb:staff:readiness"]


class TestD4TheNoticeWithoutAKeyboardIsUnchanged:
    def test_a_digest_notice_without_keyboard_keeps_the_single_door(self, settings) -> None:
        from datetime import date

        from apps.channels.max import salon_notify as sn

        _registry(settings, miniapp_url="https://app.example")
        tenant = _salon("d4")

        notice = sn.digest_notice(
            tenant, local_date=date(2026, 9, 21), lines=["Сегодня:", "7 записей."]
        )

        assert notice.keyboard == ()
        assert [b.label for b in notice.buttons] == ["Открыть салон"]
