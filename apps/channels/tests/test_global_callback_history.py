"""DRF-990, продолжение: остальные семейства ``cb:`` в глобальной истории.

#1325 закрыл ``cb:anketa:*``. Гейт персистенса на глобальном пути
(``apps/channels/max/handler.py``) пропускает мимо истории ровно четыре
семейства — ``cb:book:*`` (DRF-988), ``cb:catalog:*`` (DRF-1304),
промежуточный тап уточнения (DRF-1362) и ``cb:anketa:*`` (DRF-990).
Всё остальное записывается ``record_global_message(content=event.text)``
дословно, и в истории с ролью ``user`` оказывается строка вида
``cb:welcome:consent_yes`` — то, что человек якобы НАПИСАЛ боту словами.

Замерено на dev перед этим PR прогоном по всем семействам; сырыми в
историю ложились ``cb:welcome:*`` (все восемь), ``cb:food:*`` (все шесть),
``cb:visit:*``, ``cb:discover:book:*`` и «протухший» тап
(``cb:qa:{снятый слаг}``, ``cb:retry:last`` без истории).

Два разных механизма, и разделение между ними содержательное:

* **фраза** — ``cb:welcome:*`` и ``cb:food:*``. Это высказывания человека
  о себе («Да, продолжим», «Не сейчас», «✅ В дневник», «❌ Не то»), и у
  обоих семейств есть НАБРАННЫЕ шаги, которые в историю попадают всегда
  (в приветствие входят с ``/start`` или со свободной фразы; еду называют
  текстом «борщ 300г» и уточняют текстом после «✏️ Уточнить»). Молчание
  оставило бы запись, где сказанное человеком есть, а его решение —
  подтвердил он или отверг — отсутствует;
* **молчание** — ``cb:visit:*``, ``cb:discover:book:*`` и протухший тап.
  Первые два несут id карточки, которую бот сам нарисовал, и открывают ту
  самую воронку записи, все ОСТАЛЬНЫЕ шаги которой уже молчат
  (``cb:book:*``); набранных шагов внутри неё нет, поэтому молчание
  однородно. У протухшего тапа подставить нечего по определению.

Правило контура: отрицательному утверждению нужна положительная стража на
тех же данных. Рядом с «в истории нет ``cb:``» всюду стоит либо «история
непуста и в ней ровно то, что человек сказал», либо «маршрутизация
получила payload нетронутым» — иначе тест зеленел бы на пустой выборке.

152-ФЗ: ``ConsentRecord`` пишется ДРУГИМ путём
(``run_onboarding_turn`` → ``_record_consent_journal`` →
``record_global_consent``), который истории диалога не читает и не пишет.
Здесь это не заявлено на словах, а пришпилено
:class:`TestConsentIsWrittenByAnotherPathEntirely`.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db


# --------------------------------------------------------------------------- #
# Оснастка — та же, что у DRF-990                                              #
# (apps/channels/tests/test_global_anketa_history.py)                          #
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action",
        lambda **kwargs: {"ok": True},
    )


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


@pytest.fixture(autouse=True)
def _no_intent_llm(monkeypatch):
    """Разбор намерения — второй вызов модели на ходу со свободным текстом.

    Сценарии здесь набирают текст («привет», «борщ 300г»), поэтому без этой
    заглушки тест ходил бы в сеть за разбором намерения — и падал бы по
    погоде, а не по существу.
    """
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock(return_value=None))


@pytest.fixture
def concierge(monkeypatch):
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(return_value=DiscoveryReply(text="Расскажи чуть подробнее?", persisted=False))
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _tap(*, payload: str, user_id: int, callback_id: str, chat_id: int = 8899) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": callback_id,
            "user": {"user_id": user_id, "name": "Ирина"},
            "payload": payload,
        },
        "message": {"recipient": {"chat_id": chat_id, "chat_type": "dialog"}},
    }


def _msg(*, text: str, user_id: int, mid: str, chat_id: int = 8899) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": user_id, "name": "Ирина"},
            "recipient": {"chat_id": chat_id, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _welcomed_user(user_id: int):
    from django.utils import timezone

    from apps.consent.services import record_global_consent

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    record_global_consent(
        bot_user,
        consent_type="personal_data",
        source="test:drf990-followup",
        document_version="welcome-s2-v1",
    )
    bot_user.refresh_from_db()
    return bot_user, resolve_active_global_conversation(bot_user)


def _fresh_user(user_id: int):
    """Пользователь без согласия и без ``welcomed_at`` — как на первом контакте."""
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id="8899"
    )
    return bot_user, resolve_active_global_conversation(bot_user)


def _user_messages(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="user")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


def _assistant_messages(conversation) -> list[str]:
    from apps.conversations.models import Message

    return list(
        Message.all_tenants.filter(conversation_id=conversation.id, role="assistant")
        .order_by("created_at")
        .values_list("content", flat=True)
    )


def _raw(history: list[str]) -> list[str]:
    return [line for line in history if line.startswith("cb:")]


def _lowercased(assertions: list) -> list:
    """Те же утверждения фикстуры, но с приведёнными к нижнему регистру строками.

    Нужно ровно для :data:`TestFoodGoldenFixturesStillReplay.CASE_MISMATCH` —
    чтобы исключение снимало РЕГИСТР, а не саму проверку.
    """
    out = []
    for item in assertions:
        if not isinstance(item, dict):
            out.append(item)
            continue
        lowered = {}
        for key, value in item.items():
            if isinstance(value, str):
                lowered[key] = value.lower()
            elif isinstance(value, list):
                lowered[key] = [v.lower() if isinstance(v, str) else v for v in value]
            else:
                lowered[key] = value
        out.append(lowered)
    return out


# --------------------------------------------------------------------------- #
# 1. cb:welcome:* — приветствие и согласие                                     #
# --------------------------------------------------------------------------- #
class TestWelcomeTapsAreThePhraseTheButtonCarried:
    """Шире анкеты: приветствие проходит КАЖДЫЙ новый пользователь пилота."""

    def test_consent_yes_is_not_a_raw_payload_in_history(self, sent, fake_redis, concierge):
        _, conversation = _fresh_user(96001)

        max_handler.handle_global_max_event(_msg(text="привет", user_id=96001, mid="w-0"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:start_s2", user_id=96001, callback_id="w-1")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:consent_yes", user_id=96001, callback_id="w-2")
        )

        history = _user_messages(conversation)
        # Положительная стража: выборка не пуста и в ней то, что человек НАБРАЛ.
        assert "привет" in history, history
        assert not _raw(history), history

    def test_the_consent_decision_survives_as_the_phrase_on_the_button(
        self, sent, fake_redis, concierge
    ):
        """«Да, продолжим» — решение человека, и оно остаётся в истории.

        Метка берётся из той же клавиатуры, которую человек и нажал
        (``apps.skills.welcome.skill.welcome_tap_labels``), а не из копии
        таблицы, которая разъедется при переименовании кнопки.
        """
        from apps.skills.welcome.skill import welcome_tap_labels

        _, conversation = _fresh_user(96002)

        max_handler.handle_global_max_event(_msg(text="привет", user_id=96002, mid="w-3"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:start_s2", user_id=96002, callback_id="w-4")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:consent_yes", user_id=96002, callback_id="w-5")
        )

        assert _user_messages(conversation)[-1] == welcome_tap_labels()["cb:welcome:consent_yes"]

    def test_the_refusal_survives_too(self, sent, fake_redis, concierge):
        """«Не сейчас» — отказ, и он в истории обязан быть.

        Ответ бота («ничего запоминать не буду») записывается всегда; без
        реплики человека он читается как решение, принятое ботом самим.
        """
        from apps.skills.welcome.skill import welcome_tap_labels

        _, conversation = _fresh_user(96003)

        max_handler.handle_global_max_event(_msg(text="привет", user_id=96003, mid="w-6"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:start_s2", user_id=96003, callback_id="w-7")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:consent_refuse", user_id=96003, callback_id="w-8")
        )

        # Положительная стража: ход отработал — бот ответил на отказ.
        assert _assistant_messages(conversation), "бот не ответил — проверка ниже ни о чём"
        assert _user_messages(conversation)[-1] == welcome_tap_labels()["cb:welcome:consent_refuse"]

    def test_every_shipped_welcome_button_resolves_to_its_own_label(self):
        """Таблица не переписана сюда — она читается из самой клавиатуры.

        Кнопка, добавленная через месяц, падает здесь, а не в истории у
        человека в чате.
        """
        from apps.channels.max.global_onboarding import resolve_welcome_tap
        from apps.skills.welcome.skill import welcome_tap_labels

        labels = welcome_tap_labels()
        assert labels, "клавиатура приветствия пуста — проверка ниже ни о чём"
        for payload, label in labels.items():
            tap = resolve_welcome_tap(payload)
            assert tap is not None, payload
            assert tap.history_text == label, payload

    def test_a_retired_welcome_button_leaves_no_user_turn(self, sent, fake_redis, concierge):
        """Снятая кнопка: форма правильная, метки нет — выдумать фразу нечем.

        Ровно правило DRF-990 для нераспознанного payload'а правильной формы:
        в историю не идёт ничего, но и сырой ``cb:`` в неё не попадает.
        """
        _, conversation = _fresh_user(96004)

        max_handler.handle_global_max_event(_msg(text="привет", user_id=96004, mid="w-9"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:no_such_button", user_id=96004, callback_id="w-10")
        )

        history = _user_messages(conversation)
        assert history == ["привет"], history

    def test_typed_lookalike_is_still_the_person_s_own_words(self, sent, fake_redis, concierge):
        """Разбирается ФОРМА, а не префикс — человек может набрать это руками."""
        typed = "cb:welcome: это я просто так написала"
        _, conversation = _welcomed_user(96005)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=96005, mid="w-11"))

        assert _user_messages(conversation) == [typed]

    def test_short_term_memory_gets_the_phrase_too(self, sent, fake_redis, concierge):
        _, conversation = _fresh_user(96006)

        max_handler.handle_global_max_event(_msg(text="привет", user_id=96006, mid="w-12"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:start_s2", user_id=96006, callback_id="w-13")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:consent_yes", user_id=96006, callback_id="w-14")
        )

        recent = short_term.recall(conversation.id)
        user_turns = [m for m in recent if m.get("role") == "user"]
        assert user_turns, "короткая память пуста — проверка ниже ничего не доказывает"
        assert not [m for m in user_turns if str(m.get("content", "")).startswith("cb:")], recent


# --------------------------------------------------------------------------- #
# 1b. 152-ФЗ — согласие пишется другим путём, и этот PR его не касается        #
# --------------------------------------------------------------------------- #
class TestConsentIsWrittenByAnotherPathEntirely:
    """Юридическая запись согласия и строка в истории — разные объекты.

    ``ConsentRecord`` пишет ``record_global_consent``
    (``apps/consent/services.py``), которую зовёт ``_record_consent_journal``
    из ``run_onboarding_turn`` (``apps/channels/max/global_onboarding.py``) по
    признаку ``result.meta["reply_kind"]``. Ни ``record_global_message``, ни
    ``short_term`` в этом пути не участвуют, а ``WelcomeSkill``
    маршрутизируется по ``context.message_text``, который остаётся НЕТРОНУТЫМ
    payload'ом — правка живёт только на месте записи в историю.

    Тест пришпиливает это на данных, а не на словах.
    """

    def test_consent_record_appears_on_the_tap(self, sent, fake_redis, concierge):
        from apps.consent.models import ConsentRecord

        bot_user, conversation = _fresh_user(96010)

        max_handler.handle_global_max_event(_msg(text="привет", user_id=96010, mid="c-0"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:start_s2", user_id=96010, callback_id="c-1")
        )
        max_handler.handle_global_max_event(
            _tap(payload="cb:welcome:consent_yes", user_id=96010, callback_id="c-2")
        )

        granted = set(
            ConsentRecord.all_tenants.filter(
                bot_user=bot_user, granted=True, withdrawn_at=None
            ).values_list("consent_type", flat=True)
        )
        assert granted == {
            ConsentRecord.ConsentType.PERSONAL_DATA.value,
            ConsentRecord.ConsentType.MEMORY_GREEN.value,
        }, granted

        bot_user.refresh_from_db()
        assert bot_user.consent_at is not None

        # ...и на ТЕХ ЖЕ данных: в истории нет сырого payload'а.
        assert not _raw(_user_messages(conversation)), _user_messages(conversation)

    def test_routing_still_sees_the_untouched_payload(self, sent, fake_redis, concierge):
        """Подмена сделана на месте записи в историю, а НЕ в ``event.text``.

        ``WelcomeSkill`` маршрутизируется по payload'у; если бы фраза
        подставлялась выше маршрутизации, опрос согласия сломался бы. Стража:
        ``run_onboarding_turn`` получает именно payload.
        """
        import apps.channels.max.handler as h

        seen: list[str] = []
        original = h.run_onboarding_turn

        def spy(conversation, bot_user, text, trace_id=None):
            seen.append(text)
            return original(conversation, bot_user, text, trace_id)

        _welcomed_user(96011)
        h.run_onboarding_turn = spy
        try:
            max_handler.handle_global_max_event(
                _tap(payload="cb:welcome:consent_yes", user_id=96011, callback_id="c-3")
            )
        finally:
            h.run_onboarding_turn = original

        assert seen == ["cb:welcome:consent_yes"], seen


# --------------------------------------------------------------------------- #
# 2. cb:food:* — буквальный близнец анкеты                                     #
# --------------------------------------------------------------------------- #
class TestFoodTapsAreThePhraseTheButtonCarried:
    """Тот же вход, что у анкеты (``_STRUCTURED_CALLBACK_PREFIXES``)."""

    def test_to_diary_tap_is_not_a_raw_payload_in_history(self, sent, fake_redis, concierge):
        _, conversation = _welcomed_user(95001)

        max_handler.handle_global_max_event(_msg(text="борщ 300г", user_id=95001, mid="f-0"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:food:to_diary:scan-1", user_id=95001, callback_id="f-1")
        )

        history = _user_messages(conversation)
        assert "борщ 300г" in history, history
        assert not _raw(history), history

    def test_the_confirmation_survives_as_the_label(self, sent, fake_redis, concierge):
        """«✅ В дневник» — подтверждение человека о том, что он съел.

        Именно оно делает запись дневника его записью; молчание оставило бы
        «борщ 300г» без ответа на вопрос, подтвердил он его или отверг.
        """
        from apps.orchestrator.nutrition_global import food_tap_labels

        _, conversation = _welcomed_user(95002)

        max_handler.handle_global_max_event(_msg(text="борщ 300г", user_id=95002, mid="f-2"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:food:to_diary:scan-1", user_id=95002, callback_id="f-3")
        )

        expected = food_tap_labels("scan-1")["cb:food:to_diary:scan-1"]
        assert _user_messages(conversation)[-1] == expected

    def test_the_rejection_survives_too(self, sent, fake_redis, concierge):
        """«❌ Не то» — поправка человека; потеряв её, модель повторит ошибку."""
        from apps.orchestrator.nutrition_global import food_tap_labels

        _, conversation = _welcomed_user(95003)

        max_handler.handle_global_max_event(_msg(text="борщ 300г", user_id=95003, mid="f-4"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:food:reject:scan-1", user_id=95003, callback_id="f-5")
        )

        expected = food_tap_labels("scan-1")["cb:food:reject:scan-1"]
        assert _user_messages(conversation)[-1] == expected

    def test_the_refless_pair_resolves_too(self, sent, fake_redis, concierge):
        """``cb:food:diary`` / ``cb:food:typo`` — без scan_id, та же машинка."""
        from apps.orchestrator.nutrition_global import food_tap_labels

        _, conversation = _welcomed_user(95004)

        max_handler.handle_global_max_event(
            _tap(payload="cb:food:typo", user_id=95004, callback_id="f-6")
        )

        history = _user_messages(conversation)
        assert history, "история пуста — проверка ниже ничего не доказывает"
        assert history[-1] == food_tap_labels("typo")["cb:food:typo"]

    def test_an_unknown_food_action_leaves_no_user_turn(self, sent, fake_redis, concierge):
        """Поле, которого нет в клавиатуре: форма правильная, метки нет."""
        _, conversation = _welcomed_user(95005)

        max_handler.handle_global_max_event(_msg(text="борщ 300г", user_id=95005, mid="f-7"))
        max_handler.handle_global_max_event(
            _tap(payload="cb:food:correct:nope:scan-1", user_id=95005, callback_id="f-8")
        )

        assert _user_messages(conversation) == ["борщ 300г"]

    def test_typed_lookalike_is_still_the_person_s_own_words(self, sent, fake_redis, concierge):
        typed = "cb:food: это я просто так написала"
        _, conversation = _welcomed_user(95006)

        max_handler.handle_global_max_event(_msg(text=typed, user_id=95006, mid="f-9"))

        assert _user_messages(conversation) == [typed]

    def test_routing_still_sees_the_untouched_payload(self, sent, fake_redis, concierge):
        """Еда маршрутизируется ПО payload'у — подмена стоит ниже маршрутизации."""
        import apps.channels.max.handler as h

        seen: list[str] = []
        original = h.try_handle_structured_nutrition_turn

        def spy(*args, **kwargs):
            seen.append(str(kwargs.get("text", "")))
            return original(*args, **kwargs)

        _welcomed_user(95007)
        h.try_handle_structured_nutrition_turn = spy
        try:
            max_handler.handle_global_max_event(
                _tap(payload="cb:food:to_diary:scan-1", user_id=95007, callback_id="f-10")
            )
        finally:
            h.try_handle_structured_nutrition_turn = original

        assert "cb:food:to_diary:scan-1" in seen, seen


