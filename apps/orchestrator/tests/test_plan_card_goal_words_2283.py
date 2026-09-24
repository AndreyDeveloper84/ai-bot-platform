"""Карточка плана зовёт цель словами человека (DRF-2283, CD §73).

Владелец: Ayla хранит цель как слова человека и **показывает её в
карточке**. До этого листа карточка печатала курируемую подпись по
``goal_key`` — то есть чужие слова вместо его собственных.

## Откуда берутся слова — и почему не из plan_lite

`plan_lite` живёт внутри документа wellness-context, а у того контракт
написан прямым текстом (`wellness/context_read.py`, DRF-1344): документ
несёт **только коды состояний и ни одного текста**, потому что это вход
решающего слоя, а не экран. Положить туда дословную речь человека
значило бы снять это свойство ради удобства одной карточки.

Поэтому слова берутся из **decision-context** — документа, обращённого к
человеку, из которого их читает и экран цели в Mini App. Один источник
на две поверхности.

## Что здесь заперто

* слова человека в заголовке, когда они есть;
* **отказ этого чтения карточку не ломает**: показывается прежняя
  курируемая подпись по ключу, а не пустота и не ошибка;
* **бот не кэширует дословный текст**: каждая карточка читает заново.
  Иначе «забудь всё» сотрёт слова в каталоге, а у бота останется копия,
  и стирание перестанет быть стиранием.
"""

from __future__ import annotations

import pytest

from apps.integrations.ayla.wellness_context_client import PlanLite, PlanLiteAction
from apps.orchestrator import plan_lite_card

WORDS = "хочу −5 кг к лету"
BUCKET = ("2026-09-21", "2026-09-28")


def _plan() -> PlanLite:
    return PlanLite(
        plan_id="p-1",
        goal_key="weight",
        actions=(PlanLiteAction("log_food", "per_week", 5, 4, *BUCKET),),
    )


@pytest.fixture
def curated_label(monkeypatch):
    """Курируемая подпись по ключу — прежний живой путь."""

    monkeypatch.setattr(plan_lite_card, "goal_label", lambda key: "Вес")
    return "Вес"


class TestTheCardCallsTheGoalByThePersonsWords:
    def test_the_words_are_the_title_when_they_exist(self, curated_label, monkeypatch):
        monkeypatch.setattr(plan_lite_card, "goal_words", lambda external_user_id: WORDS)

        text = plan_lite_card.render_plan_lite_card(_plan(), words=WORDS)

        assert WORDS in text
        assert curated_label not in text

    def test_without_words_the_curated_label_stays(self, curated_label):
        text = plan_lite_card.render_plan_lite_card(_plan(), words=None)

        assert curated_label in text
        assert WORDS not in text


class TestTheReadIsBestEffort:
    def test_a_refusal_leaves_the_curated_label(self, monkeypatch):
        """Единое состояние ошибки не должно съедать единственный выход."""

        def _down(*, external_user_id):
            raise RuntimeError("goals down")

        monkeypatch.setattr("apps.integrations.ayla.goals_client.fetch_decision_context", _down)

        assert plan_lite_card.goal_words("ext-1") is None

    def test_an_empty_text_is_not_words(self, monkeypatch):
        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            lambda *, external_user_id: {"known": {"goal": {"goal_text": "   "}}},
        )

        assert plan_lite_card.goal_words("ext-1") is None

    def test_the_words_come_back_verbatim(self, monkeypatch):
        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            lambda *, external_user_id: {"known": {"goal": {"goal_text": f"  {WORDS}  "}}},
        )

        assert plan_lite_card.goal_words("ext-1") == WORDS


class TestTheBotKeepsNoCopy:
    def test_every_card_reads_the_words_again(self, monkeypatch):
        """Ни кэша, ни памяти: стирание в каталоге обязано быть стиранием."""

        calls: list[str] = []

        def _doc(*, external_user_id):
            calls.append(external_user_id)
            return {"known": {"goal": {"goal_text": WORDS}}}

        monkeypatch.setattr("apps.integrations.ayla.goals_client.fetch_decision_context", _doc)

        assert plan_lite_card.goal_words("ext-1") == WORDS
        assert plan_lite_card.goal_words("ext-1") == WORDS

        assert calls == ["ext-1", "ext-1"]

    def test_the_words_are_gone_when_the_catalog_forgot_them(self, monkeypatch):
        """Каталог стёр — следующая карточка зовёт цель курируемой подписью."""

        answers = [
            {"known": {"goal": {"goal_text": WORDS}}},
            {"known": {"goal": {"goal_key": "weight"}}},
        ]

        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            lambda *, external_user_id: answers.pop(0),
        )

        assert plan_lite_card.goal_words("ext-1") == WORDS
        assert plan_lite_card.goal_words("ext-1") is None


class TestTheCardCarriesNoLabelOfOurs:
    """Над списком действий — слова человека, и ничего нашего (§77, 24.09).

    Подмена: вернуть ярлык «Твоя цель: {goal}.» — и узел обязан покраснеть.
    """

    def test_the_first_line_is_exactly_the_persons_words(self):
        text = plan_lite_card.render_plan_lite_card(_plan(), words=WORDS)

        first = text.splitlines()[0]
        assert first == WORDS, first

    def test_no_label_of_ours_stands_above_the_list(self):
        text = plan_lite_card.render_plan_lite_card(_plan(), words=WORDS)

        first = text.splitlines()[0]
        for ours in ("Твоя цель", "Ваша цель", "Цель:", "Цель —"):
            assert ours not in first, ours
        assert not first.endswith("."), first


class TestTheChosenGoalKeepsItsOwnName:
    """Слов человека нет (цель выбрана чипом) — и подписи всё равно нет.

    Над списком стоит ровно название цели из списка владельца: это выбор
    человека, а не наша подпись. Прочтение окна, не слово владельца —
    так и помечено в коде; вопрос владельцу задан.
    """

    def test_the_curated_name_stands_alone(self, curated_label):
        text = plan_lite_card.render_plan_lite_card(_plan(), words=None)

        first = text.splitlines()[0]
        assert first == curated_label, first
        for ours in ("Твоя цель", "Ваша цель", "Цель:", "Цель —"):
            assert ours not in first, ours
        assert not first.endswith("."), first
