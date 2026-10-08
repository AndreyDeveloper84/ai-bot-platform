"""DRF-2885 — сборка и сохранение плана нового механизма из хода разговора.

Решение владельца 08.10: план создаётся в чате бота и в чате Mini App. Это
путь сквозной проверки на подготовленных данных: вход — временная команда,
на экране только подписи каталога, три фразы владельца (лист 07.10, п.15) и
имена исходов с пометкой «тест».

Вход
* v1 — команда отвечает только аккаунту из серверного списка при включённом
  механизме; остальным текст идёт дальше как обычный;
* v2 — пустой список (умолчание) — входа нет ни у кого.

Сборка
* s1 — каталогу уходит тройка ЭТОГО хода; карточка — подписи каталога и
  вопрос владельца, кнопка «Сохранить»;
* s2 — нет тройки — каталог не спрашивается вовсе;
* s3 — исход без плана показан именем с пометкой, предложение не остаётся;
* s4 — шаг без подписи: ни карточки, ни сохранения;
* s5 — ключ способности на экран не попадает.

Сохранение
* c1 — команда: решение без правок, подтверждение, тройка хода ПОДТВЕРЖДЕНИЯ;
* c2 — нет тройки — каталог не спрашивается;
* c3 — чужая или устаревшая кнопка — отказ по имени, каталог не спрашивается;
* c4 — повторное нажатие шлёт ту же команду (дубля не будет — узнаёт каталог);
* c5 — отказы каталога названы по имени.

Граница, которую каталог проверить не может
* g1 — вопрос «уточнить» открыт в слоте — уходит в ``restrictions`` при
  вердикте «норма» у хода с кнопкой;
* g2 — слот истёк, но пометка рядом с предложением осталась — уходит так же;
* g3 — вопроса нет — ``restrictions`` в команде нет;
* g4 — вердикт «уточнить» без вопроса — не шлём: «уточнить» без названного
  вопроса не держится.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from apps.integrations.ayla import plan_engine_client as client_mod
from apps.orchestrator import open_question, plan_engine_card as card
from apps.orchestrator.nutrition_global import try_handle_structured_nutrition_turn
from apps.orchestrator.safety.plan_turn import PlanTurnSafety

ACCOUNT = "max:770001"
DECISION_ID = "0f3a9c2e-1111-4222-8333-444455556666"
TOKEN = "0f3a9c2e"
POLICY_VERSIONS = {"plan_spec_version": "1", "safety_policy_version": "pre_check-abc"}
STEP_A = {"step_id": "s-a", "capability_ref": "cap.sleep_routine", "level": "CAPABILITY"}
STEP_B = {"step_id": "s-b", "capability_ref": "cap.evening_walk", "level": "CAPABILITY"}
LABELS = {"cap.sleep_routine": "Режим сна", "cap.evening_walk": "Вечерняя прогулка"}


def _decision() -> dict[str, Any]:
    return {
        "decision_id": DECISION_ID,
        "goal_ref": "9b2d0000-0000-4000-8000-000000000001",
        "steps": [STEP_A, STEP_B],
        "assertions": [{"assertion_id": "a-1"}],
        "validation": {
            "status": "INCOMPLETE",
            "step_validations": {"s-a": "VALID", "s-b": "VALID"},
        },
        "policy_versions": POLICY_VERSIONS,
        "safety_state": "NORMAL",
    }


class FakeCatalog:
    """То, что ушло бы каталогу. Пусто — каталог не спрашивали."""

    def __init__(self) -> None:
        self.composed: list[dict[str, Any]] = []
        self.saved: list[dict[str, Any]] = []
        self.outcome: dict[str, Any] = {"outcome": "PLAN", "decision": _decision()}
        self.labels: dict[str, str] = dict(LABELS)
        self.save_error: Exception | None = None

    def compose_decision(self, **kwargs: Any) -> dict[str, Any]:
        self.composed.append(kwargs)
        return self.outcome

    def capability_labels(self, *, external_user_id: str, keys: list[str]) -> dict[str, str]:
        return {k: v for k, v in self.labels.items() if k in keys}

    def save_plan(self, *, external_user_id: str, command: dict[str, Any]) -> dict[str, Any]:
        self.saved.append(command)
        if self.save_error is not None:
            raise self.save_error
        return {"plan": {}, "created": len(self.saved) == 1}


@pytest.fixture(autouse=True)
def catalog(monkeypatch: pytest.MonkeyPatch) -> FakeCatalog:
    fake = FakeCatalog()
    monkeypatch.setattr(client_mod, "PlanEngineHttpClient", lambda: fake)
    monkeypatch.setattr(
        "apps.integrations.ayla.external_user_id_for", lambda bot_user: "bot:max:770001"
    )

    def write(conversation: Any, subkey: str, value: Any) -> None:
        if value is None:
            conversation.skill_state.pop(subkey, None)
        else:
            conversation.skill_state[subkey] = value

    monkeypatch.setattr(open_question, "write_conversation_state", write)
    return fake


@pytest.fixture(autouse=True)
def _on(settings) -> None:
    settings.PLAN_ENGINE_ENABLED = True
    settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = (ACCOUNT,)


def _bot_user(account: str = ACCOUNT) -> SimpleNamespace:
    channel, channel_user_id = account.split(":")
    return SimpleNamespace(pk=1, id=1, channel=channel, channel_user_id=channel_user_id)


def _conversation() -> SimpleNamespace:
    return SimpleNamespace(id="conv-2885", skill_state={})


def _safety(state: str = "NORMAL", revision: int = 7) -> PlanTurnSafety:
    return PlanTurnSafety(
        safety_state=state, safety_policy_version="pre_check-abc", evaluated_at_revision=revision
    )


def _turn(text: str, conversation: Any, *, safety: Any = "default", bot_user: Any = None):
    provider = (lambda: _safety()) if safety == "default" else (lambda: safety)
    return try_handle_structured_nutrition_turn(
        text=text,
        attachments=None,
        bot_user=bot_user or _bot_user(),
        conversation=conversation,
        trace_id="t",
        plan_turn_safety=provider,
    )


def _proposed(conversation: Any) -> None:
    result = _turn(card.TRIGGER, conversation)
    assert result is not None and result.action_type == "plan_engine_proposal"


SAVE = f"cb:plan:save:{TOKEN}"


# ─── вход ────────────────────────────────────────────────────────────────


class TestTheEntryIsVisibleOnlyToListedAccounts:
    def test_v1_a_listed_account_gets_the_card(self) -> None:
        assert _turn(card.TRIGGER, _conversation()).action_type == "plan_engine_proposal"

    def test_v1_another_account_is_not_answered(self, catalog: FakeCatalog) -> None:
        result = card.try_handle_plan_trigger(
            text=card.TRIGGER,
            bot_user=_bot_user("max:999"),
            conversation=_conversation(),
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.composed == []

    def test_v1_the_engine_switched_off_is_not_answered(
        self, settings, catalog: FakeCatalog
    ) -> None:
        settings.PLAN_ENGINE_ENABLED = False

        assert card.trigger_visible(_bot_user()) is False
        assert catalog.composed == []
        # Положительная пара: включённый механизм тому же аккаунту отвечает.
        settings.PLAN_ENGINE_ENABLED = True
        assert card.trigger_visible(_bot_user()) is True

    def test_v2_an_empty_list_means_nobody(self, settings) -> None:
        settings.SYNTHETIC_TEST_TRIGGER_ACCOUNTS = ()

        assert card.trigger_visible(_bot_user()) is False

    def test_v1_only_the_exact_command(self, catalog: FakeCatalog) -> None:
        result = card.try_handle_plan_trigger(
            text="составь мне план",
            bot_user=_bot_user(),
            conversation=_conversation(),
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.composed == []


# ─── сборка ──────────────────────────────────────────────────────────────


class TestComposing:
    def test_s1_the_turn_triple_goes_to_the_catalog_and_the_card_is_catalog_words(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()

        result = _turn(card.TRIGGER, conversation, safety=_safety("NORMAL", 7))

        sent = catalog.composed[0]
        assert (sent["safety_state"], sent["safety_policy_version"]) == ("NORMAL", "pre_check-abc")
        assert sent["external_user_id"] == "bot:max:770001"
        assert sent["rules_registry"]["rules"]  # настоящий реестр, не пустой
        assert result.reply_text == (
            "• Режим сна\n• Вечерняя прогулка\n\nСохранить выбранные шаги в мой план?"
        )
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert buttons == [{"label": "Сохранить", "callback": SAVE}]

    def test_s2_no_triple_no_request(self, catalog: FakeCatalog) -> None:
        result = _turn(card.TRIGGER, _conversation(), safety=None)

        assert catalog.composed == []
        assert result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"

    @pytest.mark.parametrize(
        "outcome", ["SAFETY_BLOCKED", "NO_GOAL", "NO_CURATED_DECOMPOSITION", "CLARIFY_PENDING"]
    )
    def test_s3_an_outcome_without_a_plan_is_shown_by_name(
        self, catalog: FakeCatalog, outcome: str
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)  # прежнее предложение ждало ответа
        catalog.outcome = {"outcome": outcome, "decision": None, "details": {}}

        result = _turn(card.TRIGGER, conversation)

        assert result.reply_text == f"{outcome} · тест"
        assert result.action_data is None
        assert card.STATE_KEY not in conversation.skill_state  # сохранять больше нечего

    def test_s4_a_step_without_a_label_is_neither_shown_nor_saveable(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        catalog.labels = {"cap.sleep_routine": "Режим сна"}

        result = _turn(card.TRIGGER, conversation)

        assert result.reply_text == "PLAN_STEP_UNLABELLED · тест"
        assert card.STATE_KEY not in conversation.skill_state
        # Положительная пара: с обеими подписями тот же ответ каталога — карточка.
        catalog.labels = dict(LABELS)
        assert _turn(card.TRIGGER, conversation).action_type == "plan_engine_proposal"

    def test_s5_capability_keys_never_reach_the_screen(self) -> None:
        result = _turn(card.TRIGGER, _conversation())

        assert "Режим сна" in result.reply_text
        assert "cap." not in result.reply_text


# ─── сохранение ──────────────────────────────────────────────────────────


class TestSaving:
    def test_c1_the_command_is_the_decision_untouched_plus_the_confirming_turn(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _turn(card.TRIGGER, conversation, safety=_safety("NORMAL", 7))

        result = _turn(SAVE, conversation, safety=_safety("NORMAL", 9))

        assert result.reply_text == "PLAN_SAVED · тест"
        decision = _decision()
        assert catalog.saved == [
            {
                "decision_id": DECISION_ID,
                "goal_ref": decision["goal_ref"],
                "confirmation": {
                    "question_id": "plan.save_confirm",
                    "option_id": "save",
                    "state_revision": 9,
                },
                "provenance": {"policy_versions": POLICY_VERSIONS},
                "decision": {
                    "steps": decision["steps"],
                    "assertions": decision["assertions"],
                    "validation": decision["validation"],
                },
                "safety_state": "NORMAL",
                "safety_policy_version": "pre_check-abc",
                # Ревизия хода ПОДТВЕРЖДЕНИЯ, а не хода сборки (7).
                "evaluated_at_revision": 9,
            }
        ]

    def test_c2_no_triple_no_request(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(SAVE, conversation, safety=None)

        assert catalog.saved == []
        assert result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"

    @pytest.mark.parametrize("button", ["cb:plan:save:deadbeef", SAVE])
    def test_c3_a_foreign_or_stale_button_is_refused_by_name(
        self, catalog: FakeCatalog, button: str
    ) -> None:
        conversation = _conversation()
        if button != SAVE:
            _proposed(conversation)  # предложение есть, но кнопка не от него

        result = _turn(button, conversation)

        assert catalog.saved == []
        assert result.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"

    def test_c3_another_account_is_not_answered(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = card.try_handle_plan_save(
            text=SAVE,
            bot_user=_bot_user("max:999"),
            conversation=conversation,
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.saved == []

    def test_c4_a_second_tap_sends_the_same_command(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        first = _turn(SAVE, conversation, safety=_safety("NORMAL", 9))
        second = _turn(SAVE, conversation, safety=_safety("NORMAL", 9))

        assert (first.reply_text, second.reply_text) == ("PLAN_SAVED · тест", "PLAN_SAVED · тест")
        assert len(catalog.saved) == 2
        assert catalog.saved[0] == catalog.saved[1]  # дубль отсекает каталог по этой команде

    @pytest.mark.parametrize(
        ("error", "name"),
        [
            (client_mod.PlanSaveSafetyBlockedError("x"), "PLAN_SAVE_SAFETY_BLOCKED"),
            (client_mod.PlanIdempotencyConflictError("x"), "PLAN_IDEMPOTENCY_CONFLICT"),
            (client_mod.PlanGoalNotFoundError("x"), "GOAL_NOT_FOUND"),
            (
                client_mod.PlanEngineContractError("clarify_without_restriction"),
                "PLAN_CONTRACT_VIOLATION",
            ),
            (client_mod.PlanEngineUnavailableError("x"), "PLAN_ENGINE_UNAVAILABLE"),
        ],
    )
    def test_c5_catalog_refusals_are_named(
        self, catalog: FakeCatalog, error: Exception, name: str
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)
        catalog.save_error = error

        result = _turn(SAVE, conversation)

        assert result.reply_text == f"{name} · тест"


# ─── граница, которую каталог не видит ───────────────────────────────────

RESTRICTION = [{"scope": "PLAN", "cause": "SAFETY_CLARIFY", "question_id": "plan.safety_clarify"}]


def _open_clarify_slot(conversation: Any) -> None:
    from django.utils import timezone

    conversation.skill_state[open_question.STATE_KEY] = {
        "question_id": card.CLARIFY_QUESTION_ID,
        "asked_text": "вопрос",
        "at": timezone.now().isoformat(),
        "binding": True,
    }


class TestAQuestionRaisedWhileTheProposalWaitedIsSavedAsARestriction:
    def test_g1_an_open_slot_goes_into_restrictions_under_a_normal_verdict(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _open_clarify_slot(conversation)
        assert open_question.pending_question(conversation) is not None  # слот действительно открыт

        _turn(SAVE, conversation, safety=_safety("NORMAL", 9))

        assert catalog.saved[0]["restrictions"] == RESTRICTION
        assert catalog.saved[0]["safety_state"] == "NORMAL"

    def test_g2_the_mark_beside_the_proposal_outlives_the_slot(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        card.note_clarify_opened(conversation)
        assert (
            open_question.pending_question(conversation) is None
        )  # слота нет: истёк или не открывался

        _turn(SAVE, conversation, safety=_safety("NORMAL", 9))

        assert catalog.saved[0]["restrictions"] == RESTRICTION

    def test_g3_no_question_no_restrictions_key(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        _turn(SAVE, conversation, safety=_safety("NORMAL", 9))

        assert len(catalog.saved) == 1  # команда ушла — и в ней ограничений нет
        assert "restrictions" not in catalog.saved[0]

    def test_g4_clarify_without_a_named_question_is_not_sent(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(SAVE, conversation, safety=_safety("CLARIFY", 9))

        assert catalog.saved == []
        assert result.reply_text == "CLARIFY_PENDING · тест"
        # Положительная пара: с названным вопросом тот же вердикт сохраняется.
        _open_clarify_slot(conversation)
        _turn(SAVE, conversation, safety=_safety("CLARIFY", 9))
        assert catalog.saved[0]["restrictions"] == RESTRICTION

    def test_g2_a_new_proposal_starts_without_the_old_mark(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        card.note_clarify_opened(conversation)
        assert conversation.skill_state[card.STATE_KEY]["clarify_open"] is True

        _proposed(conversation)  # новая сборка — новое предложение

        assert conversation.skill_state[card.STATE_KEY]["clarify_open"] is False
