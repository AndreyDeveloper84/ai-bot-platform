"""Меню §37 через НАСТОЯЩИЙ вход — семь кнопок, «Ещё», карточка (DRF-1547).

Тесты гоняют ``handle_global_max_event``, тот же путь, которым идёт живой
клиентский бот. Соседний файл ``test_marketplace_menu_drf1491.py`` держит
инварианты прежней задачи; здесь — то, чего до §37 не существовало:

* **подменю «Ещё»** — механизма подменю не было вовсе;
* **действия на карточке КОНКРЕТНОЙ записи** — «Перенести» и «Отменить»
  были пунктами главного меню, и за пунктом «Отменить» не стояло ничего:
  тап превращался в фразу «Отменить запись» и уезжал консьержу, у
  которого глагола отмены нет в реестре инструментов;
* **предупреждение перед открытием приложения** — не было ни в одной
  кнопке;
* **возврат к дневнику после согласия** — поток согласия оставлял
  человека в профиле.

Правило DRF-1411 соблюдено буквально: рядом с каждым «этого нет» стоит
«а вот это есть» на тех же данных, и положительная стража идёт ПЕРВОЙ.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.booking.services.records import Visit, VisitsResult
from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator import visits as visits_mod
from apps.orchestrator.memory import short_term
from apps.skills.menu.marketplace import (
    CALLBACK_EXTRA_BACK,
    CALLBACK_EXTRA_HELP,
    CALLBACK_EXTRA_OPEN,
    CALLBACK_HEALTH_NEED_PREFIX,
    DIARY_TAP_TEXT,
    DISCOVER_TAP_TEXT,
    HEALTH_REQUEST_TEXT,
    WARN_GOAL,
    WARN_PROFILE,
)

pytestmark = pytest.mark.django_db

_CHAT_ID = 7712
_UUID_A = "11111111-1111-4111-8111-111111111111"
_UUID_B = "22222222-2222-4222-8222-222222222222"

#: Заведомо будущая дата: «предстоящая запись» решается сравнением с
#: часами, и один забытый год превратил бы проверку в проверку прошлого.
_FUTURE = "2099-01-15T09:30:00+00:00"


# --------------------------------------------------------------------------- #
# Оснастка                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _pilot_config(settings):
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.MAX_BOT_WEB_APP = "aylabot"
    settings.MAX_MINIAPP_URL = ""


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock(return_value=None))


def _recording_send(sink: list):
    """Заглушка исходящего, принимающая ОБА ключа адресации (DRF-1558).

    Возврат после согласия пишет человеку первым и потому уходит по
    ``user_id``; ответ внутри хода по-прежнему уходит по ``chat_id``.
    Заглушка, знающая только один ключ, роняет вызов внутрь ``except``
    вызывающего и превращает «адрес поменялся» в «доставки не было» —
    ровно то, на чём этот файл покраснел.

    ``key`` записывается рядом с адресом: без него возврат на диалог
    прошёл бы мимо теста, потому что оба значения лежат на одной строке.
    """

    def _send(*, chat_id=None, user_id=None, text, attachments=None, timeout=10.0):
        sink.append(
            {
                "chat_id": chat_id,
                "user_id": user_id,
                "addr": user_id if user_id is not None else chat_id,
                "key": "user_id" if user_id is not None else "chat_id",
                "text": text,
                "attachments": attachments,
            }
        )
        return {"ok": True}

    return _send


@pytest.fixture
def sent(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def concierge(monkeypatch):
    """Консьерж-шпион — им отвечаются пункты, у которых нет своей ветки."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(
        return_value=DiscoveryReply(text="Что именно хотите привести в порядок?", persisted=False)
    )
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


@pytest.fixture
def health_consent(monkeypatch):
    def _set(granted: bool) -> None:
        monkeypatch.setattr("apps.consent.health.is_granted", lambda _bot_user: granted)

    _set(False)
    return _set


