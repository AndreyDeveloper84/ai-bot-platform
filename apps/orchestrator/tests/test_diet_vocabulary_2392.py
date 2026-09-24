"""Один словарь диет на бот, и заглушка не затирает названное (DRF-2392).

Причина листа одна, следствий было три. Копий словаря типов питания в боте
оказалось **три**: у разбора ответов консьержа (`memory_ask`), у блока
промпта (`memory_block`) и у анкеты. Копии разошлись молча — у первой не было
`omnivore`, и «ем всё, без ограничений» ложилось как `other`, а `other` в
анкете означает «и я напишу словами», которых никто не спрашивал.

* f1 — «без ограничений» разбирается как ответ, а не как «другое»;
* f2 — значения разбора принадлежат словарю каталога, и словарь ровно один:
  у блока промпта своей копии больше нет;
* f3 — заглушка `other` НЕ затирает точное названное значение: запись «последний
  победил» иначе делала бы знание о человеке хуже от того, что он что-то
  сказал;
* f4 — «другое» СЛОВОМ по-прежнему записывается: это ответ человека, а не
  заглушка разбора.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from apps.integrations.ayla.diet_types import CATALOG_DIET_TYPES, DIET_OMNIVORE, DIET_OTHER
from apps.orchestrator import memory_ask
from apps.orchestrator.memory_block import _DIET_TYPE_VOCAB


class TestF1NoRestrictionsIsAnAnswer:
    def test_the_phrases_parse_to_omnivore(self) -> None:
        for text in ("ем всё", "без ограничений", "да нет ограничений никаких"):
            assert memory_ask._parse_diet(text) == DIET_OMNIVORE, text

    def test_an_unknown_phrase_still_falls_back(self) -> None:
        """Пара к предыдущему: разбор не стал отвечать `omnivore` на всё."""
        assert memory_ask._parse_diet("стараюсь есть меньше мяса") == DIET_OTHER


class TestF2TheVocabularyIsOne:
    def test_every_parsed_value_belongs_to_the_catalog_vocabulary(self) -> None:
        parsed = {value for value, _pattern in memory_ask._DIET_WORDS}

        assert parsed  # наличие: разбор что-то знает
        assert parsed <= set(CATALOG_DIET_TYPES)

    def test_the_prompt_block_has_no_copy_of_its_own(self) -> None:
        assert _DIET_TYPE_VOCAB == frozenset(CATALOG_DIET_TYPES)


class _Declared:
    def __init__(self, stored: str | None) -> None:
        self.status = memory_ask.GateStatus.OK
        self.context = SimpleNamespace(context={"diet_type": stored} if stored else {})


class TestF3TheFallbackDoesNotBlurANamedAnswer:
    def test_a_precise_stored_value_is_not_overwritten_by_other(self) -> None:
        """Человек ответил «Веганство» в анкете, а потом сказал консьержу
        что-то, чего разбор не понял. Записать сюда `other` значило бы сделать
        знание о человеке ХУЖЕ от того, что он открыл рот."""
        with patch.object(memory_ask, "get_declared_prefs", return_value=_Declared("vegan")):
            blurs = memory_ask._would_blur_a_named_answer(
                object(), "diet_type", DIET_OTHER, "стараюсь есть меньше мяса"
            )

        assert blurs is True

    def test_nothing_stored_means_the_answer_is_written(self) -> None:
        with patch.object(memory_ask, "get_declared_prefs", return_value=_Declared(None)):
            blurs = memory_ask._would_blur_a_named_answer(
                object(), "diet_type", DIET_OTHER, "стараюсь есть меньше мяса"
            )

        assert blurs is False

    def test_a_precise_answer_is_always_written(self) -> None:
        with patch.object(memory_ask, "get_declared_prefs", return_value=_Declared("vegan")):
            blurs = memory_ask._would_blur_a_named_answer(
                object(), "diet_type", DIET_OMNIVORE, "теперь ем всё"
            )

        assert blurs is False

    def test_a_read_failure_keeps_the_previous_behaviour(self) -> None:
        """Терять ответ человека из-за недоступной ручки нельзя."""

        def _boom(_bot_user):
            raise RuntimeError("ayla down")

        with patch.object(memory_ask, "get_declared_prefs", _boom):
            blurs = memory_ask._would_blur_a_named_answer(
                object(), "diet_type", DIET_OTHER, "что-то своё"
            )

        assert blurs is False

    def test_another_field_is_never_touched_by_this_rule(self) -> None:
        blurs = memory_ask._would_blur_a_named_answer(
            object(), "price_range_max", DIET_OTHER, "как получится"
        )

        assert blurs is False


class TestF4OtherSaidInWordsIsAnAnswer:
    def test_the_person_naming_other_is_written(self) -> None:
        """«Другое» словом — ответ человека, а не заглушка разбора."""
        with patch.object(memory_ask, "get_declared_prefs", return_value=_Declared("vegan")):
            blurs = memory_ask._would_blur_a_named_answer(
                object(), "diet_type", DIET_OTHER, "другое, у меня своя диета"
            )

        assert blurs is False
