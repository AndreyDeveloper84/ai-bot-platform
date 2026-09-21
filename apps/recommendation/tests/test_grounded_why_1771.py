"""Grounded WHY: слова человека против подписи чипа (DRF-1771, К-3 N5).

Лист в исходной формулировке (рендер `evidence_refs`) остаётся заблокирован
D11 — связи «код причины ↔ факт разговора» как ДАННЫХ нет ни в решении
резолвера, ни в документе целей. Здесь — честная часть 1771:

1. человек НАПИСАЛ (свободная цель `goal_text`, свободный ответ шага
   `answer_text`) → причина цитирует его слова; ВЫБРАЛ из предложенного →
   прежний пересказ по подписи варианта;
2. у каждой причины — происхождение (`text` / `choice`) в фактах записи:
   связь причины с фактом становится данными на нашей стороне;
3. запрещённая форма владельца 24.08 §1 («это сейчас для тебя самое
   важное» и родня) — сторож: карточки нет. Раньше правило держалось
   только договорённостью (grep по репозиторию давал 0);
4. безопасность формы цитаты: переносы и управляющие символы, очень
   длинный текст, текст из одних пробелов → причины нет (не пустая
   цитата); кавычки не ломают разметку;
5. цитата НЕ уходит в журнал.
"""

from __future__ import annotations

import logging

import pytest

from apps.recommendation import card as c


def _goal(**over):
    goal = {
        "id": "goal-1",
        "goal_key": "self_care",
        "goal_text": None,
        "label": "Привести себя в порядок",
        "direction": {"what": "Уменьшить утреннюю отёчность", "subline": "", "area_key": "face"},
        "answers": [],
    }
    goal.update(over)
    return goal


def _doc(goal=None):
    return {
        "version": 2,
        "known": {"goal": _goal() if goal is None else goal, "anketa": []},
        "missing": [],
        "suggestions": [],
        "intents": [],
        "next": {"id": "return_to_chat", "label": "Вернуться в чат"},
    }


def _answer(step: str, *, label: str, option_key: str | None = None, text: str | None = None):
    return {
        "step": step,
        "label": label,
        "option_key": option_key,
        "option_keys": [],
        "unknown": False,
        "origin": "anketa",
        "answer_text": text,
    }


class TestSaidVersusChose:
    def test_free_text_goal_is_quoted_in_the_persons_own_words(self):
        goal = _goal(
            goal_key=None, goal_text="хочу выглядеть свежее", label="хочу выглядеть свежее"
        )
        why, facts = c.grounded_reasons(goal)
        assert why[0] == c.WHY_GOAL_QUOTED.format(quote="хочу выглядеть свежее")
        assert facts["goal"] == {"value": "хочу выглядеть свежее", "origin": c.ORIGIN_TEXT}

    def test_chip_goal_keeps_the_restatement(self):
        why, facts = c.grounded_reasons(_goal())
        assert why[0] == c.WHY_GOAL.format(goal="привести себя в порядок")
        assert facts["goal"] == {"value": "Привести себя в порядок", "origin": c.ORIGIN_CHOICE}

    def test_free_text_answer_is_quoted_too(self):
        goal = _goal(answers=[_answer("area", label="болит спина", text="болит спина")])
        why, facts = c.grounded_reasons(goal)
        assert why[1] == c.WHY_STEP_QUOTED.format(quote="болит спина")
        assert facts["area"] == {"value": "болит спина", "origin": c.ORIGIN_TEXT}

    def test_chip_answer_keeps_the_restatement(self):
        goal = _goal(answers=[_answer("area", label="Лицо и кожа", option_key="face")])
        why, facts = c.grounded_reasons(goal)
        assert why[1] == c.WHY_AREA.format(area="лицо и кожа")
        assert facts["area"] == {"value": "Лицо и кожа", "origin": c.ORIGIN_CHOICE}

    def test_quoting_is_switched_off_in_one_place(self, monkeypatch):
        """Вопрос владельцу «цитата или всегда пересказ» открыт: выключение —
        одна константа, не правка веток."""
        goal = _goal(
            goal_key=None, goal_text="хочу выглядеть свежее", label="хочу выглядеть свежее"
        )
        monkeypatch.setattr(c, "QUOTE_THE_PERSON", False)
        why, facts = c.grounded_reasons(goal)
        assert why[0] == c.WHY_GOAL.format(goal="хочу выглядеть свежее")
        # Происхождение остаётся правдой даже без цитаты.
        assert facts["goal"]["origin"] == c.ORIGIN_TEXT

    def test_with_quoting_off_a_typed_answer_gives_no_phrase_but_stays_a_fact(self):
        """«Ты выбрала: болит спина» врёт о способе — человек это написал.
        Формы пересказа под произвольные слова у нас нет, поэтому фразы
        нет вовсе; факт с происхождением остаётся в записи."""
        goal = _goal(answers=[_answer("area", label="болит спина", text="болит спина")])
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(c, "QUOTE_THE_PERSON", False)
        try:
            why, facts = c.grounded_reasons(goal)
        finally:
            monkeypatch.undo()
        assert why == (c.WHY_GOAL.format(goal="привести себя в порядок"),)
        assert facts["area"] == {"value": "болит спина", "origin": c.ORIGIN_TEXT}


