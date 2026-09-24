"""Один словарь диет на бот, и непонятое — не ответ (DRF-2392).

Причина одна, следствий три. Копий словаря типов питания в боте оказалось
ТРИ: у разбора ответов консьержу (`memory_ask`), у блока промпта
(`memory_block`) и у анкеты. Копии разошлись молча — у первой не было
`omnivore`, и «ем всё, без ограничений» ложилось как `other`, а `other` в
анкете означает «и я напишу словами», которых никто не спрашивал.

Второй корень — там же: разбор отвечал `other` на ВСЁ непонятое. Запись
«последний победил» затирала этой заглушкой точное значение, названное
человеком раньше, — знание о нём становилось хуже от того, что он открыл рот.
Непонятое теперь `_UNPARSED`, то есть общее для всех полей правило: открытый
вопрос снимается, ничего не пишется.

* f1 — «без ограничений» разбирается ответом; исключение («только мясо не
  ем») ответом НЕ становится: утверждать «ограничений нет» про человека,
  назвавшего ограничение, хуже, чем не понять его;
* f2 — значения разбора принадлежат словарю каталога, и словарь один: у
  блока промпта своей копии больше нет;
* f3 — ПОВЕДЕНИЕ ручки: непонятый ответ ничего не пишет, снимает открытый
  вопрос и не оставляет человека в ловушке следующих сообщений;
* f4 — «другое» словом — ответ человека, и он записывается.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.diet_types import CATALOG_DIET_TYPES, DIET_OMNIVORE, DIET_OTHER
from apps.orchestrator import memory_ask
from apps.orchestrator.memory_block import _DIET_TYPE_VOCAB


class TestF1NoRestrictionsIsAnAnswerButAnExclusionIsNot:
    @pytest.mark.parametrize(
        "text",
        ["ем всё", "без ограничений", "никаких ограничений", "я всеядная", "ограничений нет"],
    )
    def test_the_phrases_parse_to_omnivore(self, text: str) -> None:
        assert memory_ask._parse_diet(text) == DIET_OMNIVORE

    @pytest.mark.parametrize(
        "text",
        ["ем всё, только мясо не ем", "ем всё кроме свинины", "ем всё, но без мяса"],
    )
    def test_an_exclusion_is_not_no_restrictions(self, text: str) -> None:
        """Тот же пример каноничен в `memory_extract`: исключения — не типы
        питания. Утверждать «ограничений нет» про назвавшего ограничение —
        ложный факт о человеке, хуже непонимания."""
        assert memory_ask._parse_diet(text) is memory_ask._UNPARSED

    def test_an_unknown_phrase_is_not_an_answer(self) -> None:
        assert memory_ask._parse_diet("стараюсь есть меньше мяса") is memory_ask._UNPARSED

    def test_a_named_diet_still_parses(self) -> None:
        assert memory_ask._parse_diet("я веган") == "vegan"


class TestF2TheVocabularyIsOne:
    def test_every_parsed_value_belongs_to_the_catalog_vocabulary(self) -> None:
        parsed = {value for value, _pattern in memory_ask._DIET_WORDS}

        assert parsed  # наличие: разбор что-то знает
        assert parsed <= set(CATALOG_DIET_TYPES)

    def test_the_prompt_block_has_no_copy_of_its_own(self) -> None:
        assert _DIET_TYPE_VOCAB == frozenset(CATALOG_DIET_TYPES)

    def test_the_phrases_shown_to_the_person_cover_the_whole_vocabulary(self) -> None:
        """Фразы для показа — не значения, но ПО значениям: пропусти одно, и
        строка про человека молча не покажется. Узел держит покрытие, а сами
        слова остаются на своём месте (их утверждает владелец)."""
        from apps.persona.memory_surface import _DECLARED_DIET_PHRASES

        assert set(_DECLARED_DIET_PHRASES) == set(CATALOG_DIET_TYPES)


class _Conversation:
    id = "conv-2392"


def _answer(text: str):
    """Ответ на открытый вопрос о диете — через саму ручку, не через предикат."""
    patch_mock = Mock(return_value=SimpleNamespace(status=memory_ask.GateStatus.OK))
    with (
        patch.object(memory_ask, "concierge_memory_enabled", return_value=True),
        patch.object(memory_ask, "read_pending", return_value={"field": "diet_type"}),
        patch.object(memory_ask, "_clear_pending") as clear,
        patch.object(memory_ask, "patch_declared_prefs", patch_mock),
    ):
        reply = memory_ask.try_handle_answer(_Conversation(), object(), text)
    return reply, patch_mock, clear


class TestF3AnUnparsedAnswerWritesNothingAndClosesTheQuestion:
    def test_nothing_is_written_and_the_pending_question_is_cleared(self) -> None:
        """Оставь вопрос открытым — и он забирал бы КАЖДОЕ следующее сообщение
        сутки (столько живёт отметка), а любое слово «другое» в нём записало бы
        заглушку поверх точного ответа. Ловушка была бы хуже той одной записи,
        против которой её ставили."""
        reply, patch_mock, clear = _answer("стараюсь есть меньше мяса")

        assert reply is None
        patch_mock.assert_not_called()
        clear.assert_called_once()

    def test_a_named_answer_is_written(self) -> None:
        reply, patch_mock, clear = _answer("я веган")

        assert reply is not None  # наличие: человеку ответили
        patch_mock.assert_called_once()
        assert patch_mock.call_args[0][1][0]["value"] == "vegan"
        clear.assert_called_once()


class TestF4OtherSaidInWordsIsAnAnswer:
    def test_the_person_naming_other_is_written(self) -> None:
        reply, patch_mock, _clear = _answer("другое, у меня своя диета")

        assert reply is not None
        patch_mock.assert_called_once()
        assert patch_mock.call_args[0][1][0]["value"] == DIET_OTHER
