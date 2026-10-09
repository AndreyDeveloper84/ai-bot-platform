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
* c4 — повторное нажатие в следующем ходе — то же подтверждение: ревизия в ключе
  идемпотентности каталога не меняется, меняется только ревизия вердикта;
* c5 — отказы каталога названы по имени.

Вердикт «уточнить»
* g1 — тройка с «уточнить» уходит как есть: решает каталог;
* g2 — ограничений карточка не шлёт (универсального вопроса нет).

«Изменить» (шаг 3)
* i1–i9 — «Изменить» показывает шаги кнопками и ничего не меняет; тап по шагу
  пересобирает план без него; убранное остаётся убранным; частичное принятие
  сохраняет оставшееся; кнопки старой карточки устаревают; без этого шага
  плана нет — прежнее предложение остаётся в силе.

«Мой план»
* m1–m5 — сохранённый план показан подписями каталога, без вердикта и без
  сборки; нет плана — фраза идёт дальше.
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
PROPOSAL_ID = "7c1d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff"
ACTIVE_ID = "5a5a5a5a-1111-4222-8333-999999999999"
REPLACE = "cb:plan:replace:7c1d2e3f"
KEEP = "cb:plan:keep:7c1d2e3f"
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
        #: «Зачем шаг» — ожидаемый эффект способности; по умолчанию каталог его не отдаёт.
        self.effects: dict[str, str] = {}
        self.save_error: Exception | None = None
        #: Действующий план цели: есть — сохранение создаёт предложение.
        self.active_plan_id: str | None = None
        self.replaced: list[dict[str, Any]] = []
        self.replace_error: Exception | None = None
        self.archived: list[str] = []
        self.archive_error: Exception | None = None
        self.min_steps = 1
        self.read = 0
        self.saved_plan: dict[str, Any] | None = None

    def compose_decision(self, **kwargs: Any) -> dict[str, Any]:
        self.composed.append(kwargs)
        excluded = set(kwargs.get("excluded_capability_refs") or [])
        decision = self.outcome.get("decision")
        if not excluded or not isinstance(decision, dict):
            return self.outcome
        # Как каталог: убранные способности в план не входят, сборка новая —
        # новый ``decision_id`` и новые ``step_id``.
        left = [s for s in decision["steps"] if s["capability_ref"] not in excluded]
        if len(left) < self.min_steps:
            return {"outcome": "PLAN_NOT_JUSTIFIED", "decision": None, "details": {}}
        n = len(self.composed)
        return {
            "outcome": "PLAN",
            "decision": {
                **decision,
                "decision_id": f"{n:08x}-1111-4222-8333-444455556666",
                "steps": [{**s, "step_id": f"{s['step_id']}-r{n}"} for s in left],
            },
        }

    def get_plan(self, *, external_user_id: str) -> dict[str, Any] | None:
        self.read += 1
        return self.saved_plan

    def capability_labels(self, *, external_user_id: str, keys: list[str]) -> dict[str, str]:
        return {k: v for k, v in self.labels.items() if k in keys}

    def capability_details(self, *, external_user_id: str, keys: list[str]) -> dict[str, Any]:
        return {
            k: {"label": v, "expected_effect": self.effects.get(k)}
            for k, v in self.labels.items()
            if k in keys
        }

    def save_plan(self, *, external_user_id: str, command: dict[str, Any]) -> dict[str, Any]:
        self.saved.append(command)
        if self.save_error is not None:
            raise self.save_error
        if self.active_plan_id is not None:
            # Как каталог после #704: у цели уже есть действующий план —
            # новый сохранён ПРЕДЛОЖЕНИЕМ и называет, какой план заменит.
            return {
                "plan": {"plan_id": PROPOSAL_ID, "status": "proposed"},
                "replaces": {"plan_id": self.active_plan_id},
                "created": len(self.saved) == 1,
            }
        return {
            "plan": {"plan_id": PROPOSAL_ID, "status": "active"},
            "created": len(self.saved) == 1,
        }

    def replace_plan(self, **kwargs: Any) -> dict[str, Any]:
        self.replaced.append(kwargs)
        if self.replace_error is not None:
            raise self.replace_error
        return {
            "plan": {"plan_id": kwargs["plan_id"], "status": "active"},
            "replaced": len(self.replaced) == 1,
        }

    def archive_plan(self, *, external_user_id: str, plan_id: str) -> dict[str, Any]:
        self.archived.append(plan_id)
        if self.archive_error is not None:
            raise self.archive_error
        return {"plan": {"plan_id": plan_id, "status": "archived"}}


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


