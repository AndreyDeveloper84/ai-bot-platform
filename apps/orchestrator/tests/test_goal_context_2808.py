"""DRF-2808 — построитель блока цели консьержа.

Бот обещает «Поняла. Буду учитывать: {goal}» — блок исполняет это обещание.
Узлы держат и правила, которые блок соблюдает намеренно: только слова
человека (курируемый ключ — никогда), не медицинская цель, не чувствительный
периметр; гейт памяти и её выключатель — общие с блоком памяти.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator import goal_context
from apps.orchestrator.goal_context import (
    build_goal_block,
    merge_goal_into_memory,
    render_goal_line,
)
from apps.orchestrator.nutrition_wellness import goal_is_medical

WORDS = "хочу выглядеть отдохнувшей к отпуску"
LINE = "Цель клиента своими словами: хочу выглядеть отдохнувшей к отпуску"
RULE = (
    "Цель — ориентир, а не правило: если клиент сейчас просит другое или уточняет "
    "цель, следуй его текущим словам. Не делай из цели выводов о здоровье и не "
    "называй диагнозов."
)
BLOCK = LINE + "\n" + RULE
LABEL = "Расслабиться и снять стресс"


def _profile(**flags):
    return SimpleNamespace(goal_overridden_by="", health_flags=flags, raw={})


@pytest.fixture
def doors(monkeypatch):
    """Все двери блока открыты; узел закрывает одну."""
    state = {
        "enabled": True,
        "consent": True,
        "goal": SimpleNamespace(key="", text=WORDS),
        "profile": None,
        "profile_reads": 0,
    }

    def _profile_read(bot_user):
        state["profile_reads"] += 1
        if state["profile"] == "unreadable":
            raise RuntimeError("catalog did not answer")
        return state["profile"]

    monkeypatch.setattr(
        "apps.orchestrator.memory_block.concierge_memory_enabled", lambda: state["enabled"]
    )
    monkeypatch.setattr(
        "apps.identity.services.personal_context.memory_green_open",
        lambda bot_user: state["consent"],
    )
    monkeypatch.setattr(
        "apps.orchestrator.nutrition_context._fetch_goal", lambda bot_user: state["goal"]
    )
    monkeypatch.setattr("apps.orchestrator.goal_context._read_profile", _profile_read)
    return state


class TestTheBlock:
    def test_the_persons_words_reach_the_block(self, doors) -> None:
        assert build_goal_block(object()) == BLOCK

    def test_without_an_anketa_the_goal_still_shows(self, doors) -> None:
        """Анкеты питания у бьюти-клиента обычно нет — это не периметр."""
        doors["profile"] = None

        assert build_goal_block(object()) == BLOCK
        assert doors["profile_reads"] == 1

    def test_an_unreadable_profile_hides_the_goal(self, doors) -> None:
        """Не знаем состояние — берём ограничительное: человек в периметре не
        должен получить разговор к противопоказанной цели из-за сбоя каталога."""
        doors["profile"] = None
        assert build_goal_block(object()) == BLOCK
        doors["profile"] = "unreadable"

        assert build_goal_block(object()) == ""

    def test_an_ordinary_profile_shows_the_goal(self, doors) -> None:
        doors["profile"] = _profile()

        assert build_goal_block(object()) == BLOCK


class TestWhatTheBlockKeepsOut:
    def test_a_bare_key_without_a_label_is_never_rendered(self, doors) -> None:
        doors["goal"] = SimpleNamespace(key="more_energy", text=None, label=None)

        block = build_goal_block(object())

        assert render_goal_line(SimpleNamespace(key="", text=WORDS)) == LINE
        assert block == ""
        assert doors["profile_reads"] == 0  # рисовать нечего — без похода за профилем

    def test_no_goal_no_block(self, doors) -> None:
        doors["goal"] = None

        assert build_goal_block(object()) == ""

    def test_a_medical_goal_is_kept_out(self, doors) -> None:
        medical = SimpleNamespace(key="", text="хочу вылечить диабет")
        assert goal_is_medical(medical)
        doors["goal"] = medical

        assert build_goal_block(object()) == ""

    @pytest.mark.parametrize("flag", ["pregnant", "breastfeeding", "eating_disorder"])
    def test_the_sensitive_perimeter_is_kept_out(self, doors, flag) -> None:
        doors["profile"] = _profile(**{flag: True})

        assert build_goal_block(object()) == ""

    def test_no_memory_green_consent_no_block(self, doors) -> None:
        doors["consent"] = False

        assert build_goal_block(object()) == ""

    def test_the_memory_kill_switch_closes_it_too(self, doors) -> None:
        doors["enabled"] = False

        assert build_goal_block(object()) == ""

    def test_a_failure_costs_the_block_not_the_turn(self, doors, monkeypatch) -> None:
        def _boom(bot_user):
            raise RuntimeError("goals layer is down")

        monkeypatch.setattr("apps.orchestrator.nutrition_context._fetch_goal", _boom)

        assert build_goal_block(object()) == ""


class TestMergeIntoMemory:
    def test_the_goal_goes_beside_the_memory(self) -> None:
        assert merge_goal_into_memory("ПАМЯТЬ", LINE, "") == f"ПАМЯТЬ\n\n{LINE}"

    def test_the_goal_alone_when_there_is_no_memory(self) -> None:
        assert merge_goal_into_memory("", LINE, "") == LINE

    def test_no_goal_leaves_the_memory_as_is(self) -> None:
        assert merge_goal_into_memory("ПАМЯТЬ", "", "") == "ПАМЯТЬ"

    def test_no_duplicate_when_the_nutrition_block_already_frames_it(self) -> None:
        nutrition = f"{LINE}\nБелок 62% от ориентира."

        merged = merge_goal_into_memory("ПАМЯТЬ", LINE, nutrition)

        assert merged == "ПАМЯТЬ"

    def test_one_renderer_for_both_blocks(self) -> None:
        """Совпадение строк — точное; ради него у обоих блоков один отрисовщик."""
        from apps.orchestrator.nutrition_context import _render_goal_lines

        goal = SimpleNamespace(key="", text=WORDS)
        assert _render_goal_lines(goal) == [render_goal_line(goal)]
        assert goal_context.render_goal_line(goal) == LINE


class TestAGoalChosenByButton:
    """Решение владельца 06.10, вариант (Б): подпись опции с происхождением."""

    def test_the_label_reaches_the_model_as_chosen_from_the_list(self, doors) -> None:
        doors["goal"] = SimpleNamespace(key="relax", text=None, label=LABEL)

        block = build_goal_block(object())

        assert block == (
            "Цель клиента выбрана им из списка: «Расслабиться и снять стресс». Это наша "
            "формулировка, а не его слова — не цитируй её как слова клиента.\n" + RULE
        )
        assert "своими словами" not in block
        assert "relax" not in block

    def test_the_persons_words_outrank_the_label(self, doors) -> None:
        doors["goal"] = SimpleNamespace(key="relax", text=WORDS, label=LABEL)

        block = build_goal_block(object())

        assert block == BLOCK
        assert LABEL not in block

    def test_the_chosen_goal_respects_the_perimeter_too(self, doors) -> None:
        doors["goal"] = SimpleNamespace(key="relax", text=None, label=LABEL)
        doors["profile"] = _profile(eating_disorder=True)

        assert build_goal_block(object()) == ""


class TestTheLabelFromTheCatalogDocument:
    """``known.goal.label`` у каталога — слова, иначе подпись опции, иначе ключ."""

    @staticmethod
    def _goal(**known_goal):
        from apps.nutrition_coach.goals import _goal_from_document

        return _goal_from_document({"known": {"goal": known_goal}})

    def test_a_curated_label_is_carried(self) -> None:
        goal = self._goal(goal_key="relax", goal_text=None, label=LABEL)
        assert goal is not None
        assert goal.label == LABEL

    def test_the_key_echoed_as_a_label_is_not_a_label(self) -> None:
        goal = self._goal(goal_key="relax", goal_text=None, label="relax")
        assert goal is not None
        assert goal.label is None

    def test_the_persons_words_echoed_as_a_label_are_not_a_label(self) -> None:
        goal = self._goal(goal_key="", goal_text=WORDS, label=WORDS)
        assert goal is not None
        assert goal.label is None


class TestMergeTheFullBlock:
    def test_the_rule_rides_along_and_the_duplicate_is_still_caught(self) -> None:
        nutrition = f"{LINE}\nБелок 62% от ориентира."

        assert merge_goal_into_memory("ПАМЯТЬ", BLOCK, nutrition) == "ПАМЯТЬ"
        assert merge_goal_into_memory("ПАМЯТЬ", BLOCK, "") == f"ПАМЯТЬ\n\n{BLOCK}"