# --------------------------------------------------------------------------- #
# 3. Карточки — молчание, как у cb:book:* и cb:catalog:*                       #
# --------------------------------------------------------------------------- #
class TestCardTapsAreNotSpeech:
    """``cb:visit:*`` и ``cb:discover:book:*`` — id карточки, а не слова.

    Обе ветки — вход в ту же воронку записи, все остальные шаги которой уже
    молчат (``cb:book:*``, DRF-988). Набранных шагов внутри неё нет, поэтому
    молчание однородно, и асимметрии, из-за которой анкете дали фразу, здесь
    не возникает.
    """

    @pytest.fixture
    def stub_visit_route(self, monkeypatch):
        from apps.orchestrator.discovery import DiscoveryReply

        seen: list[str] = []

        def fake(*, global_bot_user, callback_text, trace_id=None):
            seen.append(callback_text)
            return DiscoveryReply(text="Вот твой визит.")

        monkeypatch.setattr(max_handler, "route_visit_callback", fake)
        return seen

    @pytest.fixture
    def stub_discover_route(self, monkeypatch):
        from apps.orchestrator.discovery import DiscoveryReply

        seen: list[str] = []

        def fake(event, bot_user, trace_id):
            seen.append(event.text)
            return DiscoveryReply(text="Передаю тебя в салон.")

        monkeypatch.setattr(max_handler, "_discovery_handoff_reply", fake)
        return seen

    @pytest.mark.parametrize(
        ("payload", "user_id"),
        [
            ("cb:visit:card:11111111-1111-1111-1111-111111111111", 94001),
            ("cb:visit:repeat:11111111-1111-1111-1111-111111111111", 94002),
        ],
    )
    def test_visit_taps_leave_no_user_turn(
        self, payload, user_id, sent, fake_redis, concierge, stub_visit_route
    ):
        _, conversation = _welcomed_user(user_id)

        max_handler.handle_global_max_event(
            _msg(text="привет", user_id=user_id, mid=f"v-{user_id}")
        )
        max_handler.handle_global_max_event(
            _tap(payload=payload, user_id=user_id, callback_id=f"vt-{user_id}")
        )

        history = _user_messages(conversation)
        # Положительная стража на тех же данных: набранная реплика на месте,
        # и маршрутизация получила payload нетронутым.
        assert "привет" in history, history
        assert not _raw(history), history
        assert stub_visit_route == [payload], stub_visit_route

    def test_discover_book_tap_leaves_no_user_turn(
        self, sent, fake_redis, concierge, stub_discover_route
    ):
        payload = (
            "cb:discover:book:11111111-1111-1111-1111-111111111111"
            ":22222222-2222-2222-2222-222222222222"
        )
        _, conversation = _welcomed_user(94003)

        max_handler.handle_global_max_event(
            _msg(text="хочу маникюр в пензе", user_id=94003, mid="d-0")
        )
        max_handler.handle_global_max_event(_tap(payload=payload, user_id=94003, callback_id="d-1"))

        history = _user_messages(conversation)
        assert "хочу маникюр в пензе" in history, history
        assert not _raw(history), history
        assert stub_discover_route == [payload], stub_discover_route


