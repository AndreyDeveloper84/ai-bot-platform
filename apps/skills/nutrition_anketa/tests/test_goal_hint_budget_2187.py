"""DRF-2187 — подсказка цели в анкете питания не ждёт общий бюджет чтения.

Подсказка — необязательная строка под вопросом о цели (DRF-2124): без неё шаг
полноценен. Читала она документ общим бюджетом клиента — 5 s на чтение, а с
соединением 11 s, — то есть человек мог ждать вопрос анкеты одиннадцать секунд
ради строки, без которой вопрос задаётся и так.

* шаг цели зовёт ``fetch_decision_context`` со своим бюджетом — числом,
  записанным литералом, и это число меньше общего;
* истёк бюджет — шаг рисуется без подсказки, тем же простым вопросом.
"""

from __future__ import annotations

from unittest.mock import Mock

import httpx

from apps.integrations.ayla import goals_client as gc
from apps.skills.nutrition_anketa.tests import test_goal_hint_2124 as _hint_module

# Помощники соседнего модуля — присваиванием, как в узлах DRF-1933.
_BODY = _hint_module._BODY
_PLAIN_GOAL_PROMPT = _hint_module._PLAIN_GOAL_PROMPT
_Run = _hint_module._Run
_ask_goal = _hint_module._ask_goal
_doc = _hint_module._doc
_state = _hint_module._state


def test_the_goal_step_reads_the_hint_on_its_own_short_budget() -> None:
    run = _Run(_state("activity", _BODY), doc=_doc(["lose", "maintain"]))

    result = _ask_goal(run)

    # Presence first: the hint was read and shown — the call below is the real one.
    assert "обычно выбирают" in result.reply_text
    run.fetch.assert_called_once_with(external_user_id="bot:max:12345", read_timeout_s=2.0)
    assert 2.0 < gc.READ_TIMEOUT_S


def test_a_timed_out_hint_leaves_the_plain_question() -> None:
    fetch = Mock(
        side_effect=gc.GoalsUnavailable("network: ReadTimeout", cause=httpx.ReadTimeout("x"))
    )
    run = _Run(_state("activity", _BODY), fetch=fetch)

    result = _ask_goal(run)

    assert result.action_type == "anketa_step_goal"
    assert result.reply_text == _PLAIN_GOAL_PROMPT
    assert fetch.call_count == 1
