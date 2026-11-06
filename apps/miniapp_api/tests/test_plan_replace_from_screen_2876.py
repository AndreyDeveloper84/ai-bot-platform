# ruff: noqa: F811 — фикстуры берутся по имени из соседнего набора узлов
"""DRF-2876 — замена действующего плана предложением с экрана Mini App.

    POST /customer/plan/replace   {plan_id, replaces_plan_id}
    POST /customer/plan/keep      {plan_id}

Задание владельца (§9): Mini App действует с планом без сообщения в чат.
У нажатия на экране своего вердикта безопасности нет — оно несёт вердикт
ПОСЛЕДНЕГО хода разговора (решение главного окна 08.10): «стоп» минуту назад
в чате не обходится кнопкой на экране.

Замена
* r1 — с вердиктом последнего хода замена уходит каталогу: оба плана и тройка;
* r2 — разговора не было: 409 ``plan_safety_unavailable``, каталог не спрошен;
* r3 — последний ход был «стоп»: каталогу уходит «стоп», его отказ — 409;
* r4 — «уже заменено» — 200, не ошибка;
* r5 — отказы каталога названы раздельно;
* r6 — под гейтом согласия замена закрыта до каталога;
* r7 — идентификаторы — только UUID;
* r8 — флаг выключен: 404, каталог не спрошен.

Отказ от предложения
* k1 — архивирует предложение; вердикта не требует;
* k2 — гейтом согласия не закрыт;
* k3 — флаг выключен: 404.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.integrations.ayla.plan_engine_client import (
    PlanNotFoundError,
    PlanReplacementTargetChangedError,
    PlanSaveSafetyBlockedError,
    PlanTransitionRefusedError,
)
from apps.miniapp_api import views_plan_engine as views
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import (  # noqa: F401 — fixtures
    CLIENT,
    EXT,
    _auth,
    _settings,
    bot_user,
)
from apps.orchestrator.safety.plan_turn import PlanTurnSafety

#: Человек этих узлов назван в списке приёмки Плана; сам замок — test_plan_access_2885.
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("plan_open_to_everyone")]

PROPOSAL = "7c1d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff"
ACTIVE = "5a5a5a5a-1111-4222-8333-999999999999"
BODY = {"plan_id": PROPOSAL, "replaces_plan_id": ACTIVE}
#: Основание обработки (DRF-2967) — что именно в нём лежит, держат узлы гейта.
BASIS = {"state": "granted", "attested_at": "2026-10-09T10:00:00+00:00"}


def _triple(state: str = "NORMAL", revision: int = 14) -> PlanTurnSafety:
    return PlanTurnSafety(
        safety_state=state, safety_policy_version="pre_check-abc", evaluated_at_revision=revision
    )


@pytest.fixture(autouse=True)
def _basis_proven(monkeypatch: pytest.MonkeyPatch) -> None:
    """Человек этих узлов — с основанием; сам гейт держит test_plan_basis_gate_2967."""
    monkeypatch.setattr(views, "plan_processing_refusal", lambda bot_user: None)
    monkeypatch.setattr(views, "plan_consent_basis", lambda bot_user: dict(BASIS))


@pytest.fixture
def last_turn(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {"safety": _triple()}
    monkeypatch.setattr(views, "last_turn_safety_for", lambda bot_user: box["safety"])
    return box


def _post(client: Client, name: str, body: Any):
    return client.post(
        reverse(f"miniapp_api:{name}"),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(),
    )


def _replaced(mocked, replaced: bool = True) -> Any:
    fake = mocked.return_value
    fake.replace_plan.return_value = {"plan": {"plan_id": PROPOSAL}, "replaced": replaced}
    return fake


# ─── замена ──────────────────────────────────────────────────────────────


def test_r1_the_replacement_carries_both_plans_and_the_last_turns_triple(
    client, bot_user, last_turn
) -> None:
    with patch(CLIENT) as mocked:
        fake = _replaced(mocked)
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code == 200, resp.content[:300]
    assert resp.json() == {"replaced": True}
    assert fake.replace_plan.call_args.kwargs == {
        "external_user_id": EXT,
        "plan_id": PROPOSAL,
        "replaces_plan_id": ACTIVE,
        "safety_state": "NORMAL",
        "safety_policy_version": "pre_check-abc",
        "evaluated_at_revision": 14,
        "consent": BASIS,
    }


def test_r2_without_a_last_turn_verdict_the_catalog_is_not_asked(
    client, bot_user, last_turn
) -> None:
    last_turn["safety"] = None
    with patch(CLIENT) as mocked:
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_safety_unavailable"
    mocked.return_value.replace_plan.assert_not_called()


def test_r2_a_person_who_never_talked_has_no_verdict(client, bot_user) -> None:
    """Настоящее чтение, без подмены: разговора нет — вердикта нет."""
    assert views.last_turn_safety_for(bot_user) is None


def test_r3_a_stop_in_the_chat_reaches_the_catalog_and_its_refusal_is_named(
    client, bot_user, last_turn
) -> None:
    last_turn["safety"] = _triple("STOP", 15)
    with patch(CLIENT) as mocked:
        fake = mocked.return_value
        fake.replace_plan.side_effect = PlanSaveSafetyBlockedError("blocked")
        resp = _post(client, "customer_plan_replace", BODY)

    assert fake.replace_plan.call_args.kwargs["safety_state"] == "STOP"
    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_safety_blocked"


def test_r4_an_already_done_replacement_is_not_an_error(client, bot_user, last_turn) -> None:
    with patch(CLIENT) as mocked:
        _replaced(mocked, replaced=False)
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code == 200
    assert resp.json() == {"replaced": False}


@pytest.mark.parametrize(
    ("error", "slug"),
    [
        (PlanReplacementTargetChangedError("x"), "plan_replacement_target_changed"),
        (PlanTransitionRefusedError("x"), "plan_proposal_expired"),
        (PlanNotFoundError("x"), "plan_proposal_expired"),
    ],
)
def test_r5_each_catalog_refusal_keeps_its_own_name(
    client, bot_user, last_turn, error: Exception, slug: str
) -> None:
    with patch(CLIENT) as mocked:
        mocked.return_value.replace_plan.side_effect = error
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code == 409
    assert resp.json()["error"] == slug


@pytest.mark.parametrize(
    "refusal", ["PLAN_DELETION_REQUESTED", "PLAN_CONSENT_REQUIRED", "PLAN_BASIS_UNAVAILABLE"]
)
def test_r6_under_the_consent_gate_the_replacement_never_reaches_the_catalog(
    client, bot_user, last_turn, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    with patch(CLIENT) as mocked:
        _replaced(mocked)
        open_resp = _post(client, "customer_plan_replace", BODY)
        assert open_resp.status_code == 200  # положительный контроль: без отказа проходит
        mocked.reset_mock()
        monkeypatch.setattr(views, "plan_processing_refusal", lambda bot_user: refusal)
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code in (403, 423, 503)
    mocked.assert_not_called()


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"plan_id": PROPOSAL},
        {"plan_id": "не-идентификатор", "replaces_plan_id": ACTIVE},
        {"plan_id": PROPOSAL, "replaces_plan_id": 7},
        [PROPOSAL, ACTIVE],
    ],
)
def test_r7_only_plan_ids_are_accepted(client, bot_user, last_turn, body: Any) -> None:
    with patch(CLIENT) as mocked:
        resp = _post(client, "customer_plan_replace", body)

    assert resp.status_code == 400
    assert resp.json()["error"] == "malformed"
    mocked.return_value.replace_plan.assert_not_called()


def test_r8_switched_off_nothing_is_called(client, bot_user, last_turn, settings) -> None:
    settings.PLAN_ENGINE_ENABLED = False
    with patch(CLIENT) as mocked:
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code == 404
    assert resp.json()["error"] == "plan_engine_disabled"
    mocked.assert_not_called()


def test_r9_the_basis_of_processing_rides_with_the_replacement(
    client, bot_user, last_turn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DRF-2967: замена — запись, основание обработки едет с ней; каким оно
    было у человека в момент нажатия, таким и ушло."""
    other = {"state": "granted", "attested_at": "2026-10-01T00:00:00+00:00"}
    monkeypatch.setattr(views, "plan_consent_basis", lambda bot_user: dict(other))
    with patch(CLIENT) as mocked:
        fake = _replaced(mocked)
        _post(client, "customer_plan_replace", BODY)

    assert fake.replace_plan.call_args.kwargs["consent"] == other


