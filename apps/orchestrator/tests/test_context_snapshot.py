"""Снимок контекста хода (DRF-1903, срез 6.1 окна «Мозг»).

Условия главного окна 15.09, каждое своим тестом:
* только коды / ссылки / закрытые словари — ``TestCodesOnly``;
* без текста реплик и без смыслов здоровья — ``TestNoTextNoHealth``;
* стирание существующими путями (после «забудь всё» сказанного нет) — ``TestErasure``;
* digest — канонический и стабильный — ``TestDigest``.
"""

from __future__ import annotations

import pytest

from apps.orchestrator import context_snapshot as cs
from apps.orchestrator import said_memory
from apps.orchestrator.decision_readiness.safety_input import Handoff, SafetyResult, SafetyState
from apps.orchestrator.decision_readiness.state import ConversationState
from apps.orchestrator.open_question import close_question, open_question
from apps.orchestrator.tests.test_memory_erasure_matrix import _bot_user, _consents, _forget_all
from apps.orchestrator.tests.test_memory_erasure_matrix import (
    ayla as ayla,  # noqa: F811 — фикстура pytest
)

pytestmark = pytest.mark.django_db(transaction=True)

OWNER_FIRST_TURN = (
    "ты что-нибудь можешь мне посоветовать или предложить? "
    "я хочв расслабиться вечером и у меня ноет спина"
)
OWNER_SCREENING_ANSWER = "1. Спина, 2. После работы"
SHOW_MASTERS_PENZA = ({"tool": "show_masters", "arguments": {"city": "Пенза"}},)


@pytest.fixture(autouse=True)
def _known_cities(monkeypatch):
    monkeypatch.setattr("apps.marketplace.discovery._known_cities", lambda: ["Пенза", "Самара"])


_UID = iter(range(79501, 79999))


def _person(settings):
    from apps.conversations.services import resolve_active_global_conversation

    bot_user = _bot_user(f"snap-{next(_UID)}")
    bot_user.ayla_user_id_is_proxy = False
    bot_user.save(update_fields=["ayla_user_id_is_proxy"])
    _consents(bot_user, settings)
    return bot_user, resolve_active_global_conversation(bot_user)


def _dr_state(conversation, *, revision=7) -> ConversationState:
    return ConversationState(
        conversation_id=str(conversation.id),
        revision=revision,
        safety=SafetyResult(
            state=SafetyState.NORMAL, evaluated_at_revision=revision, handoff=Handoff.NONE
        ),
    )


def _build(bot_user, conversation, **kwargs):
    return cs.build_turn_snapshot(
        bot_user=bot_user,
        conversation=conversation,
        dr_state=kwargs.pop("dr_state", _dr_state(conversation)),
        readiness_state=kwargs.pop("readiness_state", "blocked"),
    )


class TestOwnerScenario:
    def test_first_turn_and_screening_answer_give_codes_only(self, settings):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(bot_user, conversation, OWNER_FIRST_TURN)
        said_memory.record_said_facts(
            bot_user, conversation, "массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
        )
        open_question(conversation, "health_screening.soft", asked_text="Где болит? Когда?")
        close_question(conversation, OWNER_SCREENING_ANSWER)
        conversation.refresh_from_db()

        snap = _build(bot_user, conversation)

        assert snap.snapshot_version == cs.SNAPSHOT_VERSION
        assert snap.content["decision_readiness"] == {
            "state_revision": 7,
            "readiness_state": "blocked",
        }
        # Слово главного окна 15.09: безопасность — только safety_evaluation_ref
        # записи, в снимке её нет; скрининговый вопрос — смысл здоровья, id не берётся.
        assert set(snap.content) == {
            "snapshot_version",
            "decision_readiness",
            "said",
            "answered_question",
        }
        assert snap.content["answered_question"] is None
        said = {row["key"]: row["value"] for row in snap.content["said"]}
        assert said == {"city": "Пенза", "visit_context": "evening"}
        assert all(row["origin"] == "conversation" for row in snap.content["said"])


