"""Остальные копии словаря диет сведены — по механизму на узел (DRF-2398).

Копий было больше, чем сведено в DRF-2392, и это РАЗНЫЕ механизмы с разной
ценой ошибки:

* **запись на провод** (`ayla_bridge`) — проверки по словарю не было вовсе:
  контракт провода был описан словами и не исполнялся ни в одном месте, а это
  единственная точка, где значение его пересекает;
* **разбор естественной речи** (`memory_extract`) — «без ограничений» тут не
  было, и «я теперь ем всё» терялось, хотя сказано прямо;
* **фразы показа** (`memory_surface`) — без фразы строка молча не покажется:
  человек не увидит, что про него помнят, и не сможет это забыть;
* **слова забывания** (`memory_commands`) — без них «забудь, что я ем всё»
  стирало ВСЮ тему питания вместо одной строки.

Граница листа: разбор речи НЕ расширяется за пределы словаря, и «ем всё,
только мясо не ем» обязано остаться неразобранным — это канонический пример
самого `memory_extract`.

* g1 — провод: значение вне словаря не уходит; значение из словаря уходит;
  пустая строка (отказ от ограничений) уходит по-прежнему;
* g2 — речь: «я теперь ем всё» разбирается; исключение в любой части фразы
  отменяет «без ограничений», а само роняется громко, как раньше;
* g3 — показ: у каждого значения словаря есть фраза, и оба банка говорят
  одними словами;
* g4 — забывание: «ем всё» попадает в конкретный факт, а не в тему целиком.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla.diet_types import CATALOG_DIET_TYPES, DIET_OMNIVORE, DIET_OTHER
from apps.persona.memory_extract import extract_user_facts
from apps.persona.memory_surface import _DECLARED_DIET_PHRASES, _DIET_PHRASES


def _diet_candidate(diet_type: str | None, value: str = "x"):
    from apps.persona.memory_extract import GreenFactCandidate

    return GreenFactCandidate(
        kind="lifestyle", content={"key": "diet", "value": value, "diet_type": diet_type}
    )


def _bridge(candidate) -> list[dict]:
    """Что уехало бы на провод для этого кандидата."""
    from apps.orchestrator.memory import ayla_bridge

    sent: list[dict] = []

    def _patch(_bot_user, updates, client=None):
        sent.extend(updates)
        return Mock(status=ayla_bridge.GateStatus.OK)

    with patch.object(ayla_bridge, "patch_declared_prefs", _patch):
        ayla_bridge.bridge_candidates_to_ayla(Mock(), [candidate], client=Mock())
    return sent


class TestG1TheWireRefusesWhatTheVocabularyDoesNotKnow:
    def test_a_value_from_the_vocabulary_travels(self) -> None:
        sent = _bridge(_diet_candidate(DIET_OMNIVORE))

        assert [u["value"] for u in sent] == [DIET_OMNIVORE]

    def test_a_value_outside_the_vocabulary_does_not(self) -> None:
        """Каталог такое значение примет — у столбца нет `choices`, там лежат
        прежние строки живых людей. В память о человеке легла бы строка,
        которой словарь не знает: ни показать, ни забыть по имени."""
        allowed = _bridge(_diet_candidate(DIET_OMNIVORE))
        assert [u["value"] for u in allowed] == [DIET_OMNIVORE]  # наличие: провод работает

        sent = _bridge(_diet_candidate("палео"))

        # Пусто — и есть предмет узла: на провод не уходит НИЧЕГО. Наличие
        # утверждено выше на том же проводе с допустимым значением.
        assert sent == []  # empty-assert-ok: провод обязан промолчать целиком

    def test_the_retraction_still_clears_the_field(self) -> None:
        """«Я теперь снова ем мясо»: пустая строка — «не знаем», это не
        значение словаря, и проверка не должна её ронять."""
        sent = _bridge(_diet_candidate(None, value="none"))

        assert [u["value"] for u in sent] == [""]


class TestG2SpeechNamesNoRestrictionsButNotAnExclusion:
    @pytest.mark.parametrize(
        "text", ["я теперь ем всё", "я ем все", "я без ограничений", "я всеядная"]
    )
    def test_naming_no_restrictions_is_extracted(self, text: str) -> None:
        result = extract_user_facts(text)
        diets = [c.content.get("diet_type") for c in result.candidates]

        assert DIET_OMNIVORE in diets

    @pytest.mark.parametrize(
        "text",
        [
            # Каноническая запись примера самого модуля — с ОДНИМ «я». Прежний
            # узел брал вариант с двумя «я»: он утверждал границу, которой при
            # узкой оговорке не было.
            "я ем всё, только мясо не ем",
            "я ем всё, кроме мяса",
            "я ем всё кроме мяса",
            "я ем всё, но без мяса",
            # Часть про непереносимость вырезается как чувствительная — и
            # именно поэтому оговорка по ОЧИЩЕННЫМ частям не срабатывала.
            "я ем всё, но у меня непереносимость лактозы",
        ],
    )
    def test_no_restrictions_is_not_claimed_over_an_exclusion(self, text: str) -> None:
        """Ложный факт о человеке хуже непонимания — и доезжает до подсказки
        промпта словами «помню, что ты ешь всё»."""
        plain = extract_user_facts("я ем всё")
        assert DIET_OMNIVORE in [  # наличие: без оговорки фраза разбирается
            c.content.get("diet_type") for c in plain.candidates
        ]

        result = extract_user_facts(text)

        assert DIET_OMNIVORE not in [c.content.get("diet_type") for c in result.candidates]

    def test_the_veto_does_not_eat_other_facts_in_the_same_clause(self) -> None:
        """Решение о питании не вправе распоряжаться чужими фактами: прерванный
        виток уносил и удобное время, названное в той же части фразы. Это была
        бы чистая потеря — такой факт собирался и ДО правки."""
        result = extract_user_facts("я ем всё и мне удобно после шести, но мясо я не ем")

        assert "preferred_time_slots" in [c.content.get("key") for c in result.candidates]

    def test_a_named_absence_of_exclusions_is_still_an_answer(self) -> None:
        """«Ничего не исключаю» — отсутствие исключений, сказанное словами
        исключения: терять такой ответ нельзя."""
        result = extract_user_facts("я ем всё, ничего не исключаю")

        assert DIET_OMNIVORE in [c.content.get("diet_type") for c in result.candidates]

    def test_an_exclusion_in_any_clause_cancels_it(self) -> None:
        """Фраза распадается на части: из первой вышло бы «без ограничений», из
        второй — потеря исключения, и про человека осталась бы записана
        неправда ровно в тот миг, когда он ограничение назвал."""
        plain = extract_user_facts("я ем всё")
        assert DIET_OMNIVORE in [  # наличие: без оговорки фраза разбирается
            c.content.get("diet_type") for c in plain.candidates
        ]

        result = extract_user_facts("я ем всё, только мясо я не ем")
        diets = [c.content.get("diet_type") for c in result.candidates]

        assert DIET_OMNIVORE not in diets
        # Само исключение роняется громко, как и раньше.
        assert any(d.reason == "diet_exclusion" for d in result.drops)

    def test_the_self_anchor_is_not_widened(self) -> None:
        """НАЗВАННЫЙ ПРЕДЕЛ: привязка к «я» остаётся. «У меня без ограничений»
        разбор не берёт — и расширять привязку нельзя, иначе «у меня подруга
        веган» стало бы фактом о человеке, которого спрашивали. Недобор здесь
        дешевле: он теряет ответ, а перебор записывает неправду."""
        anchored = extract_user_facts("я без ограничений")
        assert DIET_OMNIVORE in [  # наличие: с привязкой к «я» разбор берёт
            c.content.get("diet_type") for c in anchored.candidates
        ]

        result = extract_user_facts("у меня без ограничений")

        assert [c.content.get("diet_type") for c in result.candidates] == []

    def test_a_named_diet_is_untouched(self) -> None:
        result = extract_user_facts("я веган")
        diets = [c.content.get("diet_type") for c in result.candidates]

        assert diets == ["vegan"]


class TestG3EveryValueHasAPhrase:
    def test_the_shown_bank_covers_the_vocabulary(self) -> None:
        shown = set(_DIET_PHRASES) - {"none"}  # «none» — отказ, не значение

        assert shown == set(CATALOG_DIET_TYPES)

    def test_both_banks_speak_the_same_words(self) -> None:
        """Две формулировки об одном и том же разошлись бы молча."""
        for value in set(_DECLARED_DIET_PHRASES) & set(_DIET_PHRASES):
            assert _DECLARED_DIET_PHRASES[value] == _DIET_PHRASES[value], value


class TestG4ForgettingOneFactNotTheWholeTopic:
    @pytest.mark.parametrize(
        ("said", "value"),
        [
            ("без ограничений", DIET_OMNIVORE),
            ("я всеядная", DIET_OMNIVORE),
            ("особое питание", DIET_OTHER),
            ("особую диету", DIET_OTHER),
        ],
    )
    def test_the_specific_fact_is_matched(self, said: str, value: str) -> None:
        """Сопоставление идёт через ту же НОРМАЛИЗАЦИЮ, что в бою. Узел, минующий
        её, проходил через мёртвый стебель «ем всё» (ё сводится к е, и такой
        стебель не совпадёт никогда) — и удаление ЖИВОГО стебля оставалось
        зелёным."""
        from apps.persona.memory_commands import _FACT_KEYWORDS, _normalise

        stems = _FACT_KEYWORDS[("diet", value)]
        target = _normalise(said)

        assert any(stem in target for stem in stems), (target, stems)

    @pytest.mark.parametrize("said", ["я ем всегда поздно", "ем всего понемногу"])
    def test_a_phrase_that_is_not_about_diet_does_not_match(self, said: str) -> None:
        """Перебор в удалении дороже недобора: стебель «ем все» ловил «ем всегда»
        и удалял бы строку по фразе не о питании."""
        from apps.persona.memory_commands import _FACT_KEYWORDS, _normalise

        stems = _FACT_KEYWORDS[("diet", DIET_OMNIVORE)]
        target = _normalise(said)

        assert stems  # наличие: стебли у значения есть
        assert not any(stem in target for stem in stems), (target, stems)

    def test_no_stem_is_dead(self) -> None:
        """Мёртвый стебель хуже отсутствующего: он выглядит работающим.
        Нормализация сводит ё к е, значит стебля с ё существовать не может."""
        from apps.persona.memory_commands import _FACT_KEYWORDS, _normalise

        for (key, value), stems in _FACT_KEYWORDS.items():
            for stem in stems:
                assert stem == _normalise(stem), (key, value, stem)

    def test_every_vocabulary_value_can_be_forgotten_by_name(self) -> None:
        from apps.persona.memory_commands import _FACT_KEYWORDS

        named = {value for (key, value) in _FACT_KEYWORDS if key == "diet"}

        assert named == set(CATALOG_DIET_TYPES)