@pytest.fixture(autouse=True)
def _basis_proven(monkeypatch: pytest.MonkeyPatch) -> None:
    """DRF-2967: человек этих узлов — с основанием; база им не нужна.

    Сам гейт (три отказа и близнец) держит
    ``apps/miniapp_api/tests/test_plan_basis_gate_2967.py`` на настоящих
    строках согласия.
    """
    from apps.orchestrator import plan_gate

    monkeypatch.setattr(plan_gate, "plan_processing_refusal", lambda bot_user: None)


def _bot_user(account: str = ACCOUNT) -> SimpleNamespace:
    channel, channel_user_id = account.split(":")
    return SimpleNamespace(pk=1, id=1, channel=channel, channel_user_id=channel_user_id)


def _conversation() -> SimpleNamespace:
    return SimpleNamespace(id="conv-2885", skill_state={}, bot_user=_bot_user())


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
        assert buttons == [
            {"label": "Сохранить", "callback": SAVE},
            {"label": "Изменить", "callback": f"cb:plan:edit:{TOKEN}"},
            {"label": "Обсудить", "callback": f"cb:plan:discuss:{TOKEN}"},
        ]

    def test_s2_no_triple_no_request(self, catalog: FakeCatalog) -> None:
        result = _turn(card.TRIGGER, _conversation(), safety=None)

        assert catalog.composed == []
        assert result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"

    @pytest.mark.parametrize(
        "outcome", ["SAFETY_BLOCKED", "NO_GOAL", "NO_CURATED_DECOMPOSITION", "PLAN_NOT_JUSTIFIED"]
    )
    def test_s3_an_outcome_without_a_plan_is_shown_by_name(
        self, catalog: FakeCatalog, outcome: str
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)  # прежнее предложение ждало ответа
        catalog.outcome = {"outcome": outcome, "decision": None, "details": {}}

        result = _turn(card.TRIGGER, conversation)

        assert result.reply_text == f"{outcome} · тест"
        # Кнопки сохранения нет — только выход в меню (§72).
        assert [b["label"] for b in result.action_data["buttons"]] == ["Меню"]
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
                    # Ревизия хода, где предложение ПОКАЗАНО (7): ключ
                    # идемпотентности каталога, одна на все нажатия.
                    "state_revision": 7,
                },
                "provenance": {"policy_versions": POLICY_VERSIONS},
                "decision": {
                    "steps": decision["steps"],
                    "assertions": decision["assertions"],
                    "validation": decision["validation"],
                },
                "safety_state": "NORMAL",
                "safety_policy_version": "pre_check-abc",
                # Ревизия хода НАЖАТИЯ: при каком состоянии вынесен вердикт.
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

    def test_c3_the_engine_switched_off_is_not_answered(
        self, catalog: FakeCatalog, settings
    ) -> None:
        """Кнопки карточки зависят от механизма, а не от списка отладочных
        аккаунтов: предложение мог получить любой человек настоящим входом."""
        conversation = _conversation()
        _proposed(conversation)
        settings.PLAN_ENGINE_ENABLED = False

        result = card.try_handle_plan_save(
            text=SAVE,
            bot_user=_bot_user(),
            conversation=conversation,
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.saved == []

    def test_c3_an_account_outside_the_debug_list_can_save(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = card.try_handle_plan_save(
            text=SAVE,
            bot_user=_bot_user("max:999"),
            conversation=conversation,
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is not None and result.reply_text == "PLAN_SAVED · тест"

    def test_c4_a_second_tap_in_a_later_turn_is_the_same_confirmation(
        self, catalog: FakeCatalog
    ) -> None:
        """Повторное нажатие — НОВЫЙ ход со своей ревизией. Каталог узнаёт повтор
        по ключу (решение + вопрос + вариант + ревизия подтверждения); попади
        туда ревизия хода нажатия — второе нажатие создало бы второй план."""
        conversation = _conversation()
        _turn(card.TRIGGER, conversation, safety=_safety("NORMAL", 7))

        first = _turn(SAVE, conversation, safety=_safety("NORMAL", 9))
        second = _turn(SAVE, conversation, safety=_safety("NORMAL", 12))

        assert (first.reply_text, second.reply_text) == ("PLAN_SAVED · тест", "PLAN_SAVED · тест")
        one, two = catalog.saved
        # То, из чего каталог строит ключ идемпотентности, — одно и то же.
        assert (one["decision_id"], one["confirmation"]) == (
            two["decision_id"],
            two["confirmation"],
        )
        assert one["confirmation"]["state_revision"] == 7
        # А вердикт у каждого нажатия свой — при своей ревизии.
        assert (one["evaluated_at_revision"], two["evaluated_at_revision"]) == (9, 12)

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


# ─── вердикт «уточнить» при сохранении ───────────────────────────────────


class TestTheCatalogDecidesWhatClarifyMeans:
    """Решение владельца 08.10: универсального вопроса «уточнить» нет. Своей
    блокировки при этом вердикте карточка не держит и ограничений не шлёт —
    тройка уходит как есть, решает каталог."""

    def test_g1_a_clarify_verdict_is_sent_as_it_is(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(SAVE, conversation, safety=_safety("CLARIFY", 9))

        assert result.reply_text == "PLAN_SAVED · тест"
        assert catalog.saved[0]["safety_state"] == "CLARIFY"

    def test_g2_no_restrictions_are_sent(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        _turn(SAVE, conversation, safety=_safety("NORMAL", 9))

        assert len(catalog.saved) == 1  # команда ушла — и в ней ограничений нет
        assert "restrictions" not in catalog.saved[0]


# ─── «Изменить»: убрать шаг и пересобрать ────────────────────────────────

EDIT = f"cb:plan:edit:{TOKEN}"


def _buttons(result: Any) -> list[dict[str, str]]:
    return result.action_data["attachments"][0]["payload"]["buttons"]


class TestEditingIsRemovingAStepAndComposingAgain:
    def test_i1_edit_shows_the_steps_as_buttons_and_changes_nothing(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)
        before = dict(conversation.skill_state[card.STATE_KEY])

        result = _turn(EDIT, conversation)

        assert result.reply_text == "PLAN_EDIT · тест"
        assert _buttons(result) == [
            {"label": "Режим сна", "callback": f"cb:plan:drop:{TOKEN}:0"},
            {"label": "Вечерняя прогулка", "callback": f"cb:plan:drop:{TOKEN}:1"},
        ]
        assert len(catalog.composed) == 1  # каталог заново не спрашивали
        assert conversation.skill_state[card.STATE_KEY] == before

    def test_i2_a_tap_on_a_step_composes_again_without_it(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(f"cb:plan:drop:{TOKEN}:0", conversation, safety=_safety("NORMAL", 8))

        assert catalog.composed[1]["excluded_capability_refs"] == ["cap.sleep_routine"]
        assert result.reply_text == "• Вечерняя прогулка\n\nСохранить выбранные шаги в мой план?"
        pending = conversation.skill_state[card.STATE_KEY]
        assert pending["excluded"] == ["cap.sleep_routine"]
        assert pending["shown_at_revision"] == 8  # новое предложение — новый показ
        new_token = card._token(pending["decision"])
        assert new_token != TOKEN
        assert _buttons(result)[0] == {
            "label": "Сохранить",
            "callback": f"cb:plan:save:{new_token}",
        }

    def test_i3_what_was_removed_stays_removed_on_the_next_removal(
        self, catalog: FakeCatalog
    ) -> None:
        catalog.outcome["decision"]["steps"].append(
            {"step_id": "s-c", "capability_ref": "cap.breathing", "level": "CAPABILITY"}
        )
        catalog.outcome["decision"]["validation"]["step_validations"]["s-c"] = "VALID"
        catalog.labels["cap.breathing"] = "Дыхание"
        conversation = _conversation()
        _proposed(conversation)

        _turn(f"cb:plan:drop:{TOKEN}:0", conversation)
        second_token = card._token(conversation.skill_state[card.STATE_KEY]["decision"])
        result = _turn(f"cb:plan:drop:{second_token}:0", conversation)

        assert catalog.composed[2]["excluded_capability_refs"] == [
            "cap.sleep_routine",
            "cap.evening_walk",
        ]
        assert result.reply_text.startswith("• Дыхание")

    def test_i4_partial_acceptance_saves_only_what_is_left(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _turn(f"cb:plan:drop:{TOKEN}:0", conversation, safety=_safety("NORMAL", 8))
        new_token = card._token(conversation.skill_state[card.STATE_KEY]["decision"])

        result = _turn(f"cb:plan:save:{new_token}", conversation, safety=_safety("NORMAL", 9))

        assert result.reply_text == "PLAN_SAVED · тест"
        command = catalog.saved[0]
        assert [s["capability_ref"] for s in command["decision"]["steps"]] == ["cap.evening_walk"]
        assert command["confirmation"]["state_revision"] == 8

    def test_i5_the_old_cards_buttons_are_stale_after_a_change(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _turn(f"cb:plan:drop:{TOKEN}:0", conversation)

        save_old = _turn(SAVE, conversation)
        edit_old = _turn(EDIT, conversation)

        assert save_old.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert edit_old.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert catalog.saved == []

    def test_i6_no_plan_without_the_step_keeps_the_previous_proposal(
        self, catalog: FakeCatalog
    ) -> None:
        """«Отказаться и оставить прежнее»: без этого шага плана нет — прежнее
        предложение остаётся в силе, его «Сохранить» работает."""
        catalog.min_steps = 2
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(f"cb:plan:drop:{TOKEN}:0", conversation)

        assert result.reply_text == "PLAN_NOT_JUSTIFIED · тест"
        assert card._token(conversation.skill_state[card.STATE_KEY]["decision"]) == TOKEN
        assert _turn(SAVE, conversation).reply_text == "PLAN_SAVED · тест"
        assert len(catalog.saved[0]["decision"]["steps"]) == 2

    def test_i7_no_triple_no_recompose(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(f"cb:plan:drop:{TOKEN}:0", conversation, safety=None)

        assert result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"
        assert len(catalog.composed) == 1

    @pytest.mark.parametrize("tap", [EDIT, f"cb:plan:drop:{TOKEN}:0"])
    def test_i8_the_engine_switched_off_is_not_answered(
        self, catalog: FakeCatalog, tap: str, settings
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)
        settings.PLAN_ENGINE_ENABLED = False

        result = card.try_handle_plan_edit(
            text=tap,
            bot_user=_bot_user(),
            conversation=conversation,
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert len(catalog.composed) == 1

    def test_i9_an_index_outside_the_card_is_stale(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(f"cb:plan:drop:{TOKEN}:7", conversation)

        assert result.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert len(catalog.composed) == 1


class TestSaveRefusedBecauseTheCapabilityIsGone:
    def test_c6_the_proposal_is_dropped_so_the_person_composes_again(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)
        catalog.save_error = client_mod.PlanCapabilityNotConfirmedError("x")

        result = _turn(SAVE, conversation)

        assert result.reply_text == "PLAN_CAPABILITY_NOT_CONFIRMED · тест"
        assert card.STATE_KEY not in conversation.skill_state


# ─── «мой план»: сохранённый план нового механизма ───────────────────────


def _saved_plan() -> dict[str, Any]:
    return {"plan_id": "p-1", "status": "active", "revision": {"steps": [STEP_A, STEP_B]}}


class TestMyPlanShowsTheSavedPlan:
    def test_m1_the_saved_plan_is_shown_with_catalog_labels(
        self, catalog: FakeCatalog, settings
    ) -> None:
        settings.PLAN_LITE_ENABLED = False
        catalog.saved_plan = _saved_plan()

        result = _turn("мой план", _conversation(), safety=None)  # просмотр вердикта не требует

        assert result.action_type == "plan_engine_current"
        assert result.reply_text == "• Режим сна\n• Вечерняя прогулка\n\nPLAN_CURRENT · тест"
        assert "cap." not in result.reply_text
        assert catalog.composed == []  # показ ничего не собирает

    def test_m2_no_saved_plan_the_phrase_goes_on(self, catalog: FakeCatalog) -> None:
        result = card.try_handle_saved_plan(text="мой план", bot_user=_bot_user(), trace_id="t")

        assert result is None
        assert catalog.read == 1  # каталог спросили — плана нет

    def test_m3_the_engine_switched_off_does_not_ask_the_catalog(
        self, catalog: FakeCatalog, settings
    ) -> None:
        catalog.saved_plan = _saved_plan()
        settings.PLAN_ENGINE_ENABLED = False

        result = card.try_handle_saved_plan(text="мой план", bot_user=_bot_user(), trace_id="t")

        assert result is None
        assert catalog.read == 0
        # Положительная пара: с включённым механизмом тот же план показан —
        # и аккаунту вне отладочного списка тоже.
        settings.PLAN_ENGINE_ENABLED = True
        mine = card.try_handle_saved_plan(
            text="мой план", bot_user=_bot_user("max:999"), trace_id="t"
        )
        assert mine is not None

    def test_m4_another_phrase_does_not_ask_the_catalog(self, catalog: FakeCatalog) -> None:
        catalog.saved_plan = _saved_plan()

        assert card.try_handle_saved_plan(text="привет", bot_user=_bot_user(), trace_id="t") is None
        assert catalog.read == 0

    def test_m5_a_step_without_a_label_is_not_shown_as_a_key(self, catalog: FakeCatalog) -> None:
        catalog.saved_plan = _saved_plan()
        catalog.labels = {"cap.sleep_routine": "Режим сна"}

        result = card.try_handle_saved_plan(text="мой план", bot_user=_bot_user(), trace_id="t")

        assert result is not None
        assert result.reply_text == "PLAN_STEP_UNLABELLED · тест"


# ─── настоящий вход: кнопка и свободная просьба ──────────────────────────


class TestTheRealEntry:
    """Решение владельца 08.10: кнопка «Составить план» и свободная просьба
    без кодовой фразы. Отладочная команда — не для итоговой приёмки."""

    def test_n1_the_button_composes_for_an_account_outside_the_debug_list(
        self, catalog: FakeCatalog
    ) -> None:
        result = _turn(card.CB_COMPOSE, _conversation(), bot_user=_bot_user("max:999"))

        assert result.action_type == "plan_engine_proposal"
        assert catalog.composed[0]["safety_state"] == "NORMAL"

    def test_n1_the_debug_command_still_needs_the_list(self, catalog: FakeCatalog) -> None:
        result = card.try_handle_plan_trigger(
            text=card.TRIGGER,
            bot_user=_bot_user("max:999"),
            conversation=_conversation(),
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.composed == []

    def test_n2_the_button_is_silent_with_the_engine_off(
        self, catalog: FakeCatalog, settings
    ) -> None:
        settings.PLAN_ENGINE_ENABLED = False

        result = card.try_handle_plan_trigger(
            text=card.CB_COMPOSE,
            bot_user=_bot_user(),
            conversation=_conversation(),
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.composed == []

    def test_n3_the_free_request_composes_with_the_turns_own_triple(
        self, catalog: FakeCatalog
    ) -> None:
        """Инструмент модели: тройка берётся с объекта разговора этого хода."""
        from apps.orchestrator.safety.plan_turn import attach_turn_safety

        conversation = _conversation()
        attach_turn_safety(conversation, lambda: _safety("NORMAL", 5))

        result = card.compose_for_request(
            bot_user=_bot_user("max:999"), conversation=conversation, trace_id="t"
        )

        assert result is not None and result.action_type == "plan_engine_proposal"
        assert conversation.skill_state[card.STATE_KEY]["shown_at_revision"] == 5

    def test_n4_the_free_request_without_a_triple_does_not_ask_the_catalog(
        self, catalog: FakeCatalog
    ) -> None:
        result = card.compose_for_request(
            bot_user=_bot_user(), conversation=_conversation(), trace_id="t"
        )

        assert result is not None and result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"
        assert catalog.composed == []

    def test_n5_the_free_request_is_nothing_with_the_engine_off(
        self, catalog: FakeCatalog, settings
    ) -> None:
        settings.PLAN_ENGINE_ENABLED = False

        result = card.compose_for_request(
            bot_user=_bot_user(), conversation=_conversation(), trace_id="t"
        )

        assert result is None
        assert catalog.composed == []

    def test_n6_the_buttons_history_text_is_the_owners_words(self) -> None:
        from apps.orchestrator.plan_lite_card import is_plan_callback, tap_history_text

        assert is_plan_callback(card.CB_COMPOSE) is True
        assert tap_history_text(card.CB_COMPOSE) == "Составить план"


# ─── «Обсудить» ──────────────────────────────────────────────────────────

DISCUSS = f"cb:plan:discuss:{TOKEN}"
DISCUSS_SAVED = "cb:plan:discuss:saved"


def _discuss(text: str, conversation: Any, *, bot_user: Any = None):
    return card.try_handle_plan_discuss(
        text=text, bot_user=bot_user or _bot_user(), conversation=conversation, trace_id="t"
    )


class TestDiscussingThePlan:
    """Решение владельца 08.10: «Обсудить» на плане; первая реплика дословно;
    в контекст модели — шаги плана; ответ модели план не меняет."""

    def test_d1_the_tap_answers_with_the_owners_words_verbatim(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _discuss(DISCUSS, conversation)

        assert result is not None
        assert result.reply_text == "Давай обсудим твой план. Что хочешь изменить или уточнить?"
        assert result.action_data["buttons"]  # §72: под ответом есть следующий шаг
        assert len(catalog.composed) == 1  # обсуждение ничего не собирает

    def test_d2_the_model_sees_the_steps_in_the_catalogs_words(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        assert card.render_plan_discussion_block(conversation) == ""  # до нажатия блока нет

        _discuss(DISCUSS, conversation)
        block = card.render_plan_discussion_block(conversation)

        assert "1. Режим сна" in block
        assert "2. Вечерняя прогулка" in block
        assert "ПРЕДЛОЖЕНИЕ" in block
        assert "plan_remove_step" in block

    def test_d3_no_keys_or_ids_go_into_the_prompt(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)

        block = card.render_plan_discussion_block(conversation)

        assert "Режим сна" in block  # положительный контроль: блок с планом
        assert "cap." not in block
        assert DECISION_ID not in block
        assert TOKEN not in block

    def test_d4_a_stale_card_does_not_open_a_discussion(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _discuss("cb:plan:discuss:ffffffff", conversation)

        assert result is not None and result.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert card.render_plan_discussion_block(conversation) == ""

    def test_d5_the_engine_switched_off_is_silent(self, catalog: FakeCatalog, settings) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)
        assert card.render_plan_discussion_block(conversation) != ""
        settings.PLAN_ENGINE_ENABLED = False

        assert _discuss(DISCUSS, conversation) is None
        assert card.render_plan_discussion_block(conversation) == ""
        assert card.discussion_allows_removal(conversation) is False

    def test_d6_removing_a_step_recomposes_and_shows_the_change_before_saving(
        self, catalog: FakeCatalog
    ) -> None:
        from apps.orchestrator.safety.plan_turn import attach_turn_safety

        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)
        attach_turn_safety(conversation, lambda: _safety("NORMAL", 9))

        result = card.remove_step_for_request(
            bot_user=_bot_user(), conversation=conversation, trace_id="t", step_number=1
        )

        assert result is not None and result.action_type == "plan_engine_proposal"
        assert result.reply_text.startswith("• Вечерняя прогулка")
        assert catalog.composed[1]["excluded_capability_refs"] == ["cap.sleep_routine"]
        assert catalog.saved == []  # показано, не сохранено
        # Обсуждается уже новое предложение.
        block = card.render_plan_discussion_block(conversation)
        assert "1. Вечерняя прогулка" in block
        assert "Режим сна" not in block

    @pytest.mark.parametrize("number", [0, 3, -1, "1", None, True, 1.0])
    def test_d7_a_number_outside_the_plan_removes_nothing(
        self, catalog: FakeCatalog, number: Any
    ) -> None:
        from apps.orchestrator.safety.plan_turn import attach_turn_safety

        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)
        attach_turn_safety(conversation, lambda: _safety("NORMAL", 9))

        result = card.remove_step_for_request(
            bot_user=_bot_user(), conversation=conversation, trace_id="t", step_number=number
        )

        assert result is None
        assert len(catalog.composed) == 1

    def test_d8_without_an_open_discussion_nothing_is_removed(self, catalog: FakeCatalog) -> None:
        from apps.orchestrator.safety.plan_turn import attach_turn_safety

        conversation = _conversation()
        _proposed(conversation)
        attach_turn_safety(conversation, lambda: _safety("NORMAL", 9))

        result = card.remove_step_for_request(
            bot_user=_bot_user(), conversation=conversation, trace_id="t", step_number=1
        )

        assert result is None
        assert len(catalog.composed) == 1

    def test_d9_saving_closes_the_discussion(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)
        assert card.render_plan_discussion_block(conversation) != ""

        saved = _turn(SAVE, conversation)

        assert saved.reply_text == "PLAN_SAVED · тест"
        assert card.render_plan_discussion_block(conversation) == ""

    def test_d10_a_new_plan_does_not_inherit_the_old_discussion(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)
        assert card.render_plan_discussion_block(conversation) != ""

        _turn(card.CB_COMPOSE, conversation)

        assert card.render_plan_discussion_block(conversation) == ""

    def test_d11_the_saved_plan_is_discussed_but_not_changed_here(
        self, catalog: FakeCatalog
    ) -> None:
        catalog.saved_plan = _saved_plan()
        conversation = _conversation()

        result = _discuss(DISCUSS_SAVED, conversation)
        block = card.render_plan_discussion_block(conversation)

        assert result is not None
        assert result.reply_text == "Давай обсудим твой план. Что хочешь изменить или уточнить?"
        assert "СОХРАНЁННЫЙ" in block
        assert "1. Режим сна" in block
        assert card.discussion_allows_removal(conversation) is False
        assert "plan_remove_step" not in block

    def test_d12_no_saved_plan_opens_no_discussion(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()

        result = _discuss(DISCUSS_SAVED, conversation)

        assert result is not None and result.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert card.render_plan_discussion_block(conversation) == ""

    def test_d13_the_saved_plan_card_offers_the_discussion(self, catalog: FakeCatalog) -> None:
        catalog.saved_plan = _saved_plan()

        shown = card.try_handle_saved_plan(text="мой план", bot_user=_bot_user(), trace_id="t")

        assert shown is not None
        assert (shown.action_data or {})["buttons"][0] == {
            "label": "Обсудить",
            "callback": DISCUSS_SAVED,
        }

    def test_d14_the_tap_goes_through_the_turn_and_into_history_as_the_buttons_words(
        self, catalog: FakeCatalog
    ) -> None:
        from apps.orchestrator.plan_lite_card import is_plan_callback, tap_history_text

        conversation = _conversation()
        _proposed(conversation)

        result = _turn(DISCUSS, conversation)

        assert result.action_type == "plan_engine_discuss"
        assert is_plan_callback(DISCUSS) is True
        assert is_plan_callback(DISCUSS_SAVED) is True
        assert tap_history_text(DISCUSS) == "Обсудить"


# ─── «почему этот шаг»: слова каталога в обсуждении ──────────────────────

EFFECT_SLEEP = "Помогает ложиться и вставать в одно время."


class TestWhyThisStepInTheDiscussion:
    """Решение владельца (лист 07.10, п.13) и главного окна 09.10: «зачем шаг»
    — курируемый «ожидаемый эффект» способности из каталога. Модель приводит
    его дословно; у шага без текста причину не сочиняет."""

    def test_w1_the_catalogs_effect_reaches_the_model_next_to_its_step(
        self, catalog: FakeCatalog
    ) -> None:
        catalog.effects = {"cap.sleep_routine": EFFECT_SLEEP}
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)

        block = card.render_plan_discussion_block(conversation)

        assert f"1. Режим сна — зачем: {EFFECT_SLEEP}" in block
        # У второго шага текста нет — и в подсказке его нет.
        assert "2. Вечерняя прогулка\n" in block
        assert "дословно" in block

    def test_w2_no_effect_from_the_catalog_no_reason_in_the_prompt(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)

        block = card.render_plan_discussion_block(conversation)

        assert "1. Режим сна\n" in block  # положительный контроль: шаг в подсказке есть
        assert "— зачем:" not in block

    def test_w3_the_effect_follows_the_step_after_another_is_removed(
        self, catalog: FakeCatalog
    ) -> None:
        from apps.orchestrator.safety.plan_turn import attach_turn_safety

        catalog.effects = {"cap.evening_walk": "Даёт спокойный вечер."}
        conversation = _conversation()
        _proposed(conversation)
        _discuss(DISCUSS, conversation)
        attach_turn_safety(conversation, lambda: _safety("NORMAL", 9))

        card.remove_step_for_request(
            bot_user=_bot_user(), conversation=conversation, trace_id="t", step_number=1
        )
        block = card.render_plan_discussion_block(conversation)

        assert "1. Вечерняя прогулка — зачем: Даёт спокойный вечер." in block

    def test_w4_the_saved_plan_carries_its_effects_into_the_discussion(
        self, catalog: FakeCatalog
    ) -> None:
        catalog.saved_plan = _saved_plan()
        catalog.effects = {"cap.sleep_routine": EFFECT_SLEEP}
        conversation = _conversation()

        _discuss(DISCUSS_SAVED, conversation)
        block = card.render_plan_discussion_block(conversation)

        assert f"1. Режим сна — зачем: {EFFECT_SLEEP}" in block
        assert "2. Вечерняя прогулка\n" in block


# ─── предложение и замена действующего плана ─────────────────────────────


def _asked(conversation: Any, catalog: FakeCatalog):
    """Собрать, сохранить при действующем плане — получить вопрос о замене."""
    catalog.active_plan_id = ACTIVE_ID
    _proposed(conversation)
    return _turn(SAVE, conversation, safety=_safety("NORMAL", 8))


class TestReplacingTheActivePlan:
    """Решение владельца: сохранённый новый план не вытесняет действующий без
    подтверждения замены; слова вопроса и кнопок — лист 07.10, п.15."""

    def test_p1_saving_over_an_active_plan_asks_the_owners_question(
        self, catalog: FakeCatalog
    ) -> None:
        result = _asked(_conversation(), catalog)

        assert result.reply_text == "Заменить текущий план новым? Прежний останется в истории"
        assert result.reply_text != "PLAN_SAVED · тест"
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert buttons == [
            {"label": "Заменить план", "callback": REPLACE},
            {"label": "Оставить текущий", "callback": KEEP},
        ]
        assert catalog.replaced == []  # вопрос ничего не заменяет

    def test_p2_saving_the_first_plan_asks_nothing(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)

        result = _turn(SAVE, conversation)

        assert result.reply_text == "PLAN_SAVED · тест"

    def test_p3_replace_sends_the_named_plans_and_the_tapping_turns_triple(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)

        result = _turn(REPLACE, conversation, safety=_safety("NORMAL", 11))

        assert result.reply_text == "PLAN_REPLACED · тест"
        sent = catalog.replaced[0]
        assert (sent["plan_id"], sent["replaces_plan_id"]) == (PROPOSAL_ID, ACTIVE_ID)
        assert (sent["safety_state"], sent["evaluated_at_revision"]) == ("NORMAL", 11)

    def test_p4_a_second_tap_sends_the_same_confirmation_and_is_not_an_error(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)
        _turn(REPLACE, conversation, safety=_safety("NORMAL", 11))

        again = _turn(REPLACE, conversation, safety=_safety("NORMAL", 12))

        assert again.reply_text == "PLAN_REPLACED · тест"
        assert [r["replaces_plan_id"] for r in catalog.replaced] == [ACTIVE_ID, ACTIVE_ID]

    def test_p5_keep_archives_the_proposal_and_replaces_nothing(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)

        result = _turn(KEEP, conversation, safety=None)

        assert result.reply_text == "PLAN_KEPT · тест"
        assert catalog.archived == [PROPOSAL_ID]
        assert catalog.replaced == []
        # Вопрос закрыт: старая кнопка «Заменить план» больше ничего не заменяет.
        late = _turn(REPLACE, conversation, safety=_safety("NORMAL", 12))
        assert late.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert catalog.replaced == []

    def test_p6_no_triple_no_replacement(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)

        result = _turn(REPLACE, conversation, safety=None)

        assert result.reply_text == "SAFETY_INPUT_UNAVAILABLE · тест"
        assert catalog.replaced == []

    def test_p7_a_foreign_or_stale_card_replaces_nothing(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)

        for tap in ("cb:plan:replace:ffffffff", "cb:plan:keep:ffffffff"):
            result = _turn(tap, conversation, safety=_safety("NORMAL", 11))
            assert result.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"

        assert catalog.replaced == [] and catalog.archived == []

    def test_p8_without_a_question_the_buttons_do_nothing(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _proposed(conversation)
        _turn(SAVE, conversation)  # первый план: вопроса не было

        result = _turn(REPLACE, conversation, safety=_safety("NORMAL", 11))

        assert result.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert catalog.replaced == []

    def test_p9_a_blocking_verdict_keeps_the_question_open(self, catalog: FakeCatalog) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)
        catalog.replace_error = client_mod.PlanSaveSafetyBlockedError("blocked")

        blocked = _turn(REPLACE, conversation, safety=_safety("STOP", 11))
        catalog.replace_error = None
        later = _turn(REPLACE, conversation, safety=_safety("NORMAL", 12))

        assert blocked.reply_text == "PLAN_SAVE_SAFETY_BLOCKED · тест"
        assert later.reply_text == "PLAN_REPLACED · тест"

    @pytest.mark.parametrize(
        ("error", "shown"),
        [
            (client_mod.PlanReplacementTargetChangedError("x"), "PLAN_REPLACEMENT_TARGET_CHANGED"),
            (client_mod.PlanTransitionRefusedError("x"), "PLAN_PROPOSAL_EXPIRED"),
            (client_mod.PlanNotFoundError("x"), "PLAN_PROPOSAL_EXPIRED"),
        ],
    )
    def test_p10_a_refusal_that_cannot_be_retried_closes_the_question(
        self, catalog: FakeCatalog, error: Exception, shown: str
    ) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)
        catalog.replace_error = error

        refused = _turn(REPLACE, conversation, safety=_safety("NORMAL", 11))
        catalog.replace_error = None
        again = _turn(REPLACE, conversation, safety=_safety("NORMAL", 12))

        assert refused.reply_text == f"{shown} · тест"
        assert again.reply_text == "PLAN_PROPOSAL_EXPIRED · тест"
        assert len(catalog.replaced) == 1

    def test_p11_a_catalog_failure_keeps_the_question_for_a_retry(
        self, catalog: FakeCatalog
    ) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)
        catalog.replace_error = client_mod.PlanEngineUnavailableError("down")

        failed = _turn(REPLACE, conversation, safety=_safety("NORMAL", 11))
        catalog.replace_error = None
        retried = _turn(REPLACE, conversation, safety=_safety("NORMAL", 12))

        assert failed.reply_text == "PLAN_ENGINE_UNAVAILABLE · тест"
        assert retried.reply_text == "PLAN_REPLACED · тест"

    def test_p12_the_engine_switched_off_is_not_answered(
        self, catalog: FakeCatalog, settings
    ) -> None:
        conversation = _conversation()
        _asked(conversation, catalog)
        settings.PLAN_ENGINE_ENABLED = False

        result = card.try_handle_plan_replace(
            text=REPLACE,
            bot_user=_bot_user(),
            conversation=conversation,
            trace_id="t",
            turn_safety=_safety,
        )

        assert result is None
        assert catalog.replaced == []

    @pytest.mark.parametrize(
        "saved",
        [
            {"plan": {"plan_id": PROPOSAL_ID, "status": "proposed"}, "created": True},
            {
                "plan": {"plan_id": PROPOSAL_ID, "status": "active"},
                "replaces": {"plan_id": ACTIVE_ID},
                "created": True,
            },
        ],
    )
    def test_p13_half_of_the_proposal_mark_is_not_a_question(
        self, catalog: FakeCatalog, monkeypatch: pytest.MonkeyPatch, saved: dict[str, Any]
    ) -> None:
        """Статус без названного плана (и наоборот) — не вопрос о замене:
        спрашивать о замене плана, которого не назвали, нельзя."""
        conversation = _conversation()
        _proposed(conversation)
        monkeypatch.setattr(catalog, "save_plan", lambda **kw: saved)

        result = _turn(SAVE, conversation)

        assert result.reply_text == "PLAN_SAVED · тест"

    def test_p14_the_taps_go_into_history_as_the_buttons_words(self) -> None:
        from apps.orchestrator.plan_lite_card import is_plan_callback, tap_history_text

        assert is_plan_callback(REPLACE) is True and is_plan_callback(KEEP) is True
        assert tap_history_text(REPLACE) == "Заменить план"
        assert tap_history_text(KEEP) == "Оставить текущий"