class TestNoTextNoHealth:
    def test_screening_question_leaves_neither_id_nor_answer(self, settings):
        """Главное окно 15.09: сам факт вопроса о здоровье — смысл здоровья."""
        bot_user, conversation = _person(settings)
        open_question(conversation, "health_screening.soft", asked_text="Где именно болит?")
        close_question(conversation, OWNER_SCREENING_ANSWER)
        conversation.refresh_from_db()

        snap = _build(bot_user, conversation)

        blob = str(snap.content).lower()
        assert snap.content["decision_readiness"]["state_revision"] == 7
        assert snap.content["answered_question"] is None
        for word in ("health", "screening", "спин", "после работы", "болит", "где именно"):
            assert word not in blob

    @pytest.mark.parametrize(
        "question_id", ["health_screening.soft", "screening.v2", "safety_clarify", "wellness_sleep"]
    )
    def test_health_class_is_recognised_by_prefix(self, settings, question_id):
        bot_user, conversation = _person(settings)
        open_question(conversation, question_id, asked_text="?")
        close_question(conversation, "ответ")
        conversation.refresh_from_db()

        # empty-assert-ok: вопрос класса здоровья не берётся по построению; соседний тест показывает, что обычный вопрос берётся
        assert _build(bot_user, conversation).content["answered_question"] is None

    def test_ordinary_question_id_is_kept_without_its_text(self, settings):
        bot_user, conversation = _person(settings)
        open_question(conversation, "ask_clarification", asked_text="Какой массаж?")
        close_question(conversation, "Расслабляющий")
        conversation.refresh_from_db()

        snap = _build(bot_user, conversation)

        assert snap.content["answered_question"] == {"question_id": "ask_clarification"}
        blob = str(snap.content).lower()
        assert "какой массаж" not in blob and "расслабляющий" not in blob

    def test_symptom_turn_leaves_no_health_word(self, settings):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(bot_user, conversation, OWNER_FIRST_TURN)

        snap = _build(bot_user, conversation)

        blob = str(snap.content).lower()
        assert "evening" in blob
        assert "ноет" not in blob and "спин" not in blob


class TestCodesOnly:
    @pytest.mark.parametrize(
        "value",
        [
            {"said": [{"key": "city", "value": "Москва"}]},  # не из закрытого словаря
            {"answered_question": {"question_id": "где болит"}},  # текст с пробелом
            {"safety": {"rule_id": "Сейчас проверю"}},
            {"free text key": 1},
        ],
    )
    def test_text_is_rejected(self, value):
        with pytest.raises(cs.SnapshotRejected):
            cs.assert_codes_only(value, closed=frozenset({"Пенза"}))

    @pytest.mark.parametrize(
        "value",
        [
            {"said": [{"key": "city", "value": "Пенза", "said_on": "2026-09-14"}]},
            {"safety": {"rule_id": "pre_check:clarify", "evaluated_at_revision": 3}},
            {"trace": "7f1d0f2e-0000-4000-8000-000000000001", "flag": True, "none": None},
        ],
    )
    def test_codes_dates_and_closed_values_pass(self, value):
        cs.assert_codes_only(value, closed=frozenset({"Пенза"}))

    def test_builder_refuses_text_that_reached_a_fact(self, settings, monkeypatch):
        bot_user, conversation = _person(settings)
        monkeypatch.setattr(
            said_memory,
            "said_facts",
            lambda _bu: [said_memory.SaidFact(key="city", value="где-то у метро", said_at=None)],
        )
        with pytest.raises(cs.SnapshotRejected):
            _build(bot_user, conversation)


class TestDigest:
    def test_same_facts_same_digest_regardless_of_key_order(self):
        a = {"b": 1, "a": {"y": "x", "x": [1, 2]}}
        b = {"a": {"x": [1, 2], "y": "x"}, "b": 1}
        assert cs.content_digest(a) == cs.content_digest(b)
        assert len(cs.content_digest(a)) == 64

    def test_a_changed_fact_changes_the_digest(self, settings):
        bot_user, conversation = _person(settings)
        before = _build(bot_user, conversation).content_digest
        said_memory.record_said_facts(
            bot_user, conversation, "массаж в Пензе", tool_trace=SHOW_MASTERS_PENZA
        )
        after = _build(bot_user, conversation).content_digest
        assert before != after
        assert _build(bot_user, conversation).content_digest == after


class TestErasure:
    def test_forget_all_leaves_no_said_facts_in_the_snapshot(self, settings, ayla):
        bot_user, conversation = _person(settings)
        said_memory.record_said_facts(
            bot_user,
            conversation,
            "хочу массаж в Пензе после работы",
            tool_trace=SHOW_MASTERS_PENZA,
        )
        assert _build(bot_user, conversation).content["said"]

        _forget_all(bot_user)

        # empty-assert-ok: после «забудь всё» гейт said_facts закрыт по построению; присутствие доказано строкой выше
        assert _build(bot_user, conversation).content["said"] == []


