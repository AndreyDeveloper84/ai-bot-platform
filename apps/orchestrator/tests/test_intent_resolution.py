"""DRF-1273 — canonical intent resolution (Output Contract 0.5) tests.

Two layers, mirroring the design:

- ``_validate_and_build`` — the deterministic gate: contract invariants are
  enforced WITHOUT a model, so every test here pins a piece of the canon
  (frozen registry, status/clarification/safety logic, verbatim evidence).
- ``resolve_intent`` / ``resolve_and_log_turn_intent`` — the LLM pass with a
  mocked client: malformed drafts and LLM errors degrade to «no contract»,
  never to a fabricated one.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock, create_autospec, patch

import pytest
from django.test import override_settings

from apps.orchestrator import intent_resolution
from apps.orchestrator.intent_resolution import (
    _validate_and_build,
    build_resolution_messages,
    resolve_and_log_turn_intent,
    resolve_intent,
)

USER_TEXT = "покажи массажистов в Пензе"
MESSAGE_ID = "msg-42"
TRACE_ID = "trace-1"


def _draft(**overrides):
    """A minimal VALID resolved draft (FIND_SPECIALIST + service_category)."""
    base = {
        "intent_id": "11111111-2222-3333-4444-555555555555",
        "intent_type": "FIND_SPECIALIST",
        "status": "resolved",
        "confidence": 0.9,
        "slots": {
            "service_category": {
                "raw_value": "массажистов",
                "normalized_value": "массаж",
                "entity_ref": None,
                "confirmation_status": "filled",
                "evidence_refs": ["ev-1"],
            }
        },
        "missing_required_slots": [],
        "evidence": [
            {"evidence_id": "ev-1", "message_id": "model-guess", "fragment": "массажистов в Пензе"}
        ],
        "requires_clarification": False,
        "clarification_question": None,
        "safety_flags": [],
        "unmet_slot_requirements": [],
        "contract_version": "0.1",  # stale on purpose — runtime must overwrite
        "status_reason": None,
        "clarification_reason": None,
        "clarification_effect": None,
        "secondary_intents": [],
    }
    base.update(overrides)
    return base


def _validate(raw, user_text=USER_TEXT):
    return _validate_and_build(raw, user_text=user_text, message_id=MESSAGE_ID, trace_id=TRACE_ID)


class TestValidContracts:
    def test_resolved_contract_passes_and_version_forced(self):
        contract = _validate(_draft())
        assert contract is not None
        assert contract["intent_type"] == "FIND_SPECIALIST"
        assert contract["status"] == "resolved"
        assert contract["contract_version"] == "0.5"
        assert len(contract) == 16

    def test_message_id_overwritten_by_runtime(self):
        contract = _validate(_draft())
        assert contract["evidence"][0]["message_id"] == MESSAGE_ID

    def test_intent_id_generated_when_missing(self):
        contract = _validate(_draft(intent_id=None))
        assert contract is not None
        assert contract["intent_id"]

    def test_case_and_whitespace_only_fragment_diff_passes(self):
        draft = _draft(
            evidence=[
                {"evidence_id": "ev-1", "message_id": "x", "fragment": "  Массажистов   В Пензе "}
            ]
        )
        assert _validate(draft) is not None

    def test_out_of_scope_negative_path(self):
        draft = _draft(
            intent_type="UNKNOWN",
            status="unresolved",
            status_reason="out_of_scope",
            slots={},
            evidence=[],
        )
        contract = _validate(draft, user_text="сколько будет 2+2 на марсе")
        assert contract is not None
        assert contract["intent_type"] == "UNKNOWN"
        assert contract["status"] == "unresolved"
        assert contract["status_reason"] == "out_of_scope"

    def test_needs_clarification_intent_level(self):
        draft = _draft(
            intent_type="UNKNOWN",
            status="needs_clarification",
            status_reason="low_confidence",
            confidence=0.3,
            slots={},
            requires_clarification=True,
            clarification_question="Что именно вы хотите — найти мастера или записаться?",
            clarification_reason="intent_low_confidence",
            clarification_effect="blocks_current_action",
        )
        contract = _validate(draft)
        assert contract is not None
        assert contract["requires_clarification"] is True

    def test_blocked_safety_coerces_status_reason(self):
        draft = _draft(
            intent_type="PROVIDE_CONTEXT",
            status="blocked_safety",
            status_reason=None,
            safety_flags=["red_zone_conflict"],
        )
        contract = _validate(draft)
        assert contract is not None
        assert contract["status_reason"] == "safety_gate_blocked"

    def test_confirmed_reference_slot_without_entity_ref_downgraded(self):
        draft = _draft(
            slots={
                "service_ref": {
                    "raw_value": "массаж",
                    "normalized_value": "массаж",
                    "entity_ref": None,
                    "confirmation_status": "confirmed",
                    "evidence_refs": ["ev-1"],
                }
            }
        )
        contract = _validate(draft)
        assert contract is not None
        assert contract["slots"]["service_ref"]["confirmation_status"] == "filled"

    def test_unknown_slot_name_dropped(self):
        draft = _draft(
            slots={
                "favorite_color": {
                    "raw_value": "синий",
                    "confirmation_status": "filled",
                    "evidence_refs": ["ev-1"],
                }
            }
        )
        contract = _validate(draft)
        assert contract is not None
        assert contract["slots"] == {}

    def test_secondary_intent_shape_enforced(self):
        draft = _draft(
            secondary_intents=[
                {
                    "intent_type": "ASK_ABOUT_PRICE",
                    "evidence_refs": ["ev-1"],
                    "message_position": 2,
                },
                {"intent_type": "UNKNOWN", "evidence_refs": ["ev-1"], "message_position": 3},
                {
                    "intent_type": "BOOK_APPOINTMENT",
                    "evidence_refs": ["ev-404"],
                    "message_position": 4,
                },
            ]
        )
        contract = _validate(draft)
        assert contract is not None
        assert [s["intent_type"] for s in contract["secondary_intents"]] == ["ASK_ABOUT_PRICE"]


class TestHardRejections:
    @pytest.mark.parametrize(
        "overrides",
        [
            # Fabricated evidence — the core anti-fantasy gate.
            {
                "evidence": [
                    {"evidence_id": "ev-1", "message_id": "x", "fragment": "стрижку в Твери"}
                ]
            },
            # resolved obliges non-empty evidence.
            {"evidence": []},
            # blocked_safety ⇔ non-empty flags, both directions.
            {"status": "blocked_safety", "safety_flags": []},
            {"safety_flags": ["red_zone_conflict"]},
            # UNKNOWN rules.
            {"intent_type": "UNKNOWN", "status": "resolved"},
            {"intent_type": "UNKNOWN", "status": "unresolved", "status_reason": "low_confidence"},
            {"intent_type": "UNKNOWN", "status": "unresolved", "status_reason": None},
            # Clarification typing.
            {
                "requires_clarification": True,
                "clarification_question": None,
                "clarification_reason": "missing_required_slot",
                "clarification_effect": "blocks_current_action",
            },
            {
                "status": "needs_clarification",
                "requires_clarification": True,
                "clarification_question": "Уточните?",
                "clarification_reason": "missing_required_slot",
                "clarification_effect": "blocks_current_action",
            },
            {
                "requires_clarification": True,
                "clarification_question": "Какие данные отозвать?",
                "clarification_reason": "consent_scope_selection",
                "clarification_effect": "allows_immediate_safe_action",
            },
            # Lifecycle statuses are not resolution-pass outcomes (KM-IM-1).
            {"status": "superseded", "status_reason": "intent_shift"},
            {"status": "expired", "status_reason": "session_expired"},
            # Registry discipline.
            {"intent_type": "ORDER_PIZZA"},
            {"status_reason": "model_made_this_up", "status": "unresolved"},
        ],
        ids=[
            "fabricated_fragment",
            "resolved_without_evidence",
            "blocked_safety_without_flags",
            "flags_without_blocked_status",
            "unknown_resolved",
            "unknown_unresolved_bad_reason",
            "unknown_without_reason",
            "clarification_without_question",
            "needs_clarification_slot_level_reason",
            "consent_scope_selection_wrong_type",
            "superseded_not_a_pass_outcome",
            "expired_not_a_pass_outcome",
            "intent_type_out_of_registry",
            "status_reason_out_of_enum",
        ],
    )
    def test_hard_invariant_violations_rejected(self, overrides):
        assert _validate(_draft(**overrides)) is None

    def test_not_a_dict_rejected(self):
        assert _validate(["not", "a", "dict"]) is None

    def test_duplicate_evidence_ids_rejected(self):
        draft = _draft(
            evidence=[
                {"evidence_id": "ev-1", "message_id": "x", "fragment": "массажистов"},
                {"evidence_id": "ev-1", "message_id": "x", "fragment": "в Пензе"},
            ]
        )
        assert _validate(draft) is None

    def test_slot_with_unresolvable_evidence_refs_dropped(self):
        draft = _draft(
            slots={
                "provider_name": {
                    "raw_value": "Анна",
                    "confirmation_status": "filled",
                    "evidence_refs": ["ev-404"],
                }
            }
        )
        contract = _validate(draft)
        assert contract is not None
        assert contract["slots"] == {}


class TestSoftCoercions:
    def test_resolved_forces_status_reason_null(self):
        contract = _validate(_draft(status_reason="low_confidence"))
        assert contract is not None
        assert contract["status_reason"] is None

    def test_no_clarification_forces_triplet_null(self):
        draft = _draft(
            clarification_question="лишний вопрос",
            clarification_reason="missing_required_slot",
            clarification_effect="blocks_current_action",
        )
        contract = _validate(draft)
        assert contract is not None
        assert contract["clarification_question"] is None
        assert contract["clarification_reason"] is None
        assert contract["clarification_effect"] is None

    def test_confidence_clamped(self):
        contract = _validate(_draft(confidence=1.7))
        assert contract is not None
        assert contract["confidence"] == 1.0


def _router_client_fake(**create_behaviour: object) -> object:
    """Подделка клиента, **снятая с настоящего** ``RouterLLMClient``.

    DRF-1310 — сердце сторожа. Резолвер падал на КАЖДОМ живом ходе
    пилота с ``'RouterLLMClient' object has no attribute 'create'``, а тесты
    были зелёными: подделкой был голый ``Mock()``, который принимает
    ЛЮБОЙ путь вызова. Подделка, не совпадающая с настоящим клиентом,
    СКРЫВАЕТ поломку, а не находит её.

    Ручной ``spec=[...]`` тоже не решает задачу до конца: он закрепляет
    только верхний уровень и описывает клиент РУКАМИ — вложенный
    ``chat.completions.create`` оставался голым ``AsyncMock``, который
    принимает любую сигнатуру. ``create_autospec`` по ЖИВОМУ экземпляру
    снимает форму целиком, вместе с сигнатурой ``create``: подделка
    больше НЕ МОЖЕТ обещать того, чего у настоящего клиента нет.

    Цена решения честно: тесты резолвера теперь требуют ai-core (через
    ``concierge``). Импорт оставлен ОТЛОЖЕННЫМ — чистые тесты валидатора
    (``TestValidate*``) коллектятся и проходят без него, как и раньше.
    Связать подделку с настоящим клиентом и одновременно не зависеть
    от него нельзя — это и есть цена сторожа, и она меньше трёх часов
    мёртвого контракта на пилоте.
    """

    from apps.orchestrator.concierge import CONCIERGE_SKILL, RouterLLMClient

    client = create_autospec(RouterLLMClient(skill=CONCIERGE_SKILL), instance=True, spec_set=True)
    client.chat.completions.create.configure_mock(**create_behaviour)
    client.last_provider = "openai"
    client.last_model = "gpt-4o-mini"
    return client


def _client_returning(payload: str) -> object:
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=50),
    )
    return _router_client_fake(return_value=response)


class TestResolveIntent:
    def test_valid_json_yields_contract(self):
        client = _client_returning(json.dumps(_draft(), ensure_ascii=False))
        contract, usage = resolve_intent(
            USER_TEXT, message_id=MESSAGE_ID, trace_id=TRACE_ID, llm_client=client
        )
        assert contract is not None
        assert contract["intent_type"] == "FIND_SPECIALIST"
        assert usage.prompt_tokens == 100

    def test_fenced_json_unwrapped(self):
        payload = "```json\n" + json.dumps(_draft(), ensure_ascii=False) + "\n```"
        contract, _ = resolve_intent(
            USER_TEXT,
            message_id=MESSAGE_ID,
            trace_id=TRACE_ID,
            llm_client=_client_returning(payload),
        )
        assert contract is not None

    def test_malformed_json_yields_none(self):
        contract, _ = resolve_intent(
            USER_TEXT,
            message_id=MESSAGE_ID,
            trace_id=TRACE_ID,
            llm_client=_client_returning("{oops"),
        )
        assert contract is None

    def test_llm_error_yields_none(self):
        client = _router_client_fake(side_effect=RuntimeError("provider down"))
        contract, usage = resolve_intent(
            USER_TEXT, message_id=MESSAGE_ID, trace_id=TRACE_ID, llm_client=client
        )
        assert contract is None
        assert usage is None

    def test_invalid_draft_yields_none_nothing_fabricated(self):
        draft = _draft(
            evidence=[{"evidence_id": "ev-1", "message_id": "x", "fragment": "выдуманная цитата"}]
        )
        contract, _ = resolve_intent(
            USER_TEXT,
            message_id=MESSAGE_ID,
            trace_id=TRACE_ID,
            llm_client=_client_returning(json.dumps(draft, ensure_ascii=False)),
        )
        assert contract is None


class TestResolveAndLog:
    @override_settings(INTENT_RESOLUTION_LIVE_ENABLED=False)
    def test_flag_off_skips_llm(self):
        with patch("apps.orchestrator.concierge.RouterLLMClient") as client_cls:
            result = resolve_and_log_turn_intent(
                text=USER_TEXT,
                bot_user=Mock(),
                conversation=Mock(),
                user_message_id=1,
                trace_id=TRACE_ID,
            )
        assert result is None
        client_cls.assert_not_called()

    def test_happy_path_logs_serialized_contract(self, caplog):
        client = _client_returning(json.dumps(_draft(), ensure_ascii=False))
        with (
            patch("apps.orchestrator.concierge.RouterLLMClient", return_value=client),
            patch.object(intent_resolution, "_record_resolution_metric"),
            caplog.at_level(logging.INFO, logger="apps.orchestrator.intent_resolution"),
        ):
            contract = resolve_and_log_turn_intent(
                text=USER_TEXT,
                bot_user=Mock(),
                conversation=Mock(),
                user_message_id=1,
                trace_id=TRACE_ID,
            )
        assert contract is not None
        ok_records = [
            r for r in caplog.records if r.msg.startswith("orchestrator.intent_resolution.ok")
        ]
        assert len(ok_records) == 1
        logged = json.loads(ok_records[0].args[1])
        assert logged["contract_version"] == "0.5"
        assert logged["intent_type"] == "FIND_SPECIALIST"
        assert logged["evidence"][0]["fragment"] == "массажистов в Пензе"

    def test_failed_resolution_logs_nothing_fabricated(self, caplog):
        client = _client_returning("{not json")
        with (
            patch("apps.orchestrator.concierge.RouterLLMClient", return_value=client),
            patch.object(intent_resolution, "_record_resolution_metric"),
            caplog.at_level(logging.INFO, logger="apps.orchestrator.intent_resolution"),
        ):
            contract = resolve_and_log_turn_intent(
                text=USER_TEXT,
                bot_user=Mock(),
                conversation=Mock(),
                user_message_id=None,
                trace_id=TRACE_ID,
            )
        assert contract is None
        assert not [
            r for r in caplog.records if r.msg.startswith("orchestrator.intent_resolution.ok")
        ]


class TestResolverMatchesRealClient:
    """DRF-1310 — сторож против расхождения подделки с настоящим клиентом.

    Резолвер три часа падал на живом пилоте с
    ``'RouterLLMClient' object has no attribute 'create'``, а тесты были
    зелёными: подделкой был голый ``Mock()``, который принимает **любой**
    путь вызова. Такая подделка не может поймать неверный путь по
    построению.

    Эти два теста смотрят на настоящий класс, а не на его копию.
    """

    def test_router_client_exposes_the_path_resolver_calls(self):
        from apps.orchestrator.concierge import RouterLLMClient

        client = RouterLLMClient(skill="concierge")
        assert callable(client.chat.completions.create), (
            "Резолвер зовёт llm_client.chat.completions.create — "
            "у настоящего клиента этого пути нет"
        )

    def test_router_client_has_no_flat_create(self):
        """Обратная половина: плоского ``create`` быть не должно.

        Без неё сторож пропустит откат резолвера на прежний вызов.
        """
        from apps.orchestrator.concierge import RouterLLMClient

        client = RouterLLMClient(skill="concierge")
        assert not hasattr(client, "create"), (
            "У клиента появился плоский create — проверьте, какой путь "
            "зовёт резолвер, и обновите оба теста разом"
        )

    # -- Вторая половина сторожа: у самой ПОДДЕЛКИ должны быть зубы -----
    #
    # Тесты выше смотрят на настоящий класс. Их одних мало: они останутся
    # зелёными, даже если подделка снова разъедется с клиентом (именно так
    # DRF-1310 и дожил до пилота). Эти три проверяют саму подделку — что
    # она физически не может обещать того, чего у клиента нет.

    def test_fake_refuses_the_call_path_that_broke_the_pilot(self):
        """Плоский ``create`` на подделке обязан падать так же, как на живом.

        Ровно эта строка три часа роняла каждый ход владельца.
        """

        client = _router_client_fake(return_value=None)
        with pytest.raises(AttributeError):
            client.create(model="m", messages=[])

    def test_fake_enforces_the_real_create_signature(self):
        """Подделка снята с настоящей сигнатуры, а не с голого AsyncMock.

        Голый ``AsyncMock`` принимает любые аргументы — и пропустил бы
        опечатку в имени kwarg так же тихо, как пропустил неверный путь.
        """

        client = _router_client_fake(return_value=None)
        with pytest.raises(TypeError):
            client.chat.completions.create(model="m", msgs=[])

    def test_fake_accepts_exactly_what_the_resolver_passes(self):
        """Обратная полярность: настоящий вызов резолвера обязан проходить.

        Без неё сторож можно «починить» подделкой, которая запрещает всё.
        """

        client = _router_client_fake(return_value=None)
        # Сигнатура проверяется в момент вызова; корутину закрываем сами,
        # чтобы не оставить RuntimeWarning «never awaited».
        client.chat.completions.create(
            model="gpt-4o-mini",
            messages=build_resolution_messages("привет", message_id="m1"),
        ).close()
