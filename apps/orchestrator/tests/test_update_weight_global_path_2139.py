"""DRF-2139 — «обнови вес» доезжает до навыка на ГЛОБАЛЬНОМ пути.

Как у DRF-2138 (ревью #1907): свободный текст структурен только когда бот
сам задал вопрос или фраза детерминирована. Здесь — открытый вопрос веса и
фразы «мой вес N» / «вешу N» / «обнови вес».

* g1 — предикат: открытый вопрос веса (свежий) → «68» структурно; без него
  голое «68» — нет;
* g2 — предикат: «мой вес 65» / «обнови вес» структурно; «68» / «весна» —
  нет;
* g3 — узел через диспетчер: «обнови вес» → «68» → карточка предложения,
  один POST, ответ навыка.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from apps.consent.personal_calculation import ConsentAttestation
from apps.orchestrator.nutrition_global import (
    is_structured_nutrition_turn,
    try_handle_structured_nutrition_turn,
)
from apps.skills.nutrition_anketa.skill import UPDATE_WEIGHT_STATE_KEY
from apps.skills.nutrition_anketa.tests.test_skill import _ATTESTATION_LOOKUP
from apps.skills.nutrition_anketa.tests.test_update_weight_2139 import (
    _calculated,
    _proposed,
)

_PD_OPEN = "apps.orchestrator.personal_surface.personal_records_consent_open"
_ATTESTATION = ConsentAttestation(
    type="personal_calculation", document_version="personal-calculation-v1"
)


@pytest.fixture
def nutrition_on(settings):  # noqa: ANN001, ANN201
    settings.NUTRITION_ENABLED = True
    return settings


def _conversation(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=1, skill_state=dict(state or {}))


def _structured(text: str, conversation: SimpleNamespace) -> bool:
    return is_structured_nutrition_turn(text=text, has_attachments=False, conversation=conversation)


class TestG1PendingWeightQuestion:
    def test_fresh_question_owns_the_number(self) -> None:
        conv = _conversation(
            {UPDATE_WEIGHT_STATE_KEY: {"step": "weight", "asked_at": datetime.now(UTC).isoformat()}}
        )
        assert _structured("68", conv)
        assert not _structured("68", _conversation())


class TestG2Phrase:
    def test_the_deterministic_phrases(self) -> None:
        assert _structured("мой вес 65", _conversation())
        assert _structured("обнови вес", _conversation())
        assert not _structured("68", _conversation())
        assert not _structured("весна пришла", _conversation())


class _Catalogue:
    def __init__(self) -> None:
        self.posted: list[dict] = []
        client = Mock()

        async def _get_profile(**kwargs):
            return _calculated()

        async def _upsert(**kwargs):
            self.posted.append(kwargs)
            return _proposed(kwargs["data"]["weight_kg"])

        client.get_profile = _get_profile
        client.upsert_profile = _upsert
        self.client = client


def _turn(text: str, conversation: SimpleNamespace, catalogue: _Catalogue):
    with (
        patch(
            "apps.skills.nutrition_anketa.skill.get_nutrition_client", return_value=catalogue.client
        ),
        patch(_PD_OPEN, return_value=True),
        patch(_ATTESTATION_LOOKUP, return_value=_ATTESTATION),
    ):
        return try_handle_structured_nutrition_turn(
            text=text,
            attachments=None,
            bot_user=Mock(channel="max", channel_user_id="2139", pk=7),
            conversation=conversation,
            trace_id="t-2139",
        )


@pytest.mark.django_db
class TestG3ThroughTheDispatcher:
    def test_phrase_number_card(self, nutrition_on) -> None:  # noqa: ANN001
        catalogue = _Catalogue()
        conv = _conversation()
        asked = _turn("обнови вес", conv, catalogue)
        assert asked is not None and asked.meta["reply_kind"] == "anketa_update_weight_ask"
        card = _turn("68", conv, catalogue)
        assert card is not None, "число человека ушло бы консьержу"
        assert card.meta["reply_kind"] == "anketa_update_weight_proposed"
        assert [p["data"]["weight_kg"] for p in catalogue.posted] == [68]
