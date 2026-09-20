"""DRF-2074 — тап «согласен» на экране согласия анкеты доезжает до навыка, не в «не поняла».

Владелец в MAX: ``/anketa`` → экран согласия → тап «согласен» → «Я пока не
поняла». Причина одна и адресная: :func:`is_structured_nutrition_turn`
(``apps/orchestrator/nutrition_global.py``) считает структурными только
``cb:anketa:`` / ``cb:food:``; семейство ``cb:pc_consent:*`` экрана согласия
(#1664) в перечень не попало, поэтому ход не доходит до
``try_handle_structured_nutrition_turn``, а ``looks_like_callback_payload``
отправляет его в ветку «не поняла» (``handler.py``, DRF-1491). Навык при этом
ловит ровно эти строки (``NutritionAnketaSkill.matches``) — просто ход к нему
не приносят.

Три вещи здесь, и порядок важен:

1. **Сторож класса**, а не одного слуга. Перечень структурных префиксов —
   список исключений, и он разъезжается ровно так, как разъехался в этот
   раз: новая клавиатура — новое семейство — забытый префикс. Поэтому
   перепись ``cb:``-констант клавиатур навыков питания снимается **с самих
   модулей** (``vars(module)``), не grep'ом по исходнику и не списком в тесте,
   и каждая обязана быть структурной. На ``dev b3958d3e`` сторож красен пятью
   строками — ``cb:pc_consent:*``.
2. **Через ``handler`` в golden-стиле** — тот же путь, что прошёл владелец:
   welcomed-пользователь, ``/anketa``, тап ``cb:pc_consent:grant``. Красное до
   правки: ответ начинается с ``FALLBACK_INTRO`` («Я пока не поняла»).
3. **Выключатель**: при ``NUTRITION_ENABLED=false`` тапы согласия
   (``grant``/``decline``) отвечают буквальной заглушкой
   ``NUTRITION_UNAVAILABLE_TEXT`` — через диспетчер, не напрямую навыком (1994
   уже доказал навык; здесь предмет — маршрут). Тапы ОТЗЫВА (``withdraw*``)
   с DRF-2135 стоят выше ворот и при OFF доезжают до навыка тем же маршрутом
   и отвечают как при ON (§92: отзыв согласия работает при любом флаге);
   что именно они отвечают — ``test_withdraw_outside_switch_2135``.

Каждое отрицание стоит рядом с положительной стражей на тех же данных:
перепись непуста и содержит семейство согласия; при включённом флаге тот же
тап даёт не заглушку и записывает согласие ``personal_calculation``.

Чего файл НЕ доказывает: персистенс тапа в историю диалога — другой читатель
(DRF-990: гейт в ``handler.py`` переводит ``cb:anketa:*``/``cb:food:*`` во
фразы, ``cb:pc_consent:*`` не переводит). Названо как предел, не спрятано.
"""

from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from apps.orchestrator import nutrition_global
from apps.orchestrator.nutrition_global import (
    is_structured_nutrition_turn,
    try_handle_structured_nutrition_turn,
)
from apps.skills.base import SkillResult
from apps.skills.food_clarify import skill as food_clarify_skill
from apps.skills.food_clarify import text_entry as food_clarify_text_entry
from apps.skills.food_correction import skill as food_correction_skill
from apps.skills.food_scanner import skill as food_scanner_skill
from apps.skills.menu.marketplace import FALLBACK_INTRO, NUTRITION_UNAVAILABLE_TEXT
from apps.skills.nutrition_anketa import skill as nutrition_anketa_skill
from apps.skills.nutrition_anketa.skill import (
    CONSENT_DECLINE_CALLBACK,
    CONSENT_GRANT_CALLBACK,
    WITHDRAW_CALLBACK,
    WITHDRAW_CONFIRM_CALLBACK,
    WITHDRAW_KEEP_CALLBACK,
    NutritionAnketaSkill,
)
from apps.skills.water import skill as water_skill

STUB = NUTRITION_UNAVAILABLE_TEXT