# --------------------------------------------------------------------------- #
# 4. Протухший тап — подставить нечего, но и сырым он в историю не идёт        #
# --------------------------------------------------------------------------- #
class TestStaleTapIsNotSpeech:
    """DRF-1051 починил МАРШРУТИЗАЦИЮ протухшего тапа, но не персистенс.

    ``resolve_tap_text`` вернула None, ``is_stale_tap`` — True, модель
    payload'а не видит. Но ``record_global_message`` его всё ещё пишет.
    Фразы у снятой кнопки нет по определению («выдумать за человека фразу
    хуже»), поэтому единственный честный исход — молчание.
    """

    @pytest.mark.parametrize(
        ("payload", "user_id"),
        [("cb:qa:no_such_slug_anymore", 93001), ("cb:retry:last", 93002)],
    )
    def test_stale_tap_leaves_no_user_turn(self, payload, user_id, sent, fake_redis, concierge):
        from apps.channels.max.quick_actions import STALE_TAP_TEXT

        _, conversation = _welcomed_user(user_id)

        max_handler.handle_global_max_event(
            _tap(payload=payload, user_id=user_id, callback_id=f"s-{user_id}")
        )

        # Положительная стража: ход отработал — человек увидел экран «кнопка
        # устарела», а не молчание.
        assert sent, "бот не ответил — проверка ниже ничего не доказывает"
        assert STALE_TAP_TEXT[:20] in sent[-1]["text"], sent[-1]["text"]
        history = _user_messages(conversation)
        assert not _raw(history), history


