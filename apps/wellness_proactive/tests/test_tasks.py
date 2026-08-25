"""Повод OBSERVE: правила, оба гейта, след и стража «только коды» (DRF-1344).

Случаи, которые тикет называет обязательными:

* закрытый гейт получателя -> ничего не отправлено, след называет гейт
  (TestRecipientGateTrace)
* срабатывание ``vet_outbound`` -> отправлено ничего, а не заглушка
  (TestOutboundSafetyGate)
* ``no_action`` — валидный зелёный результат (TestNoAction)
* grep-тест: документ с ``progress_state=derived`` обрабатывается, и ни в
  одном решении/логе/audit-записи нет чисел наблюдений — при том что коды
  ЕСТЬ (TestCodesOnlyTrace)

Дат в фикстурах нет константных: все штампы — относительно ``timezone.now()``.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser
from apps.integrations.ayla.wellness_context_client import (
    OutcomeState,
    WellnessContext,
    WellnessContextUnavailableError,
)
from apps.tenancy.models import Tenant
from apps.wellness_proactive import tasks

pytestmark = pytest.mark.django_db

PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value
HEALTH = ConsentRecord.ConsentType.HEALTH.value


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="wp-salon", name="Salon", timezone="Europe/Moscow")


def grant(bot_user: BotUser, consent_type: str) -> ConsentRecord:
    """Одна активная запись согласия данного типа."""
    return ConsentRecord.all_tenants.create(
        tenant=bot_user.tenant,
        bot_user=bot_user,
        consent_type=consent_type,
        granted=True,
        source="test:fixture",
    )


def make_user(
    tenant: Tenant,
    *,
    suffix: str = "1",
    opt_out: bool = False,
    consents: tuple[str, ...] = (PERSONAL_DATA, HEALTH),
) -> BotUser:
    """Получатель, проходящий оба согласия, если флаги не говорят иного."""
    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"wp-{suffix}",
        chat_id=f"chat-wp-{suffix}",
        proactive_messages_opt_out=opt_out,
        consent_at=timezone.now() - timedelta(days=30) if consents else None,
    )
    for consent_type in consents:
        grant(user, consent_type)
    return user


def outcome(
    target: str = "weight",
    *,
    progress_state: str = "no_observations",
    horizon_status: str = "active",
    link_status: str = "linked",
) -> OutcomeState:
    return OutcomeState(
        target=target,
        link_status=link_status,
        horizon_status=horizon_status,
        progress_state=progress_state,
    )


def context_reader(context: WellnessContext):
    return lambda _ext: context


def only(decisions, bot_user):
    return next(d for d in decisions if d.bot_user_id == bot_user.pk)


# ---------------------------------------------------------------------------
# Правила повода — чистая функция над документом
# ---------------------------------------------------------------------------


class TestOccasionRules:
    def test_gated_document_is_no_occasion(self) -> None:
        """Сегодняшнее состояние контура: гейты закрыты на стороне Ayla."""
        reason, occasions = tasks.evaluate_document(WellnessContext(has_plan=False, gated=True))
        assert reason == "gated"
        assert occasions == []

    def test_no_plan_is_no_occasion(self) -> None:
        reason, _ = tasks.evaluate_document(WellnessContext(has_plan=False))
        assert reason == "no_plan"

    def test_plan_without_outcomes_is_no_occasion(self) -> None:
        reason, _ = tasks.evaluate_document(WellnessContext(has_plan=True))
        assert reason == "no_outcomes"

    @pytest.mark.parametrize("state", sorted(tasks.TRIGGER_PROGRESS_STATES))
    def test_empty_series_states_raise_observe(self, state: str) -> None:
        reason, occasions = tasks.evaluate_document(
            WellnessContext(has_plan=True, outcomes=(outcome(progress_state=state),))
        )
        assert reason == "observe_due"
        assert occasions == [
            {
                "family": "OBSERVE",
                "outcome": "weight",
                "progress_state": state,
                "horizon_status": "active",
            }
        ]

    def test_elapsed_horizon_raises_observe(self) -> None:
        reason, occasions = tasks.evaluate_document(
            WellnessContext(
                has_plan=True,
                outcomes=(outcome(progress_state="on_track", horizon_status="elapsed"),),
            )
        )
        assert reason == "observe_due"
        assert occasions[0]["horizon_status"] == "elapsed"

    def test_observed_series_is_no_action(self) -> None:
        """``derived`` — ряд живой, повода нет. no_action — зелёный результат."""
        reason, occasions = tasks.evaluate_document(
            WellnessContext(has_plan=True, outcomes=(outcome(progress_state="derived"),))
        )
        assert reason == "no_action"
        assert occasions == []

    def test_only_codes_ever_leave_the_rule(self) -> None:
        """В кодах повода нет полей под значения — ни одного числа ряда."""
        _, occasions = tasks.evaluate_document(
            WellnessContext(has_plan=True, outcomes=(outcome(),))
        )
        assert all(isinstance(value, str) for value in occasions[0].values())


# ---------------------------------------------------------------------------
# Гейт получателя: оба согласия, и след называет сработавший гейт
# ---------------------------------------------------------------------------


class TestRecipientGateTrace:
    def test_missing_health_consent_blocks_and_names_the_gate(self, tenant: Tenant) -> None:
        user = make_user(tenant, consents=(PERSONAL_DATA,))
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(WellnessContext(has_plan=True))
        )
        decision = only(decisions, user)
        assert decision.occasion is False
        assert decision.reason == "no_health_consent"
        assert decision.gate == "recipient"

    def test_withdrawn_personal_data_blocks_and_names_the_gate(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        ConsentRecord.all_tenants.filter(bot_user=user, consent_type=PERSONAL_DATA).update(
            withdrawn_at=timezone.now()
        )
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(WellnessContext(has_plan=True))
        )
        decision = only(decisions, user)
        assert decision.reason == "consent_withdrawn"
        assert decision.gate == "recipient"

    def test_never_consented_blocks(self, tenant: Tenant) -> None:
        user = make_user(tenant, consents=())
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(WellnessContext(has_plan=True))
        )
        assert only(decisions, user).reason == "no_consent"

    def test_opt_out_is_not_even_a_candidate(self, tenant: Tenant) -> None:
        user = make_user(tenant, opt_out=True)
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(WellnessContext(has_plan=True))
        )
        assert all(d.bot_user_id != user.pk for d in decisions)

    def test_the_gate_runs_before_ayla_is_read(self, tenant: Tenant) -> None:
        """Health-class документ не читается для того, кому писать нельзя."""
        user = make_user(tenant, consents=(PERSONAL_DATA,))

        def spy(_ext):
            raise AssertionError("fetch must not run for a gated recipient")

        decisions = tasks.plan_observe_occasions(fetch=spy)
        assert only(decisions, user).reason == "no_health_consent"

    def test_the_gate_is_the_shared_parametric_one(self, tenant: Tenant) -> None:
        """Делегация, не четвёртая копия гейта: вызов идёт с ОБОИМИ согласиями."""
        from apps.notifications import proactive

        assert tasks.consent_blocker is proactive.consent_blocker
        user = make_user(tenant)
        with patch.object(tasks, "consent_blocker", return_value="opt_out") as gate:
            decisions = tasks.plan_observe_occasions(
                fetch=context_reader(WellnessContext(has_plan=True))
            )
        assert only(decisions, user).reason == "opt_out"
        gate.assert_called_once()
        assert gate.call_args.kwargs["required_consents"] == (PERSONAL_DATA, HEALTH)

    def test_blocked_recipient_leaves_an_audit_trail(self, tenant: Tenant) -> None:
        """След, по которому оператор видит, КАКОЙ гейт сработал."""
        user = make_user(tenant, consents=(PERSONAL_DATA,))
        with patch.object(tasks, "enabled", return_value=True):
            tasks.evaluate_observe_occasions()
        row = AuditLog.all_tenants.get(action=tasks.AUDIT_ACTION, target_id=user.pk)
        assert row.payload["reason"] == "no_health_consent"
        assert row.payload["gate"] == "recipient"
        assert row.payload["occasion"] is False

    def test_fully_consenting_recipient_passes_the_gate(self, tenant: Tenant) -> None:
        """Положительная стража на тех же данных: гейт — не стена."""
        user = make_user(tenant)
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(WellnessContext(has_plan=True, outcomes=(outcome(),)))
        )
        assert only(decisions, user).reason == "observe_due"


# ---------------------------------------------------------------------------
# Гейт текста: срабатывание = отправлено ничего, а не заглушка
# ---------------------------------------------------------------------------


class TestOutboundSafetyGate:
    def test_a_vet_hit_sends_nothing_and_not_a_stub(self, tenant: Tenant) -> None:
        from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT, OutboundVerdict

        user = make_user(tenant)
        with patch(
            "apps.orchestrator.safety.outbound.evaluate_outbound",
            return_value=OutboundVerdict(
                allowed=False, text=REPLACEMENT_TEXT, categories=("medical_claims",)
            ),
        ):
            decisions = tasks.plan_observe_occasions(
                fetch=context_reader(WellnessContext(has_plan=True, outcomes=(outcome(),)))
            )
        decision = only(decisions, user)
        assert decision.reason == "outbound_safety_medical_claims"
        assert decision.gate == "text"
        assert REPLACEMENT_TEXT not in json.dumps(decision.as_log())

    def test_vet_receives_codes_only(self, tenant: Tenant) -> None:
        """Единственное outbound-содержимое задачи — сериализация кодов повода."""
        make_user(tenant)
        seen = {}
        real_vet = tasks.vet_outbound

        def spy(text):
            seen["text"] = text
            return real_vet(text)

        with patch.object(tasks, "vet_outbound", side_effect=spy):
            tasks.plan_observe_occasions(
                fetch=context_reader(WellnessContext(has_plan=True, outcomes=(outcome(),)))
            )
        assert seen["text"] == "OBSERVE:weight:no_observations"

    def test_our_own_codes_pass_the_real_gate(self, tenant: Tenant) -> None:
        """Регрессия на будущее: если правка добавит в payload блокируемую
        форму, сломается здесь, а не молча на пилоте."""
        user = make_user(tenant)
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(WellnessContext(has_plan=True, outcomes=(outcome(),)))
        )
        assert only(decisions, user).reason == "observe_due"


# ---------------------------------------------------------------------------
# no_action — валидный зелёный результат
# ---------------------------------------------------------------------------


class TestNoAction:
    def test_no_action_is_green_not_an_error(self, tenant: Tenant) -> None:
        user = make_user(tenant)
        decisions = tasks.plan_observe_occasions(
            fetch=context_reader(
                WellnessContext(has_plan=True, outcomes=(outcome(progress_state="derived"),))
            )
        )
        decision = only(decisions, user)
        assert decision.reason == "no_action"
        assert decision.occasion is False
        assert decision.gate == ""

    def test_no_action_writes_no_audit_row(self, tenant: Tenant, caplog) -> None:
        """Массовое зелёное состояние — лог, не audit: иначе каждый тик шумит."""
        user = make_user(tenant)
        with (
            patch.object(tasks, "enabled", return_value=True),
            patch.object(
                tasks,
                "_fetch_wellness_context",
                side_effect=context_reader(
                    WellnessContext(has_plan=True, outcomes=(outcome(progress_state="derived"),))
                ),
            ),
            caplog.at_level(logging.INFO, logger="apps.wellness_proactive.tasks"),
        ):
            result = tasks.evaluate_observe_occasions()
        assert result["deliverable"] == 0
        assert not AuditLog.all_tenants.filter(
            action=tasks.AUDIT_ACTION, target_id=user.pk
        ).exists()
        assert "no_action" in caplog.text


# ---------------------------------------------------------------------------
# Grep-стража: только коды, никогда значения
# ---------------------------------------------------------------------------


class TestCodesOnlyTrace:
    """Ни в payload, ни в логе, ни в аудите не встречается значение веса.

    Документ несёт числа наблюдений (в ``plan`` и рядом с кодами outcomes),
    и оба получателя обрабатываются: у одного ``derived`` (no_action), у
    другого ``no_observations`` (observe_due). Отрицательные утверждения
    держатся положительной стражей на тех же данных: коды при этом ЕСТЬ.
    """

    #: Числа, которые не должны пережить конвейер.
    OBSERVATION_VALUES = ("71.8", "70.35")

    def _document(self, progress_state: str) -> WellnessContext:
        # Документ собирается ЧЕРЕЗ проводной парсер, а не конструктором:
        # значения наблюдений существуют на проводе — и должны умереть там.
        from apps.integrations.ayla.wellness_context_client import _context_from_wire

        return _context_from_wire(
            {
                "data": {
                    "plan": {"code": "p", "target_value": 71.8, "baseline_value": 70.35},
                    "outcomes": [
                        {
                            "target": "weight",
                            "link_status": "linked",
                            "horizon_status": "active",
                            "progress_state": progress_state,
                            "last_value": 71.8,
                            "baseline_value": 70.35,
                        }
                    ],
                    "gated": None,
                }
            }
        )

    def test_no_observation_value_reaches_decisions_logs_or_audit(
        self, tenant: Tenant, caplog
    ) -> None:
        derived_user = make_user(tenant, suffix="derived")
        due_user = make_user(tenant, suffix="due")
        docs = {
            f"bot:max:{derived_user.channel_user_id}": self._document("derived"),
            f"bot:max:{due_user.channel_user_id}": self._document("no_observations"),
        }

        with (
            patch.object(tasks, "enabled", return_value=True),
            patch.object(tasks, "_fetch_wellness_context", side_effect=lambda ext: docs[ext]),
            patch("apps.channels.max.outbound.send_message") as send,
            caplog.at_level(logging.INFO, logger="apps.wellness_proactive.tasks"),
        ):
            result = tasks.evaluate_observe_occasions()

        # Положительная стража: оба сценария реально обработаны.
        assert result["planned"] == 2
        assert result["deliverable"] == 1
        send.assert_not_called()  # отправки нет конструкционно

        audit_rows = list(AuditLog.all_tenants.filter(action=tasks.AUDIT_ACTION))
        assert audit_rows, "observe_due должен оставить audit-след"

        artefacts = caplog.text + json.dumps([row.payload for row in audit_rows])
        for value in self.OBSERVATION_VALUES:
            assert value not in artefacts

        # ...и коды на месте: повод виден как повод, состояние ряда — как код.
        assert "OBSERVE" in artefacts
        assert "no_observations" in artefacts
        assert "no_action" in artefacts


# ---------------------------------------------------------------------------
# Задача: выключатель и «отправлено ничего»
# ---------------------------------------------------------------------------


class TestTask:
    def test_disabled_task_touches_nothing(self, tenant: Tenant, settings) -> None:
        make_user(tenant)
        settings.WELLNESS_PROACTIVE_ENABLED = False
        with patch.object(tasks, "_fetch_wellness_context") as fetch:
            result = tasks.evaluate_observe_occasions()
        assert result["planned"] == 0
        fetch.assert_not_called()

    def test_enabled_task_sends_nothing_ever(self, tenant: Tenant, settings) -> None:
        """Даже с поводом и открытыми гейтами наружу не уходит ничего."""
        user = make_user(tenant)
        settings.WELLNESS_PROACTIVE_ENABLED = True
        with (
            patch.object(
                tasks,
                "_fetch_wellness_context",
                side_effect=context_reader(WellnessContext(has_plan=True, outcomes=(outcome(),))),
            ),
            patch("apps.channels.max.outbound.send_message") as send,
        ):
            result = tasks.evaluate_observe_occasions()
        assert result["deliverable"] == 1
        send.assert_not_called()
        row = AuditLog.all_tenants.get(action=tasks.AUDIT_ACTION, target_id=user.pk)
        assert row.payload["reason"] == "observe_due"
        assert row.payload["occasions"] == [
            {
                "family": "OBSERVE",
                "outcome": "weight",
                "progress_state": "no_observations",
                "horizon_status": "active",
            }
        ]

    def test_ayla_failure_skips_rather_than_crashes(self, tenant: Tenant) -> None:
        user = make_user(tenant)

        def boom(_ext):
            raise WellnessContextUnavailableError("server: HTTP 502")

        decisions = tasks.plan_observe_occasions(fetch=boom)
        assert only(decisions, user).reason == "ayla_unavailable"