@pytest.fixture
def bookings(monkeypatch):
    """Записи человека под сценарий — без единого похода в сеть."""
    state: dict = {
        "upcoming": VisitsResult(status="empty"),
        "visits": VisitsResult(status="empty"),
        "visit": None,
        "cancelled": [],
        "cancel_status": "ok",
    }

    def _cancel(*, bot_user, appointment_id):
        state["cancelled"].append(appointment_id)
        return state["cancel_status"]

    monkeypatch.setattr(visits_mod, "list_upcoming", lambda **_: state["upcoming"])
    monkeypatch.setattr(visits_mod, "list_visits", lambda **_: state["visits"])
    monkeypatch.setattr(
        "apps.booking.services.records.get_visit",
        lambda *, bot_user, appointment_id: state["visit"],
    )
    monkeypatch.setattr("apps.booking.services.records.cancel_booking", _cancel)
    return state


def _visit(
    *,
    appointment_id: str = _UUID_A,
    service: str = "Маникюр",
    master: str = "Инна",
    start: str = _FUTURE,
    price: Decimal | None = None,
) -> Visit:
    return Visit(
        appointment_id=appointment_id,
        service_name=service,
        master_name=master,
        start_at=start,
        price=price,
    )


def _msg(*, text: str, user_id: int, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _tap(*, payload: str, user_id: int, callback_id: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": callback_id,
            "user": {"user_id": user_id, "name": "Ирина"},
            "payload": payload,
        },
        "message": {"recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"}},
    }


def _welcomed(user_id: int):
    """Человек, уже прошедший первый контакт: C01 его ход не забирает."""
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id=str(_CHAT_ID)
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf1547",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _keyboard(call: dict) -> list[dict]:
    attachments = call["attachments"]
    assert attachments, f"клавиатуры нет вовсе: {call['text']!r}"
    rows = attachments[0]["payload"]["buttons"]
    return [btn for row in rows for btn in row]


def _payloads(call: dict) -> list[str]:
    return [btn.get("payload", "") for btn in _keyboard(call)]


def _labels(call: dict) -> list[str]:
    return [btn.get("text", "") for btn in _keyboard(call)]


def _user_messages(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="user")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