#: Под воротами выключателя: дать согласие в выключенный контур нельзя.
CONSENT_GATED: tuple[str, ...] = (
    CONSENT_GRANT_CALLBACK,
    CONSENT_DECLINE_CALLBACK,
)

#: Выше ворот (DRF-2135): отзыв согласия ПДн работает при любом флаге.
CONSENT_OPEN: tuple[str, ...] = (
    WITHDRAW_CALLBACK,
    WITHDRAW_CONFIRM_CALLBACK,
    WITHDRAW_KEEP_CALLBACK,
)

#: Что именно навык делает по каждому тапу отзыва — «дошёл» и «сделал что
#: просили» различимы.
WITHDRAW_REPLY_KIND: dict[str, str] = {
    WITHDRAW_CALLBACK: "anketa_withdraw_ask",
    WITHDRAW_CONFIRM_CALLBACK: "anketa_withdraw_done",
    WITHDRAW_KEEP_CALLBACK: "anketa_withdraw_kept",
}

CONSENT_FAMILY: tuple[str, ...] = CONSENT_GATED + CONSENT_OPEN


# ---------------------------------------------------------------------------
# 1. Сторож класса — перепись констант клавиатур с самих модулей
# ---------------------------------------------------------------------------

#: Модули навыков питания, чьи клавиатуры доезжают до
#: ``try_handle_structured_nutrition_turn``. Новый навык питания добавляется
#: сюда — и его константы попадают под сторож даром.
NUTRITION_SKILL_MODULES: tuple[types.ModuleType, ...] = (
    nutrition_anketa_skill,
    food_clarify_skill,
    food_clarify_text_entry,
    food_scanner_skill,
    food_correction_skill,
    water_skill,
)

#: Семейства с хвостом (``{step}`` / ``{scan_id}``) константой не выражаются —
#: их форму задают докстринги навыков; здесь по одному представителю на форму.
DYNAMIC_SAMPLES: tuple[str, ...] = (
    "cb:anketa:choice:gender:female",
    "cb:anketa:edit:weight",
    "cb:food:to_diary:scan-1",
    "cb:food:clarify:scan-1",
    "cb:food:reject:scan-1",
    "cb:food:correct:grams:scan-1",
)

#: Литералы, которые модули пишут В КОДЕ, а не константой (``matches`` и
#: клавиатуры): перепись по ``vars(module)`` их не видит — это предел
#: инструмента, а не отсутствие кнопок. Перечислены руками; при переводе в
#: константы перепись подхватит их сама, а строка отсюда станет лишней.
INLINE_LITERALS: tuple[str, ...] = (
    "cb:anketa:start",
    "cb:food:diary",
    "cb:food:typo",
)


def _callback_constants(module: types.ModuleType) -> set[str]:
    """Все ``cb:``-строки уровня модуля: голые, в кортежах/множествах, ключи словарей."""

    found: set[str] = set()
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        if isinstance(value, str) and value.startswith("cb:"):
            found.add(value)
        elif isinstance(value, (tuple, list, frozenset, set)):
            found.update(v for v in value if isinstance(v, str) and v.startswith("cb:"))
        elif isinstance(value, dict):
            found.update(k for k in value if isinstance(k, str) and k.startswith("cb:"))
    return found


def _keyboard_census() -> tuple[str, ...]:
    census: set[str] = set()
    for module in NUTRITION_SKILL_MODULES:
        census.update(_callback_constants(module))
    return tuple(sorted(census | set(DYNAMIC_SAMPLES) | set(INLINE_LITERALS)))


KEYBOARD_CENSUS = _keyboard_census()


def _bare_conversation() -> SimpleNamespace:
    return SimpleNamespace(id=1, skill_state={})


