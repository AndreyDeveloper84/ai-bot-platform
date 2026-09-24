"""DRF-2440: отказ golden-узла несёт форму ответа, но не сам ответ.

Было: `must_pass.response_contains_any: expected ['возраст'], got '<text>'`.
Литерал `<text>` верен по замыслу (текст переписки наружу не выносим) и
бесполезен по содержанию: по нему нельзя отличить **«навык ответил иначе»** от
**«ответ подменили после навыка»** — на этом 24.09 ушло полдня и две неверные
версии.

Стало: `got 'response(empty=false chars=137 words=21 matched=0/1
missing=['возраст'] template=outage_ru)'`.

Узлы здесь — обе стороны, потому что каждая по отдельности зелёная и лживая:

* **форма есть** — сообщение называет размер, промахи и шаблон;
* **текста нет** — ни одного слова ответа в сообщении, и это проверяется
  подменой: в ответ кладётся приметное слово, которого в фикстуре нет;
* **реестр шаблонов не слепнет** — каждый адрес резолвится у источника, иначе
  переименование константы молча превратило бы `template` в вечное `none`
  ровно тогда, когда подмена и случилась.
"""

from __future__ import annotations

import pytest

from apps.replay.assertions import evaluate
from apps.replay.response_shape import (
    NO_TEMPLATE,
    TEMPLATES,
    describe,
    known_template,
    template_registry,
)

#: Приметное слово, которого нет ни в одной фикстуре этого файла: если оно
#: всплывёт в сообщении, значит утёк ответ, а не форма.
SECRET = "абракадабра-9713"


def _trace(text: str) -> dict:
    return {
        "intent": "faq",
        "skill_used": "faq",
        "safety_decision": "allow",
        "tool_calls": [],
        "response_text": text,
    }


def _failures(text: str, constraint: dict, *, forbidden: bool = False) -> list[str]:
    if forbidden:
        return evaluate(_trace(text), [], [constraint])
    return evaluate(_trace(text), [constraint], [])


# ─── форма есть ────────────────────────────────────────────────────────────


class TestTheFailureCarriesTheShape:
    def test_a_miss_names_size_misses_and_template(self) -> None:
        failures = _failures(
            f"Совсем другой ответ про {SECRET}",
            {"response_contains_any": ["возраст"]},
        )

        assert len(failures) == 1  # положительно: отказ есть
        message = failures[0]
        assert "must_pass.response_contains_any" in message
        assert "chars=" in message and "words=" in message
        assert "matched=0/1" in message
        assert "'возраст'" in message  # подстрока ФИКСТУРЫ — её назвать можно
        assert "<text>" not in message  # литерала больше нет

    def test_a_partial_match_says_which_expected_substrings_landed(self) -> None:
        failures = _failures(
            f"Записаться можно, {SECRET}",
            {"response_contains_all": ["запис", "возраст"]},
        )

        message = failures[0]
        assert "matched=1/2" in message
        assert "'возраст'" in message  # чего не хватило
        assert "'запис'" in message  # что нашлось

    def test_an_empty_answer_is_named_empty(self) -> None:
        failures = _failures("", {"response_contains_any": ["возраст"]})

        message = failures[0]
        assert "empty=true" in message
        assert "chars=0" in message

    def test_a_replaced_answer_names_the_template(self) -> None:
        from apps.orchestrator.llm.templates import OUTAGE_RU

        failures = _failures(OUTAGE_RU, {"response_contains_any": ["возраст"]})

        message = failures[0]
        assert "template=outage_ru" in message
        # …и это ровно то различие, ради которого лист заведён: ответ навыка
        # с тем же промахом шаблоном не назван
        other = _failures(f"Ответ навыка про {SECRET}", {"response_contains_any": ["возраст"]})
        assert f"template={NO_TEMPLATE}" in other[0]

    def test_the_forbidden_side_says_which_needle_hit(self) -> None:
        failures = _failures(
            f"Не знаю, {SECRET}",
            {"response_contains_any": ["не знаю"]},
            forbidden=True,
        )

        assert len(failures) == 1
        assert "forbidden.response_contains_any" in failures[0]
        assert "'не знаю'" in failures[0]

    def test_exact_measures_without_folding_case(self) -> None:
        # «Возраст» с большой буквы не считается совпадением для exact —
        # и форма обязана сказать 0/1, а не 1/1, иначе объяснит не тот промах.
        shape = describe("Возраст не спрашиваем", ["возраст"], case_folded=False)
        folded = describe("Возраст не спрашиваем", ["возраст"], case_folded=True)

        assert "matched=0/1" in shape
        assert "matched=1/1" in folded


# ─── текста нет ────────────────────────────────────────────────────────────


class TestTheAnswerNeverLeaks:
    @pytest.mark.parametrize(
        "constraint",
        (
            {"response_contains_any": ["возраст"]},
            {"response_contains_all": ["возраст", "запис"]},
            {"response_contains_exact": ["Возраст"]},
        ),
    )
    def test_no_word_of_the_answer_appears_in_the_message(self, constraint: dict) -> None:
        text = f"Здравствуйте, меня зовут Мария, телефон 89001234567, {SECRET}"

        failures = _failures(text, constraint)

        assert failures  # положительно: отказ построен, есть что проверять
        message = failures[0]
        assert SECRET not in message
        for word in ("Мария", "89001234567", "Здравствуйте"):
            assert word not in message

    def test_the_forbidden_side_leaks_nothing_either(self) -> None:
        text = f"Не знаю. Меня зовут Мария, {SECRET}"

        failures = _failures(text, {"response_contains_any": ["не знаю"]}, forbidden=True)

        assert failures
        assert SECRET not in failures[0]
        assert "Мария" not in failures[0]

    def test_a_template_answer_is_named_not_quoted(self) -> None:
        from apps.orchestrator.llm.templates import OUTAGE_RU

        failures = _failures(OUTAGE_RU, {"response_contains_any": ["возраст"]})

        assert "template=outage_ru" in failures[0]
        assert OUTAGE_RU not in failures[0]  # имя шаблона — да, текст — нет


# ─── реестр шаблонов не слепнет ────────────────────────────────────────────


class TestTheTemplateRegistryStaysHonest:
    def test_every_address_still_resolves(self) -> None:
        resolved = template_registry()

        assert set(resolved) == set(TEMPLATES), (
            "адрес шаблона не резолвится — константу переименовали или перенесли; "
            "почини адрес, иначе template молча станет вечным none"
        )
        for name, value in resolved.items():
            assert value.strip(), f"{name}: пустое значение шаблона"

    def test_a_full_match_is_a_template_and_a_partial_one_is_not(self) -> None:
        from apps.orchestrator.llm.templates import OUTAGE_RU

        assert known_template(OUTAGE_RU) == "outage_ru"  # положительно
        assert known_template(f"{OUTAGE_RU} и ещё кое-что") is None  # вхождение ≠ замена
        assert known_template("") is None
