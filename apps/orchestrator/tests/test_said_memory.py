"""Память сказанного, срез 1 — город и «когда удобно приходить» (бриф «Мозг», п.4).

Приёмка главного окна (14.09), три условия, и у каждого здесь свой тест:

1. смысл здоровья в ``MemoryEntry`` не пишется — ``TestHealthMeaningWritesNothing``;
2. новые факты стираются существующими путями («забудь всё», «забудь город»,
   удаление аккаунта) — ``TestErasure``;
3. новый читатель диалога внесён в ``DIALOGUE_READERS`` — ловит
   ``test_dialogue_reader_registry`` (строка ``said_memory:_person_named_cities``).

Приёмочный материал — реплики владельца 12.09: «я хочв расслабиться вечером и у
меня ноет спина», «1. Спина, 2. После работы».
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest
from django.utils import timezone

from apps.channels.max import handler as max_handler
from apps.conversations.services import record_global_message, resolve_active_global_conversation
from apps.identity.services.memory_key_policy import read_current_view
from apps.identity.services.privacy import delete_personal_data
from apps.llm.protocol import CompletionResult
from apps.orchestrator import concierge, said_memory
from apps.orchestrator.memory import short_term
from apps.orchestrator.open_question import close_question, open_question
from apps.orchestrator.tests.test_memory_erasure_matrix import (
    _bot_user,
    _consents,
    _forget_all,
)
from apps.orchestrator.tests.test_memory_erasure_matrix import (
    ayla as ayla,  # noqa: F811 — фикстура pytest
)
from apps.persona.memory_commands import handle_memory_command

pytestmark = pytest.mark.django_db(transaction=True)

OWNER_FIRST_TURN = (
    "ты что-нибудь можешь мне посоветовать или предложить? "
    "я хочв расслабиться вечером и у меня ноет спина"
)
OWNER_SCREENING_ANSWER = "1. Спина, 2. После работы"


@pytest.fixture(autouse=True)
def _known_cities(monkeypatch):
    monkeypatch.setattr("apps.marketplace.discovery._known_cities", lambda: ["Пенза", "Самара"])


_UID = iter(range(79101, 79999))


def _person(settings):
    bot_user = _bot_user(f"said-{next(_UID)}")
    bot_user.ayla_user_id_is_proxy = False
    bot_user.save(update_fields=["ayla_user_id_is_proxy"])
    _consents(bot_user, settings)
    return bot_user, resolve_active_global_conversation(bot_user)


def _said(bot_user) -> dict[str, str]:
    return {
        f.content["key"]: f.content["value"]
        for f in read_current_view(bot_user.ayla_user_id).green_facts
        if f.content.get("origin") == said_memory.ORIGIN_CONVERSATION
    }


def _all_values(bot_user) -> str:
    return " ".join(
        str(f.content) for f in read_current_view(bot_user.ayla_user_id).green_facts
    ).lower()


SHOW_MASTERS_PENZA = ({"tool": "show_masters", "arguments": {"city": "Пенза"}},)


# --------------------------------------------------------------------------- #
# Извлечение                                                                  #
# --------------------------------------------------------------------------- #
class TestVisitContextExtraction:
    def test_owner_first_turn_gives_evening_not_the_symptom(self):
        assert said_memory.visit_context_from_text(OWNER_FIRST_TURN) == "evening"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("хочу записаться после работы", "after_work"),
            ("хотела бы сходить в выходные", "weekend"),
            ("хочу после работы, я беременна", "after_work"),
            ("вечером болит спина, хочу массаж", None),
            ("у меня ноет спина вечером хочу массаж", None),
            ("вечером удобно", None),  # не желание — это к «мне удобно» M-B2
            ("хочу вечером или в выходные, как получится", None),  # два контекста
            ("", None),
        ],
    )
    def test_closed_vocabulary(self, text, expected):
        assert said_memory.visit_context_from_text(text) == expected


# --------------------------------------------------------------------------- #
# Условие 1: смысл здоровья не пишется                                        #
# --------------------------------------------------------------------------- #
class TestHealthMeaningWritesNothing:
    @pytest.mark.parametrize(
        "text",
        [
            "вечером ноет спина",
            "после работы тянет шею, хочу массаж",
            "хочу массаж, вечером болит поясница",
            "я беременна, хочу прийти вечером на массаж живота с болью",
            "после тренировки болит, хочу записаться",
        ],
    )
    def test_health_clause_gives_zero_rows(self, settings, text):
        """Смысл здоровья и время внутри той же клаузы — ни одной строки."""
        bot_user, conversation = _person(settings)

        written = said_memory.record_said_facts(bot_user, conversation, text)

        assert written == 0
        assert read_current_view(bot_user.ayla_user_id).green_facts == []

    @pytest.mark.parametrize(
        ("text", "value"),
        [
            ("я беременна и хочу массаж вечером", "evening"),
            ("принимаю лекарства, хочу прийти после работы", "after_work"),
        ],
    )
    def test_mixed_turn_stores_only_the_closed_value_never_the_health_word(
        self, settings, text, value
    ):
        """Клауза-желание без симптома даёт значение из закрытого словаря; слова
        из клаузы здоровья в хранилище не попадают ни в каком виде."""
        bot_user, conversation = _person(settings)

        assert said_memory.record_said_facts(bot_user, conversation, text) == 1

        assert _said(bot_user) == {"visit_context": value}
        stored = _all_values(bot_user)
        assert value in stored
        for word in ("беремен", "лекарств"):
            assert word not in stored

    def test_answer_to_screening_questions_is_never_read(self, settings):
        """«после работы» в ответе скрининга — когда болит, не когда удобно."""
        bot_user, conversation = _person(settings)
        open_question(conversation, "health_screening.soft", asked_text="Где болит? Когда?")
        assert close_question(conversation, OWNER_SCREENING_ANSWER) is not None

        written = said_memory.record_said_facts(bot_user, conversation, OWNER_SCREENING_ANSWER)

        assert written == 0
        # empty-assert-ok: ответ на вопросы скрининга не читается по построению — строк быть не должно
        assert _said(bot_user) == {}

    def test_owner_first_turn_stores_evening_and_nothing_about_the_back(self, settings):
        bot_user, conversation = _person(settings)

        assert said_memory.record_said_facts(bot_user, conversation, OWNER_FIRST_TURN) == 1

        assert _said(bot_user) == {"visit_context": "evening"}
        values = _all_values(bot_user)
        assert "evening" in values
        assert "спин" not in values and "ноет" not in values


# --------------------------------------------------------------------------- #
# Город: только названный самим человеком и только из тех, где есть мастера   #
# --------------------------------------------------------------------------- #
class TestCity:
    def test_city_the_person_named_and_the_model_searched_is_stored(self, settings):
        bot_user, conversation = _person(settings)
        record_global_message(conversation, role="user", content="В Пензе")

        written = said_memory.record_said_facts(
            bot_user, conversation, "массаж", tool_trace=SHOW_MASTERS_PENZA
        )

        assert written == 1
        assert _said(bot_user) == {"city": "Пенза"}
        fact = read_current_view(bot_user.ayla_user_id).green_facts[0]
        assert fact.source == "explicit"
        assert fact.content["said_at"]

    def test_city_only_the_bot_said_is_not_stored(self, settings):
        bot_user, conversation = _person(settings)
        record_global_message(conversation, role="user", content="массаж")
        record_global_message(conversation, role="assistant", content="Ищу в Пензе?")

        written = said_memory.record_said_facts(
            bot_user, conversation, "да", tool_trace=SHOW_MASTERS_PENZA
        )

        assert written == 0
        # empty-assert-ok: город назвал только ассистент — человеком не сказан, строк быть не должно
        assert _said(bot_user) == {}

    def test_city_without_a_search_is_not_stored(self, settings):
        bot_user, conversation = _person(settings)
        # Город назван человеком и распознан — но модель его не искала.
        assert said_memory._person_named_cities(conversation, "я из Пензы") == ["Пенза"]
        assert said_memory.record_said_facts(bot_user, conversation, "я из Пензы") == 0
        # empty-assert-ok: модель не искала мастеров — город не пишется по построению
        assert _said(bot_user) == {}

    def test_city_we_do_not_serve_is_not_stored(self, settings, monkeypatch):
        monkeypatch.setattr("apps.marketplace.discovery._known_cities", lambda: ["Самара"])
        bot_user, conversation = _person(settings)

        written = said_memory.record_said_facts(
            bot_user, conversation, "массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
        )

        assert written == 0
        # empty-assert-ok: Пенза выключена из набора городов с мастерами этим тестом — пусто по построению
        assert _said(bot_user) == {}

    def test_new_city_supersedes_the_old_one(self, settings):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(
            bot_user, conversation, "массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
        )
        said_memory.record_said_facts(
            bot_user,
            conversation,
            "а теперь в Самаре",
            tool_trace=({"tool": "show_masters", "arguments": {"city": "Самара"}},),
        )
        assert _said(bot_user) == {"city": "Самара"}

    def test_no_consent_no_memory(self, settings):
        bot_user = _bot_user(f"said-{next(_UID)}")
        conversation = resolve_active_global_conversation(bot_user)

        written = said_memory.record_said_facts(
            bot_user, conversation, "хочу в Пензе после работы", tool_trace=SHOW_MASTERS_PENZA
        )

        assert written == 0


# --------------------------------------------------------------------------- #
# Чтение: блок промпта                                                        #
# --------------------------------------------------------------------------- #
class TestSaidBlock:
    def test_block_names_fact_date_and_the_confirm_rule(self, settings):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(
            bot_user,
            conversation,
            "хочу массаж в Пензе после работы",
            tool_trace=SHOW_MASTERS_PENZA,
        )

        block = said_memory.render_said_block(bot_user)

        today = timezone.now().strftime("%d.%m")
        assert f"- город — Пенза ({today}) [key=city]" in block
        assert f"- когда удобно приходить — после работы ({today}) [key=visit_context]" in block
        # DRF-1878: подтверждение — инструментом, вопрос рисует бот, не модель.
        assert "confirm_said_fact" in block
        assert "Не спрашивай это заново" in block

    def test_block_is_empty_without_facts(self, settings):
        bot_user, _conversation = _person(settings)
        assert said_memory.render_said_block(bot_user) == ""

    def test_kill_switch_silences_the_block(self, settings):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(bot_user, conversation, OWNER_FIRST_TURN)
        assert said_memory.render_said_block(bot_user) != ""
        settings.CONCIERGE_MEMORY_ENABLED = False
        assert said_memory.render_said_block(bot_user) == ""


# --------------------------------------------------------------------------- #
# Условие 2: стирание существующими путями                                    #
# --------------------------------------------------------------------------- #
class _EmptyAylaExport:
    """Клиент Ayla для выгрузки: пустая половина Ayla, чтобы проверялась половина бота."""

    def get_personal_data_export(self, *, ayla_user_id: str, external_user_id: str) -> dict:
        return {}

    def close(self) -> None:
        return None


class TestErasure:
    def _with_both_facts(self, settings):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(
            bot_user,
            conversation,
            "хочу массаж в Пензе после работы",
            tool_trace=SHOW_MASTERS_PENZA,
        )
        assert _said(bot_user) == {"city": "Пенза", "visit_context": "after_work"}
        return bot_user, conversation

    def test_forget_all_erases_said_facts_and_the_block(self, settings, ayla):
        bot_user, _conversation = self._with_both_facts(settings)
        assert _said(bot_user) == {"city": "Пенза", "visit_context": "after_work"}

        _forget_all(bot_user)

        assert _said(bot_user) == {}
        assert said_memory.render_said_block(bot_user) == ""

    def test_forget_all_blocks_re_learning(self, settings, ayla):
        bot_user, conversation = self._with_both_facts(settings)
        assert said_memory.render_said_block(bot_user) != ""
        _forget_all(bot_user)

        written = said_memory.record_said_facts(bot_user, conversation, OWNER_FIRST_TURN)

        assert written == 0
        assert _said(bot_user) == {}

    def test_account_delete_erases_said_facts(self, settings, ayla):
        bot_user, _conversation = self._with_both_facts(settings)
        assert _said(bot_user) == {"city": "Пенза", "visit_context": "after_work"}

        delete_personal_data(bot_user, client=ayla)

        assert _said(bot_user) == {}

    def test_personal_data_export_carries_both_facts_under_memory(self, settings):
        """152-ФЗ ст. 14: выгрузка по запросу содержит сказанное — в разделе
        ``memory`` (``export_coverage.SECTIONS``), с происхождением и датой."""
        from apps.identity.services.privacy import export_personal_data

        bot_user, _conversation = self._with_both_facts(settings)

        # Пустая выгрузка Ayla — как `_NoAyla` у сторожа export_coverage: здесь
        # проверяется половина бота, раздел `memory`.
        payload = export_personal_data(bot_user, client=_EmptyAylaExport())

        rows = {
            row["content"].get("key"): row
            for row in payload["memory"]
            if isinstance(row.get("content"), dict)
        }
        assert rows["city"]["content"]["value"] == "Пенза"
        assert rows["visit_context"]["content"]["value"] == "after_work"
        for key in ("city", "visit_context"):
            assert rows[key]["content"]["origin"] == said_memory.ORIGIN_CONVERSATION
            assert rows[key]["content"]["said_at"]
            assert rows[key]["is_current"] is True

    def test_show_names_them_and_forget_city_removes_only_the_city(self, settings):
        bot_user, _conversation = self._with_both_facts(settings)

        shown = handle_memory_command(user_id=bot_user.ayla_user_id, text="покажи что знаешь")
        assert shown is not None
        assert "ищешь мастеров в городе Пенза" in shown.text
        assert "хочешь приходить после работы" in shown.text
        labels = [b["label"] for b in (shown.action_data or {}).get("buttons", [])]
        assert "Забыть: город" in labels

        forgot = handle_memory_command(user_id=bot_user.ayla_user_id, text="забудь город")
        assert forgot is not None
        assert _said(bot_user) == {"visit_context": "after_work"}


# --------------------------------------------------------------------------- #
# Провод: канал пишет после ответа, консьерж читает на следующем ходу         #
# --------------------------------------------------------------------------- #
class _CapturingModel:
    def __init__(self) -> None:
        self.systems: list[str] = []

    async def complete(self, messages, *, model, tools=None, **kwargs):
        self.systems.append("\n".join(m["content"] for m in messages if m.get("role") == "system"))
        return CompletionResult(
            text="Хорошо. Какой массаж тебе ближе — расслабляющий или спортивный?",
            tool_calls=[],
            prompt_tokens=20,
            completion_tokens=10,
            model="gpt-4o-mini",
            provider="openai",
            finish_reason="stop",
        )


def _msg(*, text: str, user_id: str, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": int(user_id.rsplit("-", 1)[-1]), "name": "Андрей"},
            "recipient": {"chat_id": 8899, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


class TestWiring:
    def test_channel_writes_after_reply_and_next_turn_prompt_reads_it(self, settings, monkeypatch):
        settings.GLOBAL_BOT_ONBOARDING = True
        monkeypatch.setattr(
            "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
        )
        monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
        monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)
        monkeypatch.setattr(max_handler, "send_message", lambda **kwargs: {"ok": True})
        from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

        monkeypatch.setattr(short_term, "_redis_client", lambda: _FakeRedis())
        model = _CapturingModel()
        router = Mock()
        router.get_provider.return_value = model
        monkeypatch.setattr(concierge, "get_router", lambda: router)

        uid = str(next(_UID))
        bot_user = _bot_user(uid)
        bot_user.ayla_user_id_is_proxy = False
        bot_user.welcomed_at = timezone.now()
        bot_user.save(update_fields=["ayla_user_id_is_proxy", "welcomed_at"])
        _consents(bot_user, settings)

        max_handler.handle_global_max_event(
            _msg(text="хочу прийти после работы", user_id=uid, mid="m1")
        )
        bot_user.refresh_from_db()
        assert _said(bot_user) == {"visit_context": "after_work"}
        # На первом ходу факта ещё не было — промпт был, блока в нём нет.
        first_turn = list(model.systems)
        assert first_turn and all("Сегодня:" in s for s in first_turn)
        assert all("когда удобно приходить" not in s for s in first_turn)

        model.systems.clear()
        max_handler.handle_global_max_event(_msg(text="привет", user_id=uid, mid="m2"))

        assert model.systems, "модель не вызывалась на втором ходу"
        assert "- когда удобно приходить — после работы" in model.systems[0]