# --------------------------------------------------------------------------- #
# 1. Живой проход по каждой из семи кнопок                                     #
# --------------------------------------------------------------------------- #
class TestEverySevenButtonDoesWhatItPromises:
    """Тап -> обещанное поведение, на настоящем входе."""

    def test_the_menu_draws_the_owner_s_seven(self, sent, fake_redis, concierge):
        _welcomed(71001)

        max_handler.handle_global_max_event(_msg(text="меню", user_id=71001, mid="s-0"))

        assert sent[0]["text"].startswith("Что хотите сделать?")
        assert _labels(sent[0]) == [
            "Подобрать услугу",
            "Найти салон",
            # Значки — решение владельца 07.09.2026 (пересмотр §37 п.7).
            # Пара «Записаться»/«Мои записи» делит корень и стоит соседними
            # строками одного столбика: это единственное место в семёрке,
            # где значок различает пункты, а не украшает их.
            "📅 Записаться",
            "📋 Мои записи",
            "Моя цель",
            "Профиль",
            # OD-UI-2 («Ещё убираем, помощь в главное меню»): подменю
            # снесено, и «Помощь» заняла его место последней кнопкой.
            # Пищевого пункта здесь нет, потому что ворота закрыты —
            # ``NUTRITION_ENABLED`` в этом файле не поднимался.
            "Помощь",
        ]

    def test_pick_a_service_asks_what_the_person_wants(self, sent, fake_redis, concierge):
        """§37: «бот спрашивает, что человек хочет получить»."""
        _, conversation = _welcomed(71002)

        max_handler.handle_global_max_event(_msg(text=DISCOVER_TAP_TEXT, user_id=71002, mid="s-1"))

        # Стража: ответ есть, и это вопрос о желаемом исходе.
        assert sent[0]["text"] == "Что именно хотите привести в порядок?"
        assert concierge.call_count == 1
        # Тап неотличим от набранного текста (DRF-1348): в истории фраза.
        assert _user_messages(conversation) == [DISCOVER_TAP_TEXT]

    def test_find_a_salon_answers_in_the_chat(self, sent, fake_redis, concierge):
        """§37 п.4 — переименование, поведение прежнее: салоны В ЧАТЕ."""
        _welcomed(71003)

        max_handler.handle_global_max_event(
            _tap(payload="cb:catalog:salons", user_id=71003, callback_id="s-2")
        )

        # Стража: бот ответил сам, приложение не открывалось.
        assert len(sent) == 1 and sent[0]["text"], sent
        assert concierge.call_count == 0
        assert not [b for b in (sent[0]["attachments"] or []) if b.get("type") == "open_app"], sent[
            0
        ]["attachments"]

    def test_my_bookings_lists_them_with_actions(self, sent, fake_redis, concierge, bookings):
        bookings["upcoming"] = VisitsResult(status="ok", visits=(_visit(),))
        _welcomed(71004)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:my_bookings", user_id=71004, callback_id="s-3")
        )

        assert "Ваши предстоящие записи" in sent[0]["text"], sent[0]["text"]
        assert _labels(sent[0]) == [
            "Подробнее: Маникюр",
            "Перенести: Маникюр",
            "Отменить: Маникюр",
        ]

    def test_my_bookings_also_lists_the_past_in_the_chat(
        self, sent, fake_redis, concierge, bookings
    ):
        """§62 / OD-UI-1 — одна кнопка, оба ответа, и оба В ЧАТЕ.

        Это то, ради чего «История визитов» перестала быть отдельным
        пунктом. Раньше прошлые визиты в боте не показывались вообще:
        пункт был экранным и уводил на ``/customer/records``, который
        открывается на вкладке «Ближайшие».

        Проверяется ровно живой путь: настоящий вход, настоящая
        лестница. И детерминированность здесь не мелочь — ``concierge``
        не звался ни разу, то есть тап по кнопке не ушёл в модель
        (регрессия DRF-1051).
        """
        bookings["visits"] = VisitsResult(
            status="ok",
            visits=(
                _visit(
                    appointment_id=_UUID_B,
                    service="Массаж спины",
                    start="2026-08-12T09:30:00+00:00",
                ),
            ),
        )
        _welcomed(71014)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:my_bookings", user_id=71014, callback_id="s-14")
        )

        assert "Ваши последние визиты:" in sent[0]["text"], sent[0]["text"]
        assert "Массаж спины" in sent[0]["text"], sent[0]["text"]
        assert concierge.call_count == 0
        # Приложение не открывалось: пять визитов и меньше читаются целиком.
        assert not [b for b in (sent[0]["attachments"] or []) if b.get("type") == "open_app"], sent[
            0
        ]["attachments"]

    def test_my_bookings_says_out_loud_that_the_history_is_empty(
        self, sent, fake_redis, concierge, bookings
    ):
        """Ноль завершённых визитов — сегодняшняя норма пилота, не сбой."""
        bookings["upcoming"] = VisitsResult(status="ok", visits=(_visit(),))
        bookings["visits"] = VisitsResult(status="empty")
        _welcomed(71015)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:my_bookings", user_id=71015, callback_id="s-15")
        )

        # Стража: ответ построен и предстоящая половина в нём есть.
        assert "Ваши предстоящие записи" in sent[0]["text"], sent[0]["text"]
        # И только теперь — вторая половина, которая раньше молчала.
        assert "Завершённых визитов пока нет" in sent[0]["text"], sent[0]["text"]

    @pytest.mark.parametrize(
        ("payload", "warning", "slug"),
        [
            ("cb:open:goal_select", WARN_GOAL, "open_goal_select"),
            ("cb:open:profile", WARN_PROFILE, "open_profile"),
        ],
    )
    def test_a_screen_button_warns_first_and_opens_second(
        self, sent, fake_redis, concierge, payload, warning, slug
    ):
        """§37 п.6 — предупреждение непосредственно перед открытием.

        Два хода вместо одного — не бюрократия, а единственный способ
        сказать что-либо ПЕРЕД открытием: ``open_app`` на MAX открывает
        приложение мгновенно.
        """
        _welcomed(71005)

        max_handler.handle_global_max_event(
            _tap(payload=payload, user_id=71005, callback_id=f"s-{payload}")
        )

        # Стража: предупреждение показано дословно, и под ним ровно одна
        # кнопка — та, что открывает нужный экран.
        assert sent[0]["text"] == warning, sent[0]["text"]
        buttons = _keyboard(sent[0])
        assert len(buttons) == 1, buttons
        assert buttons[0]["type"] == "open_app", buttons
        assert buttons[0]["text"] == "Открыть"
        assert buttons[0]["payload"] == slug, buttons

    def test_yesterdays_more_and_back_still_answer_with_the_menu(self, sent, fake_redis, concierge):
        """OD-UI-2 — подменю снесено, но вчерашние кнопки живы в переписке.

        Раньше ``cb:extra:open`` открывал «Дополнительные возможности», а
        ``cb:extra:back`` возвращал. Экрана больше нет; ветка осталась,
        потому что клавиатуры живут в истории чата дольше кода. Тап по
        кнопке, которую бот сам нарисовал, обязан дойти до ОТВЕТА — иначе
        сырой ``cb:extra:…`` уедет модели (DRF-1051) или человек получит
        молчание.
        """
        _welcomed(71006)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=71006, callback_id="s-5")
        )
        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_BACK, user_id=71006, callback_id="s-5b")
        )

        assert len(sent) == 2, sent
        for reply in sent:
            assert reply["text"].startswith("Что хотите сделать?"), reply["text"]
            assert "Помощь" in _labels(reply), _labels(reply)
        # Экрана подменю больше нет ни у одного из двух ходов.
        assert not [r for r in sent if r["text"] == "Дополнительные возможности"], sent
        assert concierge.call_count == 0

    def test_the_menu_no_longer_offers_a_way_into_the_submenu(self, sent, fake_redis, concierge):
        """…и нарисовать «Ещё» заново меню уже не может."""
        _welcomed(71007)

        max_handler.handle_global_max_event(_msg(text="меню", user_id=71007, mid="s-6"))

        labels = _labels(sent[0])
        payloads = _payloads(sent[0])
        # Стража НА ТЕХ ЖЕ данных: меню построено и полно — и по подписям,
        # и по payload'ам.
        assert "Профиль" in labels, labels
        assert "Помощь" in labels, labels
        assert CALLBACK_EXTRA_HELP in payloads, payloads
        # И только теперь отрицания.
        assert "Ещё" not in labels, labels
        assert "Назад" not in labels, labels
        assert CALLBACK_EXTRA_OPEN not in payloads, payloads
        assert CALLBACK_EXTRA_BACK not in payloads, payloads

    def test_help_from_the_main_menu_answers_with_the_menu(self, sent, fake_redis, concierge):
        """§25 п.2 — «отвечаем меню, а не свободной прозой».

        Главная проверка переезда «Помощи» (OD-UI-2). Payload остался
        ``cb:extra:help``, и это несущее решение: ``resolve_tap_text``
        забирает весь ``cb:menu:*`` ВЫШЕ этой лестницы и подставляет
        каноническую фразу, а для ``cb:menu:help`` на глобальном пути эта
        фраза — «Что ты умеешь?», уезжающая КОНСЬЕРЖУ. То есть наивный
        переезд дал бы прозу модели вместо меню.

        Здесь проверяется живой путь целиком: тап приходит настоящим
        webhook'ом, отвечает меню, и консьерж не звался НИ РАЗУ.
        """
        _welcomed(71008)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_HELP, user_id=71008, callback_id="s-8")
        )

        assert len(sent) == 1, sent
        assert sent[0]["text"].startswith("Что хотите сделать?")
        assert "Помощь" in _labels(sent[0]), _labels(sent[0])
        assert concierge.call_count == 0

    def test_the_naive_help_payload_would_have_gone_to_the_concierge(self):
        """Стража к предыдущему: ловушка настоящая, а не выдуманная.

        Если однажды ``cb:menu:help`` перестанет перехватываться выше
        лестницы, предыдущий тест этого не заметит — а этот заметит и
        скажет, что довод устарел.
        """
        from apps.channels.max.quick_actions import resolve_tap_text
        from apps.skills.menu.matching import CALLBACK_MENU_HELP

        assert resolve_tap_text(CALLBACK_MENU_HELP) == "Что ты умеешь?"
        assert resolve_tap_text(CALLBACK_EXTRA_HELP) is None

    def test_navigation_taps_never_land_in_history(self, sent, fake_redis, concierge):
        """«Ещё» — не высказывание, а сырой ``cb:`` в истории это DRF-988."""
        _, conversation = _welcomed(71009)

        max_handler.handle_global_max_event(
            _tap(payload=CALLBACK_EXTRA_OPEN, user_id=71009, callback_id="s-9")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:open:profile", user_id=71009, callback_id="s-10")
        )

        # Стража: ходы состоялись, ответы бота есть, и человек, который
        # ДЕЙСТВИТЕЛЬНО что-то сказал, в истории появляется.
        assert len(sent) == 2, sent
        max_handler.handle_global_max_event(_msg(text="хочу массаж", user_id=71009, mid="s-11"))
        assert _user_messages(conversation) == ["хочу массаж"]
        # И только теперь отрицание: за двумя навигационными тапами
        # реплик человека не записано ни одной — иначе сырой «cb:…» уехал
        # бы в промпт консьержа (DRF-988).


