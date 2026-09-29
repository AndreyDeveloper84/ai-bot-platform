"""DRF-2138 — ручной ориентир доезжает до навыка на ГЛОБАЛЬНОМ пути.

Ревью #1907: на глобальном (пилотном) пути свободный текст структурен только
когда бот сам задал вопрос (:func:`is_structured_nutrition_turn`); новый
открытый вопрос «Сколько ккал…» и детерминированная фраза «мне врач назначил
1800 ккал» в перечень не входили — после первого тапа число человека отвечал
бы консьерж, а фраза была мёртвым кодом.

* g1 — предикат: открытый вопрос ручного ориентира (свежий) → структурно;
  без него голое «1800» — нет (присутствие раньше отсутствия);
* g2 — предикат: фраза с числом и «ккал» → структурно; «1800 ккал» без
  специалиста / «врач сказал 1800» без «ккал» — нет;
* g3 — узел через диспетчер: тап кнопки → «1800» → подтвердить → один POST,
  ответ навыка, не консьержа;
* g4 — узел через диспетчер: «/anketa» на шаге числа не застревает —
  открытый вопрос снят, анкета начинается.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.orchestrator.nutrition_global import (
    is_structured_nutrition_turn,
    try_handle_structured_nutrition_turn,
)
from apps.skills.nutrition_anketa.skill import (
    MANUAL_CONFIRM_CALLBACK,
    MANUAL_STATE_KEY,
    MANUAL_TARGET_CALLBACK,
)
from apps.skills.nutrition_anketa.tests.test_manual_target_2138 import _manual_profile

_PD_OPEN = "apps.orchestrator.personal_surface.personal_records_consent_open"
_M_GRANTED = "apps.consent.personal_calculation.is_granted"


@pytest.fixture
def nutrition_on(settings):  # noqa: ANN001, ANN201
    settings.NUTRITION_ENABLED = True
    return settings


def _conversation(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=1, skill_state=dict(state or {}))


def _structured(text: str, conversation: SimpleNamespace) -> bool:
    return is_structured_nutrition_turn(text=text, has_attachments=False, conversation=conversation)


class TestG1PendingQuestionIsStructured:
    def test_fresh_question_owns_the_number(self) -> None:
        from datetime import UTC, datetime

        conv = _conversation(
            {MANUAL_STATE_KEY: {"step": "kcal", "asked_at": datetime.now(UTC).isoformat()}}
        )
        assert _structured("1800", conv)
        assert not _structured("1800", _conversation())


class TestG2PhraseIsStructured:
    def test_the_deterministic_phrase(self) -> None:
        assert _structured("мне врач назначил 1800 ккал", _conversation())
        assert not _structured("1800 ккал", _conversation())
        assert not _structured("врач сказал 1800", _conversation())


class _Catalogue:
    def __init__(self) -> None:
        self.posted: list[dict] = []
        client = Mock()

        async def _set_manual(**kwargs):
            self.posted.append(kwargs)
            return _manual_profile(kwargs["calories_kcal"]), {"warnings": []}

        client.set_manual_targets = _set_manual
        self.client = client


def _turn(text: str, conversation: SimpleNamespace, catalogue: _Catalogue):
    with (
        patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client", return_value=catalogue.client
        ),
        patch(_PD_OPEN, return_value=True),
        patch(_M_GRANTED, return_value=True),
    ):
        return try_handle_structured_nutrition_turn(
            text=text,
            attachments=None,
            bot_user=Mock(channel="max", channel_user_id="2138", pk=7),
            conversation=conversation,
            trace_id="t-2138",
        )


@pytest.mark.django_db
class TestG3ThroughTheDispatcher:
    def test_button_number_confirm(self, nutrition_on) -> None:  # noqa: ANN001
        catalogue = _Catalogue()
        conv = _conversation()
        asked = _turn(MANUAL_TARGET_CALLBACK, conv, catalogue)
        assert asked is not None and asked.meta["reply_kind"] == "anketa_manual_target_ask"

        card = _turn("1800", conv, catalogue)
        assert card is not None, "число человека ушло бы консьержу"
        assert card.meta["reply_kind"] == "anketa_manual_target_card"
        assert catalogue.posted == []

        done = _turn(MANUAL_CONFIRM_CALLBACK, conv, catalogue)
        assert done is not None and done.meta["reply_kind"] == "anketa_manual_target_done"
        assert [p["calories_kcal"] for p in catalogue.posted] == [1800]

    def test_phrase_goes_to_the_card(self, nutrition_on) -> None:  # noqa: ANN001
        catalogue = _Catalogue()
        card = _turn("мне врач назначил 1800 ккал", _conversation(), catalogue)
        assert card is not None and card.meta["reply_kind"] == "anketa_manual_target_card"


@pytest.mark.django_db
class TestG4AnketaIsNotTrapped:
    def test_slash_anketa_on_the_number_step_starts_the_anketa(self, nutrition_on) -> None:  # noqa: ANN001
        catalogue = _Catalogue()
        conv = _conversation()
        _turn(MANUAL_TARGET_CALLBACK, conv, catalogue)
        assert MANUAL_STATE_KEY in conv.skill_state
        result = _turn("/anketa", conv, catalogue)
        assert result is not None
        assert result.meta["reply_kind"] != "anketa_manual_target_kcal_invalid"
        assert MANUAL_STATE_KEY not in conv.skill_state
        assert result.action_type == "anketa_step_gender"