class TestSaidVocabularyByKey:
    """DRF-1911: «похоже на код» — не сторож смысла; said[] сверяется по ключу."""

    CITIES = frozenset({"Пенза", "Самара"})

    @pytest.mark.parametrize(
        "rows",
        [
            [{"key": "visit_context", "value": "pregnant"}],
            [{"key": "city", "value": "back_pain"}],
            [{"key": "city", "value": "Penza"}],
            [{"key": "health", "value": "evening"}],
        ],
    )
    def test_value_or_key_outside_the_vocabulary_is_rejected(self, rows):
        with pytest.raises(cs.SnapshotRejected):
            cs.assert_said_in_vocabulary(rows, cities=self.CITIES)

    def test_the_writers_own_values_pass(self):
        cs.assert_said_in_vocabulary(
            [{"key": "city", "value": "Пенза"}, {"key": "visit_context", "value": "evening"}],
            cities=self.CITIES,
        )
        cs.assert_said_in_vocabulary([], cities=self.CITIES)

    def test_visit_vocabulary_is_the_writers_rule_codes(self):
        """Один источник: словарь сторожа — ровно коды, которые пишет память сказанного."""
        assert set(said_memory.VISIT_CONTEXT_LABELS) == {
            code for code, _rule in said_memory._VISIT_RULES
        }

    def test_builder_refuses_a_code_shaped_word_outside_the_vocabulary(self, settings, monkeypatch):
        """«pregnant» проходит форму кода — сборщик обязан отказать по словарю ключа."""
        bot_user, conversation = _person(settings)
        assert cs._CODE_RE.match("pregnant")
        monkeypatch.setattr(
            said_memory,
            "said_facts",
            lambda _bu: [said_memory.SaidFact(key="visit_context", value="pregnant", said_at=None)],
        )
        with pytest.raises(cs.SnapshotRejected):
            _build(bot_user, conversation)


class TestHealthClassMirrorsCatalog:
    """DRF-1913: класс здоровья по правилу каталога DRF-1906 — любой сегмент, без регистра."""

    @pytest.mark.parametrize(
        "code",
        [
            "said.health_note",
            "Health_Status",
            "open:screening-2",
            "screenings_v2",
            "screening",
            "safety_check",
            "said.health_x",
            "a/wellness_sleep",
        ],
    )
    def test_segment_prefix_is_a_health_class(self, code):
        assert cs.health_prefix(code) is not None

    @pytest.mark.parametrize(
        "code", ["xhealth_y", "said.xhealth_y", "safetynet", "ask_clarification", "said.city"]
    )
    def test_substring_or_ordinary_code_is_not(self, code):
        # empty-assert-ok: не класс здоровья по построению; соседний параметризованный тест доказывает, что класс распознаётся
        assert cs.health_prefix(code) is None

    def test_the_rule_mirrors_the_catalog_by_name(self):
        """Зеркало recommendation/snapshots.py DRF-1906: HEALTH_PREFIXES, _SEGMENT_RE, _QUESTION_ID_RE."""
        assert cs.HEALTH_QUESTION_PREFIXES == ("health_", "screening", "safety_", "wellness_")
        assert cs._SEGMENT_RE.pattern == r"[.:/\-]"
        assert cs._QUESTION_ID_RE.pattern == r"^[A-Za-z0-9_.:/\-]{1,64}$"

    @pytest.mark.parametrize(
        "question_id", ["said.health_x", "Health_x", "a/wellness_sleep", "x-Screening.v2"]
    )
    def test_builder_drops_a_health_class_question(self, settings, question_id):
        bot_user, conversation = _person(settings)
        open_question(conversation, question_id, asked_text="?")
        close_question(conversation, "ответ")
        conversation.refresh_from_db()

        # empty-assert-ok: вопрос класса здоровья не берётся по построению; test_ordinary_question_id_is_kept_without_its_text доказывает, что обычный берётся
        assert _build(bot_user, conversation).content["answered_question"] is None

    def test_guard_rejects_health_class_long_id_and_extra_keys(self):
        cs.assert_answered_question(None)
        cs.assert_answered_question({"question_id": "said.city"})
        for bad in (
            {"question_id": "said.health_x"},
            {"question_id": "q" * 65},
            {"question_id": "ask_clarification", "asked_text": "?"},
        ):
            with pytest.raises(cs.SnapshotRejected):
                cs.assert_answered_question(bad)

    def test_builder_refuses_a_question_id_longer_than_the_catalog_allows(self, settings):
        bot_user, conversation = _person(settings)
        open_question(conversation, "q" * 65, asked_text="?")
        close_question(conversation, "ответ")
        conversation.refresh_from_db()

        with pytest.raises(cs.SnapshotRejected):
            _build(bot_user, conversation)