class TestEveryNutritionKeyboardCallbackIsStructured:
    def test_the_census_is_not_empty_and_names_the_consent_family(self) -> None:
        """Присутствие впереди утверждения «каждый»: пустая перепись зеленела бы
        на любом предикате."""

        assert len(KEYBOARD_CENSUS) >= 18, KEYBOARD_CENSUS
        assert set(CONSENT_FAMILY) <= set(KEYBOARD_CENSUS)
        assert "cb:food:text_log" in KEYBOARD_CENSUS  # food_clarify.text_entry снят

    @pytest.mark.parametrize("callback", KEYBOARD_CENSUS)
    def test_every_keyboard_callback_is_structured(self, callback: str) -> None:
        assert is_structured_nutrition_turn(
            text=callback, has_attachments=False, conversation=_bare_conversation()
        ), f"{callback} не структурный — уедет в «не поняла» или к модели"

    def test_free_text_is_still_not_structured(self) -> None:
        """Обратная стража: предикат не стал «всё структурно»."""

        # Присутствие на тех же данных: тот же предикат на настоящем тапе — True.
        assert is_structured_nutrition_turn(
            text=CONSENT_GRANT_CALLBACK, has_attachments=False, conversation=_bare_conversation()
        )
        for text in ("привет", "что мне есть", "cb-не-колбэк", "consent grant"):
            assert not is_structured_nutrition_turn(
                text=text, has_attachments=False, conversation=_bare_conversation()
            )


# ---------------------------------------------------------------------------
# 2. Диспетчер — навык получает ход; при выключенном флаге — заглушка
# ---------------------------------------------------------------------------


@pytest.fixture
def nutrition_on(settings):  # noqa: ANN001, ANN201
    settings.NUTRITION_ENABLED = True
    return settings


@pytest.fixture
def nutrition_off(settings):  # noqa: ANN001, ANN201
    settings.NUTRITION_ENABLED = False
    return settings


@pytest.mark.django_db
class TestDispatcherHandsConsentTapsToTheAnketaSkill:
    """``_run_skill`` входит в ``tenant_scope(get_global_bot_tenant())`` — база нужна."""

    @pytest.mark.parametrize("callback", CONSENT_FAMILY)
    def test_on_the_tap_reaches_the_skill_untouched(
        self, nutrition_on, monkeypatch, callback: str
    ) -> None:
        seen: list[str] = []

        def spy(self, context):  # noqa: ANN001, ANN202
            seen.append(context.message_text)
            return SkillResult(reply_text="навык ответил", action_type="anketa_consent")

        monkeypatch.setattr(NutritionAnketaSkill, "handle", spy)

        result = try_handle_structured_nutrition_turn(
            text=callback,
            attachments=None,
            bot_user=Mock(),
            conversation=_bare_conversation(),
            trace_id="t-2074",
        )

        assert result is not None, f"{callback}: диспетчер не отдал ход навыку"
        assert result.reply_text == "навык ответил"
        assert seen == [callback]  # payload дошёл нетронутым

    @pytest.mark.parametrize("callback", CONSENT_GATED)
    def test_off_the_tap_answers_the_literal_stub_through_the_dispatcher(
        self, nutrition_off, callback: str
    ) -> None:
        result = try_handle_structured_nutrition_turn(
            text=callback,
            attachments=None,
            bot_user=Mock(),
            conversation=_bare_conversation(),
            trace_id="t-2074",
        )

        assert result is not None, f"{callback}: при выключенном флаге ход ушёл мимо навыка"
        assert result.reply_text == STUB
        assert result.meta["reply_kind"] == "nutrition_anketa_nutrition_off"

    @pytest.mark.parametrize("callback", CONSENT_OPEN)
    def test_off_the_withdrawal_tap_still_reaches_the_skill_and_is_not_the_stub(
        self, nutrition_off, monkeypatch, callback: str
    ) -> None:
        """DRF-2135 — маршрут при OFF тот же, что при ON: диспетчер отдаёт ход
        навыку, навык отвечает НЕ заглушкой. Само поведение отзыва (согласие
        снято, параметры удалены) — предмет ``test_withdraw_outside_switch_2135``;
        здесь предмет — маршрут, поэтому запись согласия и каталог подменены."""
        monkeypatch.setattr("apps.consent.personal_calculation.is_granted", lambda bot_user: True)
        monkeypatch.setattr("apps.consent.personal_calculation.withdraw", lambda bot_user: 1)

        class _Catalog:
            async def purge_body_parameters(self, *, external_user_id):  # noqa: ANN001, ANN202
                return True

        monkeypatch.setattr(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client", lambda: _Catalog()
        )

        result = try_handle_structured_nutrition_turn(
            text=callback,
            attachments=None,
            bot_user=Mock(),
            conversation=_bare_conversation(),
            trace_id="t-2074",
        )

        assert result is not None, f"{callback}: при выключенном флаге ход ушёл мимо навыка"
        assert result.reply_text != STUB
        assert result.meta["reply_kind"] == WITHDRAW_REPLY_KIND[callback]

    def test_off_the_withdrawal_phrase_reaches_the_skill_only_with_an_anketa_in_flight(
        self, nutrition_off, monkeypatch
    ) -> None:
        """Предел DRF-2135, закреплённый с обеих сторон: текстовая форма отзыва —
        не структурный payload. При активной анкете ход структурный и доезжает
        до навыка (``anketa_withdraw_ask``); без анкеты в полёте диспетчер его
        не забирает (``None`` — ход уходит консьержу). Изменится любая сторона
        — красный здесь, а не тихая перемена маршрута."""
        from apps.skills.nutrition_anketa.skill import WITHDRAW_ACTION_TEXT

        monkeypatch.setattr("apps.consent.personal_calculation.is_granted", lambda bot_user: True)

        in_flight = SimpleNamespace(
            id=1, skill_state={"nutrition_anketa": {"current_step": "weight"}}
        )
        result = try_handle_structured_nutrition_turn(
            text=WITHDRAW_ACTION_TEXT,
            attachments=None,
            bot_user=Mock(),
            conversation=in_flight,
            trace_id="t-2135",
        )
        assert result is not None
        assert result.meta["reply_kind"] == "anketa_withdraw_ask"

        bare = try_handle_structured_nutrition_turn(
            text=WITHDRAW_ACTION_TEXT,
            attachments=None,
            bot_user=Mock(),
            conversation=_bare_conversation(),
            trace_id="t-2135",
        )
        assert bare is None