# --------------------------------------------------------------------------- #
# 5. Golden-фикстуры затронутых путей на ГЛОБАЛЬНОМ пути                       #
# --------------------------------------------------------------------------- #
class TestFoodGoldenFixturesStillReplay:
    """``golden/food_scanner``, ``food_clarify``, ``food_correction``.

    Тот же довод, что у #1325: штатный гейт
    ``apps/replay/tests/test_live_path_gate.py`` набор ``golden/`` не берёт
    (``GATED_SETS`` = adversarial, voice), а CLI гоняет их через
    ``pipeline.turn``, у которого нет вызывающих вне тестов. Правится
    персистенс на ГЛОБАЛЬНОМ пути — на нём и проверяется.
    """

    SETS: ClassVar[tuple[str, ...]] = ("food_scanner", "food_clarify", "food_correction")

    #: Фикстуры, чей ``must_pass`` не выполняется на живом коде — и НЕ из-за
    #: этого PR. Ровно тот же класс расхождения, что у ``anketa_age_invalid_reask``
    #: в #1325: ``response_contains_any`` сравнивает подстроки БЕЗ приведения
    #: регистра (``apps/replay/assertions.py``: ``s in response_text``), а
    #: фикстуры написаны строчными:
    #:
    #:   food_scanner_cb_clarify   ищет «что не так»/«напиши»
    #:                             -> «Что не так? Напиши коротко…»
    #:   food_scanner_cb_to_diary  ищет «записала»/«дневник»
    #:                             -> «Записала: … ккал.»
    #:   food_clarify_cb_typo_ack  ищет «поняла»       -> «Поняла 🙂»
    #:
    #: Расхождение регистра, не поведения; ни тексты скиллов, ни фикстуры этим
    #: PR не тронуты — ``git diff origin/dev -- apps/skills/ apps/replay/``
    #: показывает только резолвер тапа еды.
    #:
    #: Исключение сделано ГРОМКИМ и самопроверяющимся: ``must_pass`` не
    #: снимается, а перепроверяется на тех же данных БЕЗ учёта регистра
    #: (см. цикл ниже). Если ответ поменяется по существу, а не заглавной
    #: буквой, тест упадёт — молча пройденной фикстуры не будет.
    CASE_MISMATCH: ClassVar[frozenset[str]] = frozenset(
        {
            "food_scanner_cb_clarify",
            "food_scanner_cb_to_diary",
            "food_clarify_cb_typo_ack",
        }
    )

    @pytest.fixture(autouse=True)
    def _nutrition_on(self, settings):
        """Фикстуры описывают ответы включённой нутриции.

        ``NUTRITION_ENABLED`` по умолчанию False, и тогда скиллы отвечают
        заглушкой «пока выключено» — фикстуры проверяли бы её, а не то
        поведение, которое они описывают.
        """
        settings.NUTRITION_ENABLED = True

    @pytest.fixture(autouse=True)
    def _nutrition_client(self, monkeypatch):
        """Ayla на месте: ``cb:food:to_diary`` — это её вызов ``log_meal``.

        ``AYLA_BASE_URL`` в тестовом окружении пуст, и без заглушки клиент
        падает на конструкторе — фикстура проверяла бы отсутствие адреса, а
        не поведение. Ответ канонический (``FoodLogResponse``), а не Mock:
        скилл рендерит из него текст.
        """
        from apps.integrations.ayla.nutrition_client import FoodLogResponse

        log = FoodLogResponse(
            log_id="log-1",
            dish_name="Борщ",
            meal_type="other",
            calories=320.0,
            raw={},
        )

        class _Client:
            async def log_meal(self, **kwargs):
                return log

        monkeypatch.setattr(
            "apps.skills.food_scanner.skill.get_nutrition_client", lambda: _Client()
        )

    def _fixtures(self, name: str):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__import__("apps.replay", fromlist=["x"]).__file__).parent
        return load_fixture_set(root / "fixtures" / "golden" / name)

    def _consented_user(self, user_id: int):
        """Пользователь, у которого сканер еды уже разрешён.

        ``FoodScannerSkill`` держит собственный 152-ФЗ гейт на поле
        ``BotUser.food_scanner_consent_at`` и без него отвечает «открой Mini
        App и подтверди согласие». Фикстуры описывают поведение согласившегося
        человека, поэтому согласие ставит оснастка — иначе проверялся бы гейт,
        а не то, что фикстуры описывают.
        """
        from django.utils import timezone

        bot_user, conversation = _welcomed_user(user_id)
        bot_user.food_scanner_consent_at = timezone.now()
        bot_user.save(update_fields=["food_scanner_consent_at"])
        return bot_user, conversation

    def test_the_fixture_sets_are_the_real_ones(self):
        """Стража сторожа: перебор ниже читает настоящие YAML."""
        counts = {name: len(list(self._fixtures(name))) for name in self.SETS}
        assert counts == {"food_scanner": 5, "food_clarify": 5, "food_correction": 5}, counts

    def test_the_replayed_subset_is_exactly_the_callback_shaped_fixtures(self):
        """Что именно перебирается ниже — и почему не все пятнадцать.

        Правится ГЕЙТ ПЕРСИСТЕНСА КОЛБЭКА, поэтому воспроизводятся фикстуры,
        чей вход — колбэк. Остальные пять на глобальном пути отвечает МОДЕЛЬ
        (свободный текст «борщ 300г» уходит в инструмент ``clarify_food_entry``,
        а не в ``matches()``) либо требуют вложения-фото, которого у события
        колбэка нет. Прогонять их здесь значило бы проверять заглушку
        консьержа, а не код — поэтому список зафиксирован явно, а не
        «случайно получился».
        """
        replayed = sorted(
            f.name
            for name in self.SETS
            for f in self._fixtures(name)
            if str(f.input.get("text", "")).startswith("cb:")
        )
        assert replayed == [
            "food_clarify_cb_diary_redirect",
            "food_clarify_cb_typo_ack",
            "food_correction_cb_grams",
            "food_correction_cb_macros",
            "food_correction_cb_name",
            "food_correction_distinct_prompts",
            "food_correction_unknown_field",
            "food_scanner_cb_clarify",
            "food_scanner_cb_reject",
            "food_scanner_cb_to_diary",
            "food_scanner_cb_unknown_id",
        ], replayed

    def test_every_callback_fixture_replays_and_leaves_no_raw_payload(
        self, sent, fake_redis, concierge
    ):
        from apps.replay.assertions import evaluate, evaluate_voice

        failures: list[str] = []
        replayed = 0
        uid = 92000
        for set_name in self.SETS:
            for fixture in self._fixtures(set_name):
                text = str(fixture.input.get("text", ""))
                if not text.startswith("cb:"):
                    continue
                uid += 1
                replayed += 1
                _, conversation = self._consented_user(uid)
                before = len(sent)
                max_handler.handle_global_max_event(
                    _tap(payload=text, user_id=uid, callback_id=f"fx-{uid}")
                )

                response = "\n".join(m["text"] for m in sent[before:])
                trace = {
                    "intent": "",
                    "skill_used": "",
                    "safety_decision": "allow",
                    "response_text": response,
                    "tool_calls": [],
                }
                must_pass = fixture.must_pass
                if fixture.name in self.CASE_MISMATCH:
                    # Та же проверка на тех же данных, только без регистра.
                    trace = {**trace, "response_text": response.lower()}
                    must_pass = _lowercased(must_pass)
                for problem in evaluate(trace, must_pass, fixture.forbidden):
                    failures.append(f"{set_name}/{fixture.name}: {problem}")
                for problem in evaluate_voice(response, fixture.voice_check):
                    failures.append(f"{set_name}/{fixture.name}: voice: {problem}")
                if not response:
                    failures.append(f"{set_name}/{fixture.name}: бот вообще не ответил")
                raw = _raw(_user_messages(conversation))
                if raw:
                    failures.append(f"{set_name}/{fixture.name}: сырой payload в истории: {raw}")

        # Положительная стража: перебор действительно что-то прогнал.
        assert replayed == 11, replayed
        assert not failures, failures
