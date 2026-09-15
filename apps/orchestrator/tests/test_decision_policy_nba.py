"""Выбор NBA в тени (DRF-1932, каркас среза 6.3).

Боевые словари J1/J2 пусты (``test_nba_taxonomy``), поэтому ветки выбора
``CLEAR_PRIMARY`` / ``MULTIPLE_SUITABLE`` проверяются на **тестовом словаре,
который подставляет сам тест**. Это не значения политики — это фикстура.

* safety первой: граница, неизвестный вердикт, clarify, признак боли (I2),
  health-контекст (п.6) — ``TestSafetyComesFirst``;
* ``candidate_nba`` при недоступном входе: исход не меняется, в каталог не
  пишется — ``TestCandidateWhenInputUnavailable``;
* выбор при доступном входе — ``TestSelection``;
* у каждого NBA есть тройка, у исхода без NBA тройки нет, сигнал модели не
  меняет выбор — ``TestTripleInvariants``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.orchestrator import decision_policy as dp
from apps.orchestrator import nba_taxonomy as tx

#: Тестовый словарь — не J1.
PHRASES = {
    "расслабиться вечером": "RELAXATION",
    "расслабить спину": "BACK_COMFORT",
    "выглядеть свежее": "FACE_FRESHNESS",
    "отеки": "PUFFINESS_REDUCTION",
}
#: Тестовые умолчания — не J2. Сочетания произвольные: I1 (а) их не ограничивает.
DEFAULTS = {
    "RELAXATION": ("SUPPORT", "PROVIDER_SESSION"),
    "BACK_COMFORT": ("RECOVER", "SELF_CARE"),
    "FACE_FRESHNESS": ("OBSERVE", "OBSERVE"),
    "PUFFINESS_REDUCTION": ("ADDRESS", "PLAN"),
}

INPUT_UNAVAILABLE = ("STATE_BLOCKED", "BLOCK_READINESS_INPUT_UNAVAILABLE")
READY = ("STATE_READY",)


def _evidence(safety_state="normal", reason_codes=READY, **extra):
    return SimpleNamespace(
        safety={"state": safety_state},
        reason_codes=tuple(reason_codes),
        readiness_state="ready",
        allow_recommend=False,
        model_signals_rejected=(),
        measures={},
        **extra,
    )


def _decide(text, *, safety_state="normal", reason_codes=READY, handoff=None, defaults=None):
    needs = tx.read_turn_needs(text, phrases=PHRASES)
    return dp.decide(
        _evidence(safety_state, reason_codes),
        handoff=handoff,
        needs=needs,
        defaults=DEFAULTS if defaults is None else defaults,
    )


class TestSafetyComesFirst:
    def test_boundary_carries_no_target_and_no_triple(self):
        verdict = _decide("хочу расслабиться вечером", safety_state="stop")

        assert verdict.result_status is dp.PolicyStatus.SAFETY_BOUNDARY
        assert verdict.recognized_targets == ()
        assert verdict.primary is None and verdict.candidate_nba is None

    @pytest.mark.parametrize("state", ["unknown", None, ""])
    def test_unknown_safety_builds_nothing(self, state):
        verdict = _decide("хочу расслабиться вечером", safety_state=state)

        assert verdict.result_status is dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE
        assert verdict.recognized_targets == ()
        assert verdict.candidate_nba is None

    def test_clarify_keeps_the_recognized_target_but_no_nba(self):
        verdict = _decide("хочу расслабиться вечером", safety_state="clarify")

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.recognized_targets == ("RELAXATION",)
        assert verdict.primary is None and verdict.candidate_nba is None

    @pytest.mark.parametrize(
        "text",
        [
            "ноет спина, хочу расслабить спину",
            "болит, хочу расслабить спину",
            "простреливает, хочу расслабить спину",
            "онемение, хочу расслабить спину",
            "отдаёт в ногу, хочу расслабить спину",
            "слабость, хочу расслабить спину",
        ],
    )
    @pytest.mark.parametrize("codes", [READY, INPUT_UNAVAILABLE])
    def test_pain_signal_i2_is_clarification_with_the_target_and_without_a_triple(
        self, text, codes
    ):
        verdict = _decide(text, reason_codes=codes)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.reason_codes == (dp.POLICY_PAIN_SIGNAL,)
        assert verdict.recognized_targets == ("BACK_COMFORT",)
        assert verdict.primary is None and verdict.alternatives == ()
        assert verdict.candidate_nba is None

    @pytest.mark.parametrize("text", ["у меня отёки", "беспокоят отёки, хочу выглядеть свежее"])
    @pytest.mark.parametrize("codes", [READY, INPUT_UNAVAILABLE])
    def test_swelling_p6_is_clarification_without_any_target(self, text, codes):
        verdict = _decide(text, reason_codes=codes)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.reason_codes == (dp.POLICY_HEALTH_SENSITIVE_CONTEXT,)
        assert verdict.recognized_targets == ()
        assert verdict.candidate_nba is None

    def test_owner_example_without_pain_takes_part_in_selection(self):
        verdict = _decide("хочу расслабить спину")

        assert verdict.result_status is dp.PolicyStatus.CLEAR_PRIMARY
        assert verdict.primary == tx.Triple("BACK_COMFORT", "RECOVER", "SELF_CARE")


class TestCandidateWhenInputUnavailable:
    def test_status_stays_input_unavailable_and_the_triple_is_a_candidate(self):
        verdict = _decide("хочу расслабиться вечером", reason_codes=INPUT_UNAVAILABLE)

        assert verdict.result_status is dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE
        assert verdict.reason_codes == (dp.POLICY_READINESS_INPUT_UNAVAILABLE,)
        assert verdict.primary is None and verdict.alternatives == ()
        assert verdict.candidate_nba == tx.Triple("RELAXATION", "SUPPORT", "PROVIDER_SESSION")
        assert verdict.candidate_not_actionable_reason == dp.POLICY_READINESS_INPUT_UNAVAILABLE
        assert verdict.catalog_writable is False

    def test_candidate_field_in_the_record_spelling(self):
        verdict = _decide("хочу расслабиться вечером", reason_codes=INPUT_UNAVAILABLE)

        assert verdict.nba_fields()["candidate_nba"] == {
            "role": "primary",
            "target": "RELAXATION",
            "family": "SUPPORT",
            "action_type": "PROVIDER_SESSION",
            "actionable": False,
            "not_actionable_reason": "POLICY_READINESS_INPUT_UNAVAILABLE",
        }

    def test_the_order_constant_turns_the_candidate_off(self, monkeypatch):
        monkeypatch.setattr(dp, "CANDIDATE_NBA_WHEN_INPUT_UNAVAILABLE", False)

        verdict = _decide("хочу расслабиться вечером", reason_codes=INPUT_UNAVAILABLE)

        assert verdict.result_status is dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE
        assert verdict.candidate_nba is None
        assert verdict.recognized_targets == ("RELAXATION",)

    def test_production_dictionaries_name_the_owner_version(self):
        needs = tx.read_turn_needs("Хочу снять напряжение")
        verdict = dp.decide(_evidence("normal", INPUT_UNAVAILABLE), needs=needs)

        assert verdict.nba_fields()["taxonomy_version"] == "h5-j1j2:owner-2026-09-15"

    def test_candidate_status_is_never_catalog_writable(self):
        with pytest.raises(dp.NotCatalogWritable):
            dp.assert_catalog_writable(
                _decide("хочу расслабиться вечером", reason_codes=INPUT_UNAVAILABLE).result_status
            )


class TestSelection:
    def test_relax_in_the_evening_is_relaxation(self):
        verdict = _decide("хочу расслабиться вечером")

        assert verdict.result_status is dp.PolicyStatus.CLEAR_PRIMARY
        assert verdict.reason_codes == (dp.POLICY_SINGLE_TARGET,)
        assert verdict.primary == tx.Triple("RELAXATION", "SUPPORT", "PROVIDER_SESSION")
        assert verdict.alternatives == ()
        assert verdict.catalog_writable is False

    def test_several_targets_primary_by_h5_order_and_at_most_two_alternatives(self):
        verdict = _decide("хочу выглядеть свежее, расслабиться вечером и расслабить спину")

        assert verdict.result_status is dp.PolicyStatus.MULTIPLE_SUITABLE
        assert verdict.primary is not None and verdict.primary.target == "FACE_FRESHNESS"
        assert [a.target for a in verdict.alternatives] == ["RELAXATION", "BACK_COMFORT"]
        assert len(verdict.alternatives) <= dp.MAX_ALTERNATIVES

    def test_alternatives_are_capped(self, monkeypatch):
        monkeypatch.setattr(dp, "MAX_ALTERNATIVES", 1)

        verdict = _decide("хочу выглядеть свежее, расслабиться вечером и расслабить спину")

        assert [a.target for a in verdict.alternatives] == ["RELAXATION"]

    def test_no_target_is_named_and_not_insufficient_context(self):
        verdict = _decide("что посоветуешь?")

        assert verdict.result_status is dp.PolicyStatus.NBA_TARGET_NOT_RECOGNIZED
        assert verdict.result_status.value != "INSUFFICIENT_CONTEXT"

    def test_target_without_a_default_is_named(self):
        verdict = _decide("хочу расслабиться вечером", defaults={})

        assert verdict.result_status is dp.PolicyStatus.NBA_TRIPLE_NOT_DEFINED
        assert verdict.recognized_targets == ("RELAXATION",)
        assert verdict.primary is None


#: Сценарии для кванторов ниже: (текст, safety, коды движка).
SCENARIOS = [
    ("хочу расслабиться вечером", "normal", READY),
    ("хочу выглядеть свежее и расслабить спину", "caution", READY),
    ("что посоветуешь?", "normal", READY),
    ("хочу расслабиться вечером", "normal", INPUT_UNAVAILABLE),
    ("ноет спина", "normal", READY),
    ("у меня отёки", "normal", READY),
    ("хочу расслабиться вечером", "clarify", READY),
    ("хочу расслабиться вечером", "stop", READY),
    ("хочу расслабиться вечером", "unknown", READY),
]


class TestTripleInvariants:
    def test_scenarios_cover_both_sides(self):
        """Квантор по пустому множеству зелёный на чём угодно — нижняя граница."""
        statuses = {
            _decide(t, safety_state=s, reason_codes=c).result_status for t, s, c in SCENARIOS
        }

        assert statuses & dp.NBA_STATUSES == dp.NBA_STATUSES
        assert len(statuses - dp.NBA_STATUSES) >= 4

    @pytest.mark.parametrize(("text", "state", "codes"), SCENARIOS)
    def test_every_nba_has_a_triple_and_no_other_outcome_has_one(self, text, state, codes):
        verdict = _decide(text, safety_state=state, reason_codes=codes)

        if verdict.result_status in dp.NBA_STATUSES:
            assert verdict.primary is not None
            for triple in (verdict.primary, *verdict.alternatives):
                assert triple.target in tx.TARGETS
                assert triple.family in tx.FAMILIES
                assert triple.action_type in tx.ACTION_TYPES
        else:
            assert verdict.primary is None
            assert verdict.alternatives == ()

    @pytest.mark.parametrize(("text", "state", "codes"), SCENARIOS)
    def test_model_signals_do_not_change_the_choice(self, text, state, codes):
        plain = _decide(text, safety_state=state, reason_codes=codes)
        noisy = SimpleNamespace(
            **{
                **vars(_evidence(state, codes)),
                "model_signals_rejected": ({"signal": "target", "value": "BACK_COMFORT"},),
                "measures": {"completeness": 1.0},
                "allow_recommend": True,
                "readiness_state": "ready",
            }
        )

        assert (
            dp.decide(noisy, needs=tx.read_turn_needs(text, phrases=PHRASES), defaults=DEFAULTS)
            == plain
        )

    def test_logged_triples_count_candidates_too(self):
        """Сторож «тройки логируются» считает primary, alternatives и candidate_nba."""
        logged = []
        for text, state, codes in SCENARIOS:
            fields = _decide(text, safety_state=state, reason_codes=codes).nba_fields()
            logged += [f for f in (fields["primary"], fields["candidate_nba"]) if f]
            logged += fields["alternatives"]

        assert any(f.get("actionable") is False for f in logged)
        assert all({"target", "family", "action_type", "role"} <= set(f) for f in logged)


class TestCandidateStaysInTheShadow:
    """``candidate_nba`` не идёт ни в запись Recommendation, ни в ответ человеку.

    Механизм: имя поля встречается в коде приложения ровно в одном модуле —
    политике, которая его строит; строку тени собирает ``nba_fields()``. Новый
    читатель (писатель записи 6.4, рендер ответа) краснит этот тест, и решение
    о нём принимается явно, а не протекает.
    """

    def test_only_the_policy_module_names_the_candidate(self):
        from pathlib import Path

        apps_root = Path(dp.__file__).resolve().parents[1]
        scanned = [
            p
            for p in apps_root.rglob("*.py")
            if "tests" not in p.parts and "migrations" not in p.parts
        ]
        naming = sorted(
            str(p.relative_to(apps_root.parent)).replace("\\", "/")
            for p in scanned
            if "candidate_nba" in p.read_text(encoding="utf-8", errors="replace")
        )

        assert len(scanned) >= 500, f"скан пуст или не тот корень: {apps_root}"
        assert naming == ["apps/orchestrator/decision_policy.py"]


# --------------------------------------------------------------------------- #
# DRF-1945 — боевые словари: safety первой, NBA = NONE без fallback (§3, §5)   #
# --------------------------------------------------------------------------- #
#: Фраза владельца из J1 (§3 BACK_COMFORT).
OWNER_BACK_PHRASE = "Хочу расслабить спину"
#: §3: стоп-фразы I2 — дословно.
OWNER_STOP_PHRASES = ("ноет спина", "болит спина", "простреливает", "онемение", "отдаёт")


def _decide_production(text, *, safety_state="normal", reason_codes=READY, handoff=None):
    """Боевые словари J1/J2 — без подстановки."""
    return dp.decide(
        _evidence(safety_state, reason_codes), handoff=handoff, needs=tx.read_turn_needs(text)
    )


class TestSafetyBlocksTheProductionChoice:
    """§3/§5: цель может быть распознана, выбор NBA — нет. Вход готовности
    недоступен, как на пилоте (τ не откалиброван), — ``candidate_nba`` тоже нет."""

    @pytest.mark.parametrize(
        "safety_state, handoff, status, reason",
        [
            ("stop", None, dp.PolicyStatus.SAFETY_BOUNDARY, dp.POLICY_SAFETY_STOP),
            ("normal", "required", dp.PolicyStatus.SAFETY_BOUNDARY, dp.POLICY_HANDOFF_REQUIRED),
            (
                "unknown",
                None,
                dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE,
                dp.POLICY_SAFETY_VERDICT_UNAVAILABLE,
            ),
        ],
    )
    def test_boundary_or_unknown_safety_gives_no_nba(self, safety_state, handoff, status, reason):
        verdict = _decide_production(
            OWNER_BACK_PHRASE,
            safety_state=safety_state,
            handoff=handoff,
            reason_codes=INPUT_UNAVAILABLE,
        )

        assert verdict.result_status is status
        assert verdict.reason_codes == (reason,)
        assert verdict.primary is None and verdict.alternatives == ()
        assert verdict.candidate_nba is None

    def test_clarify_recognizes_the_target_and_selects_nothing(self):
        verdict = _decide_production(
            OWNER_BACK_PHRASE, safety_state="clarify", reason_codes=INPUT_UNAVAILABLE
        )

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.recognized_targets == ("BACK_COMFORT",)
        assert verdict.primary is None
        assert verdict.candidate_nba is None

    def test_pain_with_an_owner_phrase_recognizes_the_target_and_selects_nothing(self):
        verdict = _decide_production(
            "Хочу расслабить спину, но простреливает", reason_codes=INPUT_UNAVAILABLE
        )

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.reason_codes == (dp.POLICY_PAIN_SIGNAL,)
        assert verdict.recognized_targets == ("BACK_COMFORT",)
        assert verdict.primary is None
        assert verdict.candidate_nba is None


class TestOwnerStopPhrases:
    @pytest.mark.parametrize("text", OWNER_STOP_PHRASES)
    def test_stop_phrase_is_pending_without_nba(self, text):
        verdict = _decide_production(text, reason_codes=INPUT_UNAVAILABLE)

        assert verdict.result_status is dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING
        assert verdict.reason_codes == (dp.POLICY_PAIN_SIGNAL,)
        assert verdict.primary is None
        assert verdict.candidate_nba is None


class TestOwnerButtonsWithoutATarget:
    @pytest.mark.parametrize(
        "text, status, reason",
        [
            (
                "Беспокоят отёки",
                dp.PolicyStatus.SAFETY_CLARIFICATION_PENDING,
                dp.POLICY_HEALTH_SENSITIVE_CONTEXT,
            ),
            (
                "Последнее время сильно устаю",
                dp.PolicyStatus.NBA_TARGET_NOT_RECOGNIZED,
                dp.POLICY_TARGET_NOT_RECOGNIZED,
            ),
            (
                "Хочу больше времени уделять себе",
                dp.PolicyStatus.NBA_TARGET_NOT_RECOGNIZED,
                dp.POLICY_TARGET_NOT_RECOGNIZED,
            ),
            (
                "Готовлюсь к важному событию",
                dp.PolicyStatus.NBA_TARGET_NOT_RECOGNIZED,
                dp.POLICY_TARGET_NOT_RECOGNIZED,
            ),
        ],
    )
    def test_button_without_a_target_selects_nothing(self, text, status, reason):
        verdict = _decide_production(text)

        assert verdict.result_status is status
        assert verdict.reason_codes == (reason,)
        assert verdict.primary is None
        assert verdict.candidate_nba is None


#: J1 + J2 владельца (§3–§4): фраза → ожидаемая тройка.
OWNER_PHRASE_TRIPLES = [
    ("Хочу выглядеть свежее", tx.Triple("FACE_FRESHNESS", "ADDRESS", "PROVIDER_SESSION")),
    ("Хочу снять напряжение", tx.Triple("RELAXATION", "SUPPORT", "PROVIDER_SESSION")),
    ("Хочу расслабить спину", tx.Triple("BACK_COMFORT", "RECOVER", "PROVIDER_SESSION")),
    ("Спина напряжена", tx.Triple("BACK_COMFORT", "RECOVER", "PROVIDER_SESSION")),
    ("Хочу снять зажимы", tx.Triple("BACK_COMFORT", "RECOVER", "PROVIDER_SESSION")),
    ("Устала спина после работы", tx.Triple("BACK_COMFORT", "RECOVER", "PROVIDER_SESSION")),
]


class TestOwnerPhrasesSelectTheOwnerTriple:
    @pytest.mark.parametrize("text, triple", OWNER_PHRASE_TRIPLES)
    def test_ready_input_is_a_clear_primary_with_the_owner_triple(self, text, triple):
        verdict = _decide_production(text)

        assert verdict.result_status is dp.PolicyStatus.CLEAR_PRIMARY
        assert verdict.primary == triple
        assert verdict.alternatives == ()

    @pytest.mark.parametrize("text, triple", OWNER_PHRASE_TRIPLES)
    def test_on_the_pilot_path_the_owner_triple_is_a_candidate(self, text, triple):
        """τ не откалиброван → вход готовности недоступен → тройка в ``candidate_nba``."""
        verdict = _decide_production(text, reason_codes=INPUT_UNAVAILABLE)

        assert verdict.result_status is dp.PolicyStatus.POLICY_INPUT_UNAVAILABLE
        assert verdict.candidate_nba == triple
        assert verdict.primary is None

    def test_tension_in_the_back_is_relaxation_until_the_owner_adds_a_phrase(self):
        """Главное окно 15.09 (Q3): «напряжение в спине» в J1 нет — фраза даёт только
        «хочу снять напряжение». Если владелец добавит фразу про спину, тест краснеет
        и возвращает вопрос о правиле перекрытия фраз."""
        verdict = _decide_production("Хочу снять напряжение в спине")

        assert verdict.result_status is dp.PolicyStatus.CLEAR_PRIMARY
        assert verdict.recognized_targets == ("RELAXATION",)
        assert verdict.primary == tx.Triple("RELAXATION", "SUPPORT", "PROVIDER_SESSION")
        assert verdict.alternatives == ()