# ---------------------------------------------------------------------------
# 3. Через handler — путь владельца, golden-стиль
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestOwnerPathThroughTheHandler:
    @pytest.fixture(autouse=True)
    def _onboarding_on(self, settings):  # noqa: ANN001, ANN202
        settings.GLOBAL_BOT_ONBOARDING = True

    @pytest.fixture(autouse=True)
    def _no_chat_actions(self, monkeypatch):  # noqa: ANN001, ANN202
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
        )

    @pytest.fixture
    def sent(self, monkeypatch):  # noqa: ANN001, ANN202
        from apps.channels.max import handler as max_handler

        calls: list[dict] = []

        def fake_send(*, chat_id, text, attachments=None, timeout=10.0):  # noqa: ANN001, ANN202
            calls.append({"chat_id": chat_id, "text": text, "attachments": attachments})
            return {"ok": True}

        monkeypatch.setattr(max_handler, "send_message", fake_send)
        return calls

    @pytest.fixture
    def fake_redis(self, monkeypatch):  # noqa: ANN001, ANN202
        from apps.orchestrator.memory import short_term
        from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

        fake = _FakeRedis()
        monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
        return fake

    @pytest.fixture
    def model(self, monkeypatch):  # noqa: ANN001, ANN202
        from apps.orchestrator.discovery import DiscoveryReply

        spy = MagicMock(return_value=DiscoveryReply(text="модель ответила", persisted=False))
        monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
        return spy

    @staticmethod
    def _welcomed_user(user_id: int):  # noqa: ANN205
        from django.utils import timezone

        from apps.consent.services import record_global_consent
        from apps.identity.services.resolver import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=str(user_id), chat_id="8899"
        )
        bot_user.welcomed_at = timezone.now()
        bot_user.save(update_fields=["welcomed_at"])
        record_global_consent(
            bot_user,
            consent_type="personal_data",
            source="test:drf2074",
            document_version="welcome-s2-v1",
        )
        return bot_user

    @staticmethod
    def _msg(*, text: str, user_id: int, mid: str) -> dict:
        return {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Ирина"},
                "recipient": {"chat_id": 8899, "chat_type": "dialog"},
                "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
            },
        }

    @staticmethod
    def _tap(*, payload: str, user_id: int, callback_id: str) -> dict:
        return {
            "update_type": "message_callback",
            "timestamp": 1731320000000,
            "callback": {
                "callback_id": callback_id,
                "user": {"user_id": user_id, "name": "Ирина"},
                "payload": payload,
            },
            "message": {"recipient": {"chat_id": 8899, "chat_type": "dialog"}},
        }

    @staticmethod
    def _offered_callbacks(sent_call: dict) -> set[str]:
        """Все callback'и кнопок в отправленном сообщении — как их видит MAX."""

        found: set[str] = set()
        for attachment in sent_call.get("attachments") or []:
            payload = attachment.get("payload") if isinstance(attachment, dict) else None
            for row in (payload or {}).get("buttons", []):
                for button in row:
                    if isinstance(button, dict) and button.get("payload"):
                        found.add(str(button["payload"]))
        return found

    def test_owner_scenario_grant_tap_reaches_the_anketa_not_the_fallback(
        self, nutrition_on, sent, fake_redis, model
    ) -> None:
        from apps.channels.max import handler as max_handler
        from apps.consent.models import ConsentRecord

        bot_user = self._welcomed_user(20740)

        max_handler.handle_global_max_event(self._msg(text="/anketa", user_id=20740, mid="a-1"))
        # Положительная стража на входе: экран согласия реально предложил кнопку.
        assert sent, "на /anketa ничего не ушло"
        assert CONSENT_GRANT_CALLBACK in self._offered_callbacks(sent[-1]), sent[-1]

        max_handler.handle_global_max_event(
            self._tap(payload=CONSENT_GRANT_CALLBACK, user_id=20740, callback_id="a-2")
        )

        reply = sent[-1]["text"]
        assert not reply.startswith(FALLBACK_INTRO), reply
        assert reply != STUB
        assert model.call_count == 0
        # Данные: согласие записано — навык действительно выполнял ход.
        assert ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=ConsentRecord.ConsentType.PERSONAL_CALCULATION.value,
            granted=True,
            withdrawn_at=None,
        ).exists()

    def test_decline_tap_is_also_the_skill_s_answer_not_the_fallback(
        self, nutrition_on, sent, fake_redis, model
    ) -> None:
        from apps.channels.max import handler as max_handler

        self._welcomed_user(20741)
        max_handler.handle_global_max_event(self._msg(text="/anketa", user_id=20741, mid="b-1"))
        assert CONSENT_DECLINE_CALLBACK in self._offered_callbacks(sent[-1]), sent[-1]

        max_handler.handle_global_max_event(
            self._tap(payload=CONSENT_DECLINE_CALLBACK, user_id=20741, callback_id="b-2")
        )

        reply = sent[-1]["text"]
        assert not reply.startswith(FALLBACK_INTRO), reply
        assert model.call_count == 0

    def test_off_the_grant_tap_answers_the_stub_not_the_fallback(
        self, nutrition_off, sent, fake_redis, model
    ) -> None:
        """Выключатель закрывает и этот вход: заглушка, не «не поняла», не модель."""
        from apps.channels.max import handler as max_handler

        self._welcomed_user(20742)
        max_handler.handle_global_max_event(
            self._tap(payload=CONSENT_GRANT_CALLBACK, user_id=20742, callback_id="c-1")
        )

        assert sent
        assert sent[-1]["text"] == STUB
        assert model.call_count == 0


def test_the_prefix_list_is_the_one_the_predicate_reads() -> None:
    """Сторож на сам сторож: константа, которую правит эта задача, — та, что
    читает предикат. Иначе правка списка рядом ничего бы не изменила."""

    assert "cb:pc_consent:" in nutrition_global._STRUCTURED_CALLBACK_PREFIXES  # noqa: SLF001
    assert is_structured_nutrition_turn(
        text="cb:pc_consent:anything", has_attachments=False, conversation=_bare_conversation()
    )