class TestForbiddenForm:
    @pytest.mark.parametrize(
        "text",
        [
            "Это сейчас для тебя самое важное",
            "Это для тебя важнее всего",
            "Главное для тебя сейчас — отёчность",
            "Для тебя это наиболее важно",
        ],
    )
    def test_priority_claim_refuses_the_card(self, text):
        """24.08 §1 — «недопустимо без явного подтверждения приоритета»."""
        assert c.boundary_violation(text) == "priority_claim"
        assert c.build_card(_doc(_goal(direction={"what": text, "subline": ""}))) is None

    def test_a_quote_of_those_words_is_refused_too(self):
        """Человек может написать что угодно; наша карточка этого не утверждает."""
        goal = _goal(
            goal_key=None, goal_text="это для меня самое важное", label="это для меня самое важное"
        )
        assert c.build_card(_doc(goal)) is None

    def test_plain_words_are_untouched(self):
        assert c.boundary_violation("Уменьшить утреннюю отёчность") is None
        assert c.boundary_violation("Тебе важно выглядеть свежее") is None


class TestQuoteSafety:
    def test_newlines_and_control_chars_are_not_a_reason(self):
        goal = _goal(
            goal_key=None, goal_text="хочу\nсвежесть\r\tсейчас", label="хочу\nсвежесть\r\tсейчас"
        )
        why, facts = c.grounded_reasons(goal)
        # Цитата в одну строку — текст нормализован, а не выброшен.
        assert why[0] == c.WHY_GOAL_QUOTED.format(quote="хочу свежесть сейчас")
        assert "\n" not in why[0]
        assert facts["goal"]["value"] == "хочу свежесть сейчас"

    def test_whitespace_only_text_gives_no_reason(self):
        goal = _goal(goal_key=None, goal_text="   ", label="   ")
        why, facts = c.grounded_reasons(goal)
        assert why == ()
        assert facts == {}

    def test_a_very_long_quote_is_cut_on_a_word_boundary(self):
        long_text = "хочу " * 60
        goal = _goal(goal_key=None, goal_text=long_text, label=long_text)
        why, _ = c.grounded_reasons(goal)
        quote = why[0][len(c.WHY_GOAL_QUOTED.split("{quote}")[0]) :].rstrip('»"')
        assert len(quote) <= c.QUOTE_MAX_CHARS
        assert quote.endswith("…")
        assert "  " not in quote

    def test_quotes_inside_the_text_do_not_break_the_frame(self):
        goal = _goal(
            goal_key=None,
            goal_text='хочу «свежесть» и "лёгкость"',
            label='хочу «свежесть» и "лёгкость"',
        )
        why, _ = c.grounded_reasons(goal)
        assert why[0].startswith(c.WHY_GOAL_QUOTED.split("{quote}")[0])
        assert why[0].endswith(c.WHY_GOAL_QUOTED.split("{quote}")[1])

    def test_the_quote_never_reaches_the_log(self, caplog):
        secret = "хочу выглядеть свежее"
        goal = _goal(goal_key=None, goal_text=secret, label=secret)
        with caplog.at_level(logging.DEBUG, logger="apps.recommendation.card"):
            c.build_card(_doc(goal))
        assert secret not in caplog.text


class TestStillHolds:
    def test_dont_know_is_still_not_a_reason(self):
        goal = _goal(
            answers=[{**_answer("area", label="Не знаю", option_key="unknown"), "unknown": True}]
        )
        why, facts = c.grounded_reasons(goal)
        assert why == (c.WHY_GOAL.format(goal="привести себя в порядок"),)
        assert "area" not in facts

    def test_zero_reasons_still_means_no_card(self):
        assert c.build_card(_doc(_goal(label="", goal_text=None, answers=[]))) is None
