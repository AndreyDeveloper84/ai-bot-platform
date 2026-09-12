"""DRF-1760 — копия уточнений в DM по макетам C02/C03.

Три обещания макета, у каждого — свой сторож:

* C02.3 «Не знаю» — отдельная кнопка на вопросе в режиме ``free``
  (у него нет вариантов, и до этого клавиатуры не было вовсе);
* C03 «Не знаю» — полноценный ответ: тап не протухает, отвечает своим
  текстом, и в ``ClarifyOutcome`` есть чем закрыть открытый вопрос (DRF-1779);
* C02.2 «Выбрано: N» — честное число отмеченного на multiselect, строки нет
  при нуле.
"""

from __future__ import annotations

from apps.orchestrator import discovery

_QUESTION = "Что именно нужно?"
_OPTIONS = ["Маникюр", "Педикюр", "Стрижка"]


def _buttons(reply: discovery.DiscoveryReply) -> list[dict[str, str]]:
    assert reply.action_data is not None
    return [
        b
        for row in reply.action_data["attachments"][0]["payload"]["buttons"]
        for b in (row if isinstance(row, list) else [row])
    ]


class TestDontKnowTap:
    def test_payload_parses_as_its_own_kind(self):
        tap = discovery.parse_clarify_callback(discovery.CLARIFY_DONT_KNOW_CALLBACK)
        assert tap is not None and tap.kind == "dontknow"

    def test_answers_even_when_no_options_were_offered(self):
        """Free-вопрос по построению без вариантов — «Не знаю» на нём не протух."""
        outcome = discovery.execute_clarify_callback(
            discovery.CLARIFY_DONT_KNOW_CALLBACK, [], _QUESTION
        )
        assert outcome is not None and outcome.reply is not None
        assert outcome.reply.text == discovery.CLARIFY_DONT_KNOW_TEXT
        assert outcome.reply.text != discovery.CLARIFY_STALE_TEXT
        # Ответ — в модель не идёт и вопрос не перерисовывает.
        assert outcome.submit_text == ""
        assert outcome.redraw is False

    def test_carries_the_answer_to_close_the_open_question(self):
        outcome = discovery.execute_clarify_callback(discovery.CLARIFY_DONT_KNOW_CALLBACK, _OPTIONS)
        assert outcome is not None
        assert outcome.answer_text == discovery.CLARIFY_DONT_KNOW_LABEL

    def test_other_taps_carry_no_answer(self):
        """Тогл — перерисовка, а не ответ: закрывать вопрос им нельзя."""
        outcome = discovery.execute_clarify_callback("cb:clarify:tg:0:0", _OPTIONS)
        assert outcome is not None and outcome.redraw is True
        assert outcome.answer_text == ""


class TestFreeModeKeyboard:
    def test_free_question_gets_one_dont_know_button(self):
        reply = discovery._render_ask_clarification(_QUESTION, [], offer_dont_know=True)
        assert reply.text == _QUESTION
        buttons = _buttons(reply)
        assert [b["label"] for b in buttons] == [discovery.CLARIFY_DONT_KNOW_LABEL]
        assert buttons[0]["callback"] == discovery.CLARIFY_DONT_KNOW_CALLBACK
        assert reply.action_data is not None
        assert reply.action_data["clarification"] == {
            "mode": discovery.CLARIFICATION_MODE_FREE,
            "options": [],
        }

    def test_canon_no_criteria_replies_stay_bare(self):
        """Канонические «без критериев» — не вопрос модели: без кнопки, как были."""
        assert discovery._render_ask_clarification(_QUESTION, []).action_data is None
        assert discovery.render_no_criteria_clarification().action_data is None

    def test_question_with_options_has_no_dont_know(self):
        """Кнопка — только у free: у выбора из вариантов свой набор."""
        reply = discovery._render_ask_clarification(_QUESTION, _OPTIONS, offer_dont_know=True)
        labels = [b.get("label") or b.get("text") for b in _buttons(reply)]
        assert _OPTIONS[0] in labels
        assert discovery.CLARIFY_DONT_KNOW_LABEL not in labels


class TestSelectedCount:
    def test_two_ticked_reads_selected_two(self):
        reply = discovery.render_multiselect_clarification(_QUESTION, _OPTIONS, mask=0b101)
        assert reply.text.startswith(_QUESTION)
        assert reply.text.endswith("Выбрано: 2")

    def test_question_is_kept_apart_from_the_count(self):
        """Перерисовка читает вопрос из блока — счётчик на счётчик не ложится."""
        reply = discovery.render_multiselect_clarification(_QUESTION, _OPTIONS, mask=0b1)
        assert reply.action_data is not None
        assert reply.action_data["clarification"]["question"] == _QUESTION
        assert "Выбрано" not in reply.action_data["clarification"]["question"]

    def test_nothing_ticked_has_no_count_line(self):
        reply = discovery.render_multiselect_clarification(_QUESTION, _OPTIONS, mask=0)
        assert reply.text == _QUESTION
        assert "Выбрано" not in reply.text
