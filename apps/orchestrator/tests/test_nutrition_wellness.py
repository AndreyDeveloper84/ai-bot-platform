"""Nutrition Wellness Interpretation — гейт и его контракт (§48).

Решение владельца 07.09.2026, `docs/OPEN_DECISIONS.md` §48. Здесь прибиты
две вещи, и вторая важнее первой.

``TestOwnerContract`` — четыре класса, заданные владельцем дословно.
Проверяются КОДОМ, а не прогоном модели: в этом и был смысл требования
«гейт детерминированный». Утверждение «модель не ответит на боль»
проверяемо только живым прогоном, то есть недетерминированно; утверждение
«на ход про боль картина не прикладывается» проверяется здесь.

``TestVetoDoesNotEatOwnGoals`` — обратная сторона: veto, который глушит
собственную таксономию целей продукта, закрыл бы способность ровно для
тех, ради кого она есть.

Регрессия на исходный дефект DRF-1284 (требование §48) живёт отдельно, в
``test_concierge_nutrition_permission.py``: она про промпт, а не про гейт,
и уехала туда вместе с правкой инструкции консьержа, которую §48 велел
держать отдельным коммитом.
"""

from __future__ import annotations

import pytest

from apps.nutrition_coach.goals import Goal
from apps.orchestrator.nutrition_wellness import (
    goal_is_medical,
    interpretation_eligible,
    medical_veto,
    turn_is_about_food,
)

# Цели из утверждённой таксономии (§3.1). Ключ курируемый — значит цель
# пришла из мастера выбора, а не из свободного поля.
RECOVERY_GOAL = Goal(key="more_energy", text="восстановить силы")
BEAUTY_GOAL = Goal(key="skin_glow", text="здоровое сияние кожи")

# Свободная формулировка (`category='custom'`, у нас key == ""), в которую
# человек написал медицинскую цель. §3.3 разворачивает такие на входе, но
# ловит не всё — этот случай и есть та дыра.
MEDICAL_GOAL = Goal(key="", text="вылечить гастрит и сесть на лечебную диету")


class TestOwnerContract:
    """Четыре класса §48, дословно из решения владельца."""

    def test_food_plus_recovery_goal_is_allowed(self) -> None:
        assert interpretation_eligible("Что мне съесть после тренировки?", goal=RECOVERY_GOAL)

    def test_food_plus_beauty_goal_is_allowed(self) -> None:
        assert interpretation_eligible("Питание влияет на то, как выглядит кожа?", goal=BEAUTY_GOAL)

    @pytest.mark.parametrize(
        "text",
        [
            "После обеда болит живот",
            "Почему меня тошнит после еды?",
            "Какие витамины пить при таком питании?",
            "Мне нужна диета, чтобы похудеть на 5 кг",
        ],
    )
    def test_food_plus_symptom_or_pain_is_blocked(self, text: str) -> None:
        """Медицинская тема закрывает допуск, даже когда ход про еду.

        Контроль присутствия рядом: тот же самый ход без медицинского
        слова допуск получает — иначе «заблокировано» доказывало бы лишь
        то, что предикат вообще ничего не пропускает.
        """
        assert turn_is_about_food(text), "ход обязан быть про еду, иначе тест не о том"
        assert interpretation_eligible(text, goal=RECOVERY_GOAL) is False

    def test_food_plus_medical_goal_is_blocked(self) -> None:
        """Цель тоже закрывает — и это вторая ступень гейта, по данным."""
        text = "Что мне съесть на обед?"
        # Контроль присутствия: с нормальной целью этот же ход проходит.
        assert interpretation_eligible(text, goal=RECOVERY_GOAL)
        assert interpretation_eligible(text, goal=MEDICAL_GOAL) is False

    def test_a_curated_goal_key_is_never_medical(self) -> None:
        """Курируемые ключи безопасны по построению (§3.1 медицинских не несёт).

        Проверять их текст значило бы наказывать человека за формулировку,
        которую он не писал: она наша.
        """
        assert goal_is_medical(Goal(key="better_sleep", text="лечить бессонницу")) is False
        assert goal_is_medical(None) is False


class TestNotAboutFoodIsNotTheSameAsBlocked:
    """«Не про еду» и «заблокировано» — разные исходы, и разница видна.

    Ход про запись к мастеру не «нарушает границу» — он просто не о том.
    Слить их в один False удобно, но тогда в логе исчезнет причина, а
    вместе с ней и возможность понять, почему поверхность молчит.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "Подбери мастера по маникюру",
            "Есть ли у вас окошко в четверг?",
            "Во сколько вы работаете?",
            "",
            None,
        ],
    )
    def test_non_food_turns_never_attach_the_picture(self, text) -> None:
        assert turn_is_about_food(text) is False
        assert interpretation_eligible(text, goal=RECOVERY_GOAL) is False

    def test_the_word_est_alone_is_not_food(self) -> None:
        """«Есть ли окошко» — это глагол «иметься», а не приём пищи.

        Ровно та причина, по которой голое «есть» не входит в список: на
        нём предикат прикладывал бы картину питания к вопросу о записи.
        """
        assert turn_is_about_food("Есть ли у вас окошко в четверг?") is False
        assert turn_is_about_food("Что мне поесть вечером?") is True


class TestVetoDoesNotEatOwnGoals:
    """Veto не глушит собственную таксономию целей продукта (§3.1)."""

    @pytest.mark.parametrize(
        "word,category",
        [
            ("усталость", "1 — самочувствие и энергия"),
            ("отёчность", "4 — тело и форма"),
            ("сон", "5 — сон и восстановление"),
            ("кожа", "3 — внешний вид"),
            ("волосы", "6 — состояние волос и кожи"),
        ],
    )
    def test_wellness_words_are_not_medical(self, word: str, category: str) -> None:
        assert medical_veto(f"меня беспокоит {word}") is False, category

    def test_but_the_same_topic_with_pain_is(self) -> None:
        """Граница проходит не по теме, а по уходу в симптом."""
        assert medical_veto("плохой сон") is False
        assert medical_veto("плохой сон и болит голова") is True