# --------------------------------------------------------------------------- #
# 2. Отменяется ИМЕННО выбранная запись                                        #
# --------------------------------------------------------------------------- #
class TestCancellingTheChosenBooking:
    """Главная проверка §37 п.1 — целиком через настоящий вход."""

    def test_two_bookings_and_the_second_one_is_the_one_that_goes(
        self, sent, fake_redis, concierge, bookings
    ):
        bookings["upcoming"] = VisitsResult(
            status="ok",
            visits=(
                _visit(appointment_id=_UUID_A, service="Маникюр"),
                _visit(appointment_id=_UUID_B, service="Массаж спины"),
            ),
        )
        _welcomed(71101)

        max_handler.handle_global_max_event(
            _tap(payload="cb:menu:my_bookings", user_id=71101, callback_id="c-1")
        )
        # Человек выбирает ВТОРУЮ запись — и подпись кнопки называет её.
        assert "Отменить: Массаж спины" in _labels(sent[0]), _labels(sent[0])

        bookings["visit"] = _visit(appointment_id=_UUID_B, service="Массаж спины")
        max_handler.handle_global_max_event(
            _tap(payload=f"cb:visit:cancel:{_UUID_B}", user_id=71101, callback_id="c-2")
        )
        # Экран подтверждения называет, ЧТО именно исчезнет.
        assert "Массаж спины" in sent[1]["text"], sent[1]["text"]
        assert "15 января" in sent[1]["text"], sent[1]["text"]
        # Ничего ещё не отменено.
        assert bookings["cancelled"] == []

        max_handler.handle_global_max_event(
            _tap(payload=f"cb:visit:drop:{_UUID_B}", user_id=71101, callback_id="c-3")
        )

        # Стража: отменена ровно выбранная, и бот сказал какая.
        assert bookings["cancelled"] == [_UUID_B]
        assert "Отменила: Массаж спины" in sent[2]["text"], sent[2]["text"]
        # И только теперь отрицание: первая запись не тронута.
        assert _UUID_A not in bookings["cancelled"]

    def test_keeping_the_booking_returns_to_its_card(self, sent, fake_redis, concierge, bookings):
        bookings["visit"] = _visit(appointment_id=_UUID_A)
        _welcomed(71102)

        max_handler.handle_global_max_event(
            _tap(payload=f"cb:visit:cancel:{_UUID_A}", user_id=71102, callback_id="k-1")
        )
        max_handler.handle_global_max_event(
            _tap(payload=f"cb:visit:card:{_UUID_A}", user_id=71102, callback_id="k-2")
        )

        # Стража: карточка вернулась со своими действиями.
        assert f"cb:visit:move:{_UUID_A}" in _payloads(sent[1]), _payloads(sent[1])
        # И только теперь отрицание: отмены не произошло.
        assert bookings["cancelled"] == []


