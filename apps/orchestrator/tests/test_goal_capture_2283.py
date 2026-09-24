"""Цель словами человека, сказанная в переписке (DRF-2283, CD §73).

Владелец 22.09: человек записывает свою цель как есть («хочу −5 кг к
лету»), Ayla хранит её **как его слова**. Хранилище и ручка записи уже
есть (`ClientGoal.goal_text`, `POST goals/select/`); в чате входа не
было — только кнопка в приложение.

## Чего этот модуль боится

«Хочу …» — обычная человеческая фраза, и **большинство таких фраз целью
не являются**. Ложная цель хуже отсутствующей: человек потом не может
объяснить, откуда она взялась. Поэтому отрицательных узлов здесь больше,
чем положительных, и правило одно: **не уверены — не распознаём**.
Кнопка «Выбрать цель» никуда не делась.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.goal_capture import (
    CONFIRMATION,
    capture_goal_from_chat,
    looks_like_goal_statement,
)

# Цель — только явное заявление о себе: результат, форма, срок.
GOALS = [
    "хочу −5 кг к лету",
    "хочу минус 5 кг к лету",
    "хочу привести себя в порядок к отпуску",
    "моя цель — похудеть",
    "цель: подтянуть фигуру",
]

# Всё остальное. Каждая строка — то, что человек говорит каждый день.
NOT_GOALS = [
    # еда
    "хочу есть",
    "хочу пиццу",
    "хочу кофе",
    "хочу шаурму на обед",
    # запись
    "хочу записаться",
    "хочу к мастеру",
    "хочу на маникюр",
    "хочу записаться на стрижку к лету",  # срок есть, но это запись
    # быт
    "хочу спать",
    "хочу домой",
    "хочу в отпуск",
    # вопросы и пустое
    "а что ты умеешь",
    "хочу",
    "",
]


class TestWhatIsAGoalAndWhatIsNot:
    @pytest.mark.parametrize("text", GOALS)
    def test_a_goal_is_recognised(self, text):
        assert looks_like_goal_statement(text) is True

    @pytest.mark.parametrize("text", NOT_GOALS)
    def test_an_everyday_phrase_is_not_a_goal(self, text):
        assert looks_like_goal_statement(text) is False


class TestTheWordsAreWrittenAsSaid:
    def test_the_verbatim_text_goes_to_the_one_write_path(self, monkeypatch):
        """Та же ручка, что у приложения, и `source_channel="bot"`."""

        sent: dict = {}

        def _fake_post(*, external_user_id, payload):
            sent["external_user_id"] = external_user_id
            sent["payload"] = payload
            return {"known": {"goal": {"goal_text": payload["goal_text"]}}}

        monkeypatch.setattr("apps.integrations.ayla.goals_client.post_goal_select", _fake_post)
        monkeypatch.setattr(
            "apps.orchestrator.goal_capture._screen", lambda bot_user, body: (None, body)
        )

        result = capture_goal_from_chat(
            bot_user=object(), external_user_id="ext-1", text="  хочу −5 кг к лету  "
        )

        assert result is not None
        # Дословно: обрезаны только пробелы по краям, больше ничего.
        assert sent["payload"] == {"goal_text": "хочу −5 кг к лету", "source_channel": "bot"}
        assert sent["external_user_id"] == "ext-1"

    def test_a_health_signal_stops_the_write_and_does_not_claim_the_turn(self, monkeypatch):
        """Ворота здоровья (DRF-1763) — те же, что у приложения.

        Записать цель мимо них значило бы обойти безопасность чатовым
        путём. Ход не забираем: пусть его ведёт обычный путь, а не
        выдуманный здесь текст.
        """

        called = False

        def _fake_post(*, external_user_id, payload):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr("apps.integrations.ayla.goals_client.post_goal_select", _fake_post)
        monkeypatch.setattr(
            "apps.orchestrator.goal_capture._screen", lambda bot_user, body: (object(), body)
        )

        result = capture_goal_from_chat(
            bot_user=object(), external_user_id="ext-1", text="хочу похудеть, болит спина"
        )

        assert result is None
        assert called is False

    def test_ayla_unavailable_does_not_claim_the_turn(self, monkeypatch):
        from apps.integrations.ayla.goals_client import GoalsUnavailable

        def _fake_post(*, external_user_id, payload):
            raise GoalsUnavailable("down")

        monkeypatch.setattr("apps.integrations.ayla.goals_client.post_goal_select", _fake_post)
        monkeypatch.setattr(
            "apps.orchestrator.goal_capture._screen", lambda bot_user, body: (None, body)
        )

        assert (
            capture_goal_from_chat(
                bot_user=object(), external_user_id="ext-1", text="хочу −5 кг к лету"
            )
            is None
        )


class TestTheWordsStayOutOfAnalytics:
    """Дословные слова живут в хранилище — не в логах.

    Каталог держит то же правило на своей стороне: событие воронки несёт
    `has_text`, а не сам текст. Проверка поведенческая: гоняем оба исхода и
    смотрим, что фразы в логах нет. Считать строки исходника бессмысленно —
    `len(goal_text)` в логе как раз законен.
    """

    SECRET = "хочу −5 кг к лету и подтянуть фигуру"

    def test_a_written_goal_is_not_in_the_log(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.post_goal_select",
            lambda **kwargs: {},
        )
        monkeypatch.setattr(
            "apps.orchestrator.goal_capture._screen", lambda bot_user, body: (None, body)
        )

        with caplog.at_level(logging.DEBUG):
            captured = capture_goal_from_chat(
                bot_user=object(), external_user_id="ext-1", text=self.SECRET
            )

        assert captured == self.SECRET
        assert self.SECRET not in caplog.text

    def test_a_refused_goal_is_not_in_the_log_either(self, monkeypatch, caplog):
        import logging

        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.post_goal_select",
            lambda **kwargs: {},
        )
        monkeypatch.setattr(
            "apps.orchestrator.goal_capture._screen", lambda bot_user, body: (object(), body)
        )

        with caplog.at_level(logging.DEBUG):
            assert (
                capture_goal_from_chat(
                    bot_user=object(), external_user_id="ext-1", text=self.SECRET
                )
                is None
            )

        assert self.SECRET not in caplog.text


class TestTheOwnersWords:
    """Текст владельца, 24.09, реестр §77 — проверяется ЦЕЛИКОМ.

    Не «содержит слово цель»: двоеточие, отсутствие точки в конце и «Твоя»
    вместо «Ваша» — части формулировки, которую владелец утвердил. Узел на
    подстроку пропустил бы и точку, и «Ваша», и они вернулись бы сами.
    """

    def test_the_confirmation_is_the_owners_line_verbatim(self):
        assert CONFIRMATION == "Твоя цель теперь: {goal}"
        assert CONFIRMATION.format(goal="хочу −5 кг к лету") == (
            "Твоя цель теперь: хочу −5 кг к лету"
        )

    def test_the_line_ends_without_a_full_stop(self):
        """Точка в конце — отдельная проверка: её возвращают чаще всего."""
        assert not CONFIRMATION.rstrip("}goal{").endswith(".")
        assert CONFIRMATION.format(goal="борщ")[-1] != "."