@pytest.mark.parametrize(
    ("error_name", "status", "slug"),
    [
        ("PlanDeletionInProgressError", 423, "deletion_requested"),
        ("PlanConsentRequiredError", 403, "plan_consent_required"),
    ],
)
def test_r10_a_catalog_refusal_on_the_basis_is_named_like_the_bots_own(
    client, bot_user, last_turn, error_name: str, status: int, slug: str
) -> None:
    import apps.integrations.ayla.plan_engine_client as client_mod

    with patch(CLIENT) as mocked:
        mocked.return_value.replace_plan.side_effect = getattr(client_mod, error_name)("x")
        resp = _post(client, "customer_plan_replace", BODY)

    assert resp.status_code == status, resp.content
    assert resp.json()["error"] == slug


def test_k5_keep_sends_no_basis_it_is_not_a_record_of_processing(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        _post(client, "customer_plan_keep", {"plan_id": PROPOSAL})

    assert "consent" not in mocked.return_value.archive_plan.call_args.kwargs


# ─── отказ от предложения ────────────────────────────────────────────────


def test_k1_keep_archives_the_proposal_and_needs_no_verdict(client, bot_user, last_turn) -> None:
    last_turn["safety"] = None
    with patch(CLIENT) as mocked:
        fake = mocked.return_value
        resp = _post(client, "customer_plan_keep", {"plan_id": PROPOSAL})

    assert resp.status_code == 200, resp.content[:300]
    assert resp.json() == {"kept": True}
    assert fake.archive_plan.call_args.kwargs == {"external_user_id": EXT, "plan_id": PROPOSAL}
    fake.replace_plan.assert_not_called()


@pytest.mark.parametrize(
    "refusal", ["PLAN_DELETION_REQUESTED", "PLAN_CONSENT_REQUIRED", "PLAN_BASIS_UNAVAILABLE"]
)
def test_k2_keep_is_open_under_the_consent_gate(
    client, bot_user, monkeypatch: pytest.MonkeyPatch, refusal: str
) -> None:
    monkeypatch.setattr(views, "plan_processing_refusal", lambda bot_user: refusal)
    with patch(CLIENT) as mocked:
        resp = _post(client, "customer_plan_keep", {"plan_id": PROPOSAL})

    assert resp.status_code == 200
    mocked.return_value.archive_plan.assert_called_once()


def test_k3_switched_off_nothing_is_called(client, bot_user, settings) -> None:
    settings.PLAN_ENGINE_ENABLED = False
    with patch(CLIENT) as mocked:
        resp = _post(client, "customer_plan_keep", {"plan_id": PROPOSAL})

    assert resp.status_code == 404
    mocked.assert_not_called()


def test_k4_a_proposal_that_is_gone_is_named(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        mocked.return_value.archive_plan.side_effect = PlanTransitionRefusedError("x")
        resp = _post(client, "customer_plan_keep", {"plan_id": PROPOSAL})

    assert resp.status_code == 409
    assert resp.json()["error"] == "plan_proposal_expired"