# --------------------------------------------------------------------------- #
# 3. Дневник питания через «Ещё» и через БОТА                                  #
# --------------------------------------------------------------------------- #
class TestDiaryThroughTheBot:
    """§37 п.5 — дневник вернулся, но ботовым путём."""

    def test_the_diary_button_lands_on_the_diary_not_on_the_app(
        self, sent, fake_redis, concierge, settings, health_consent, monkeypatch
    ):
        from apps.orchestrator.discovery import DiscoveryReply

        settings.NUTRITION_ENABLED = True
        health_consent(True)
        rendered: list[str] = []

        def _render(bot_user, *, period="today"):
            rendered.append(period)
            return DiscoveryReply(text="Питание за сегодня. Белка 40 г.")

        monkeypatch.setattr("apps.orchestrator.personal_surface.render_diary", _render)
        _welcomed(71201)

        # Вход — ГЛАВНОЕ меню: с OD-UI-2 дневник живёт здесь, подменю нет.
        max_handler.handle_global_max_event(_msg(text="меню", user_id=71201, mid="d-1"))
        # Стража: пункт нарисован, и его payload это ФРАЗА, а не слаг.
        assert "🥗 Дневник питания" in _labels(sent[0]), _labels(sent[0])
        assert DIARY_TAP_TEXT in _payloads(sent[0]), _payloads(sent[0])

        max_handler.handle_global_max_event(_msg(text=DIARY_TAP_TEXT, user_id=71201, mid="d-2"))

        # Дневник ответил В ЧАТЕ, детерминированно, без модели.
        assert rendered == ["today"], rendered
        assert "Питание за сегодня" in sent[1]["text"], sent[1]["text"]
        assert concierge.call_count == 0

    def test_a_closed_gate_keeps_the_diary_out_of_the_main_menu(
        self, sent, fake_redis, concierge, settings, health_consent
    ):
        """Первая строка таблицы §25 п.6 пережила переезд (OD-UI-2).

        Согласие ЕСТЬ — и всё равно пункта нет: первые ворота считаются
        раньше вторых, и никакое согласие их не открывает. Дописать
        пищевой пункт в статический кортеж главного меню значило бы
        покраснеть именно здесь.
        """
        settings.NUTRITION_ENABLED = False
        health_consent(True)
        _welcomed(71206)

        max_handler.handle_global_max_event(_msg(text="меню", user_id=71206, mid="c-1"))

        labels = _labels(sent[0])
        payloads = _payloads(sent[0])
        # Стража НА ТЕХ ЖЕ данных: меню построено и полно — и по подписям,
        # и по payload'ам, среди которых ЕСТЬ ботовые фразы (то есть
        # отсутствие фразы дневника ниже — состав, а не пустой список).
        assert "Помощь" in labels, labels
        assert "📋 Мои записи" in labels, labels
        assert DISCOVER_TAP_TEXT in payloads, payloads
        # И только теперь отрицания — ни кнопки, ни запроса согласия.
        assert "🥗 Дневник питания" not in labels, labels
        assert DIARY_TAP_TEXT not in payloads, payloads
        assert not [p for p in payloads if p.startswith(CALLBACK_HEALTH_NEED_PREFIX)], payloads

    def test_without_consent_the_button_explains_and_leads_to_the_profile(
        self, sent, fake_redis, concierge, settings, health_consent
    ):
        settings.NUTRITION_ENABLED = True
        health_consent(False)
        _welcomed(71202)

        max_handler.handle_global_max_event(_msg(text="меню", user_id=71202, mid="g-1"))
        # Пункт стоит в ГЛАВНОМ меню (OD-UI-2) и ведёт на ЗАПРОС согласия,
        # а не в поверхность: вторая строка таблицы §25 п.6 пережила переезд.
        assert "🥗 Дневник питания" in _labels(sent[0]), _labels(sent[0])
        assert f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary" in _payloads(sent[0]), _payloads(sent[0])
        assert DIARY_TAP_TEXT not in _payloads(sent[0]), _payloads(sent[0])

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=71202,
                callback_id="g-2",
            )
        )

        # Стража: объяснение причины показано и профиль открывается.
        assert sent[1]["text"] == HEALTH_REQUEST_TEXT
        assert "152-ФЗ" in sent[1]["text"]
        assert "open_profile" in _payloads(sent[1]), _payloads(sent[1])

    def test_after_consent_the_person_is_returned_to_the_diary(
        self, sent, fake_redis, concierge, settings, health_consent, monkeypatch
    ):
        """§37 п.5 — «после согласия возвращает человека к дневнику».

        До этой правки поток согласия оставлял человека в профиле, и он
        должен был сам вспомнить, зачем туда шёл.
        """
        from apps.orchestrator.discovery import DiscoveryReply

        settings.NUTRITION_ENABLED = True
        health_consent(False)
        bot_user, _conversation = _welcomed(71203)

        monkeypatch.setattr(
            "apps.orchestrator.personal_surface.render_diary",
            lambda bot_user, **_: DiscoveryReply(text="Питание за сегодня. Белка 40 г."),
        )
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_message",
            _recording_send(sent),
        )

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=71203,
                callback_id="r-1",
            )
        )
        # Стража: человек стоит на экране запроса согласия.
        assert sent[-1]["text"] == HEALTH_REQUEST_TEXT

        # …соглашается в мини-приложении.
        from apps.orchestrator.health_return import resume_after_health_consent

        health_consent(True)
        assert resume_after_health_consent(bot_user) is True

        # И получает В ЧАТ то, ради чего согласие давал.
        assert "Питание за сегодня" in sent[-1]["text"], sent[-1]["text"]
        # DRF-1558 — и получает как ЧЕЛОВЕК, а не как диалог: возврат уходит
        # вне хода и пишет первым, а сохранённый ``chat_id`` принадлежит паре
        # «другой бот + человек». Ключ проверяется рядом с адресом: оба
        # значения лежат на одной строке, и без ключа возврат на диалог
        # прошёл бы мимо.
        assert sent[-1]["key"] == "user_id"
        assert sent[-1]["addr"] == str(bot_user.channel_user_id)

    def test_the_return_asks_for_the_welcome_cadence_not_the_ordinary_one(
        self, sent, fake_redis, concierge, settings, health_consent, monkeypatch
    ):
        """§39 — приветственное слово вне системы лимитов.

        Возврат после согласия отрисовывает дневник, и у человека с
        непустой неделей дневник может нести строку наблюдения диетолога.
        Она показывается, но суточный слот не тратит: слот остаётся целым
        для захода, который человек сделает сам.

        Здесь прибит СТЫК — что возврат просит именно эту категорию. Что
        категория действительно не трогает участок лимитов, прибито
        отдельно, в ``apps/orchestrator/tests/test_coach_observation.py``
        (``TestWelcomeOutsideLimits``), и решается там ВТОРЫМ заходом:
        первый заход §39 не различает.
        """
        from apps.orchestrator.coach_observation import Cadence
        from apps.orchestrator.discovery import DiscoveryReply

        settings.NUTRITION_ENABLED = True
        health_consent(False)
        bot_user, _conversation = _welcomed(71207)

        asked: list[dict] = []

        def _render(_bot_user, **kwargs):
            asked.append(kwargs)
            return DiscoveryReply(text="Питание за сегодня. Белка 40 г.")

        monkeypatch.setattr("apps.orchestrator.personal_surface.render_diary", _render)
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_message",
            _recording_send(sent),
        )

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=71207,
                callback_id="r-39",
            )
        )

        from apps.orchestrator.health_return import resume_after_health_consent

        health_consent(True)
        assert resume_after_health_consent(bot_user) is True

        # Присутствие выше отсутствия: дневник действительно отрисован...
        assert asked
        # ...и попрошен он как приветствие, а не как обычный заход.
        assert asked[-1].get("cadence") is Cadence.UNTRACKED

    def test_the_return_is_delivered_once_not_on_every_repeated_grant(
        self, sent, fake_redis, concierge, settings, health_consent, monkeypatch
    ):
        """POST согласия идемпотентен — доставка обязана быть тоже."""
        from apps.orchestrator.discovery import DiscoveryReply

        settings.NUTRITION_ENABLED = True
        health_consent(False)
        bot_user, _conversation = _welcomed(71204)

        monkeypatch.setattr(
            "apps.orchestrator.personal_surface.render_diary",
            lambda bot_user, **_: DiscoveryReply(text="Питание за сегодня."),
        )
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_message",
            _recording_send(sent),
        )

        max_handler.handle_global_max_event(
            _tap(
                payload=f"{CALLBACK_HEALTH_NEED_PREFIX}food_diary",
                user_id=71204,
                callback_id="i-1",
            )
        )
        from apps.orchestrator.health_return import resume_after_health_consent

        health_consent(True)
        # Стража: первая доставка состоялась.
        assert resume_after_health_consent(bot_user) is True
        delivered = len(sent)
        # И только теперь отрицание: вторая не состоялась.
        assert resume_after_health_consent(bot_user) is False
        assert len(sent) == delivered

    def test_a_person_who_never_asked_gets_nothing_pushed(
        self, sent, fake_redis, concierge, settings, health_consent
    ):
        """Возврат — это возврат, а не рассылка."""
        settings.NUTRITION_ENABLED = True
        bot_user, _conversation = _welcomed(71205)

        max_handler.handle_global_max_event(_msg(text="меню", user_id=71205, mid="q-1"))
        # Стража: диалог существует и в нём есть ход бота.
        assert len(sent) == 1, sent

        from apps.orchestrator.health_return import resume_after_health_consent

        assert resume_after_health_consent(bot_user) is False
        assert len(sent) == 1, sent
