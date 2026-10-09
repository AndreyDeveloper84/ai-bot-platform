# ruff: noqa: F811 — фикстуры берутся по имени из соседнего набора узлов
"""DRF-2876 — сохранённый план нового механизма для экрана «Мой план».

    GET /customer/plan/current

Решение владельца (лист 07.10, п.9–10): раздел «Мой план» один; подписи
шагов — утверждённый клиентский текст способности, технические ключи
человеку не показываются, отсутствующая подпись не придумывается.

* c1 — флаг выключен: 404, каталог не спрашивают;
* c2 — плана нет: 200 ``plan: null`` (экран показывает прежний план);
* c3 — план есть: идентификаторы и подписи шагов, по порядку каталога;
* c4 — ключ способности экрану не уходит;
* c5 — у шага нет подписи: 502 ``plan_step_unlabelled``, частичного плана нет;
* c6 — план без шагов — тоже не «плана нет»;
* c7 — субъект — человек из подписанных данных Mini App;
* c8 — отказ каталога: 502, не «плана нет»;
* c9 — без подписи Mini App — 401.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.integrations.ayla.plan_engine_client import PlanEngineUnavailableError
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import (  # noqa: F401 — fixtures
    CLIENT,
    EXT,
    _auth,
    _settings,
    bot_user,
)

LABELS = {"cap.sleep_routine": "Режим сна", "cap.evening_walk": "Вечерняя прогулка"}


ACTIVE_ID = "5a5a5a5a-1111-4222-8333-999999999999"
PROPOSAL_ID = "7c1d2e3f-aaaa-4bbb-8ccc-ddddeeeeffff"


def _plan(*refs: str, plan_id: str = "plan-2876", status: str = "active") -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "status": status,
        "revision": {
            "steps": [{"step_id": f"s-{i}", "capability_ref": ref} for i, ref in enumerate(refs)]
        },
    }


def _get(client: Client, *, auth: bool = True):
    url = reverse("miniapp_api:customer_plan_current")
    if not auth:
        return client.get(url)
    return client.get(url, HTTP_AUTHORIZATION=_auth())


def _catalog(
    mocked,
    plan: dict[str, Any] | None,
    labels: dict[str, str] | None = None,
    effects: dict[str, str] | None = None,
    proposal: dict[str, Any] | None = None,
) -> Any:
    fake = mocked.return_value
    fake.get_plan_and_proposal.return_value = (plan, proposal)
    fake.capability_details.return_value = {
        key: {"label": label, "expected_effect": (effects or {}).get(key)}
        for key, label in (LABELS if labels is None else labels).items()
    }
    return fake


def test_c1_switched_off_the_catalog_is_not_asked(client, bot_user, settings) -> None:
    settings.PLAN_ENGINE_ENABLED = False
    with patch(CLIENT) as mocked:
        resp = _get(client)

    assert resp.status_code == 404
    assert resp.json()["error"] == "plan_engine_disabled"
    mocked.assert_not_called()


def test_c2_no_saved_plan_is_null_not_an_error(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        fake = _catalog(mocked, None)
        resp = _get(client)

    assert resp.status_code == 200
    assert resp.json() == {"plan": None, "proposal": None, "draft": None}
    fake.capability_details.assert_not_called()


def test_c3_the_saved_plan_comes_as_ids_and_labels_in_catalog_order(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        fake = _catalog(mocked, _plan("cap.sleep_routine", "cap.evening_walk"))
        resp = _get(client)

    assert resp.status_code == 200, resp.content[:300]
    assert resp.json() == {
        "plan": {
            "plan_id": "plan-2876",
            "steps": [
                {"step_id": "s-0", "label": "Режим сна", "why": None},
                {"step_id": "s-1", "label": "Вечерняя прогулка", "why": None},
            ],
        },
        "proposal": None,
        "draft": None,
    }
    assert fake.capability_details.call_args.kwargs["keys"] == [
        "cap.sleep_routine",
        "cap.evening_walk",
    ]


def test_c4_the_capability_key_never_reaches_the_screen(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        _catalog(mocked, _plan("cap.sleep_routine"))
        resp = _get(client)

    body = json.dumps(resp.json(), ensure_ascii=False)
    assert "Режим сна" in body  # положительный контроль: ответ с планом
    assert "cap.sleep_routine" not in body
    assert "capability_ref" not in body


def test_c5_a_step_without_a_label_shows_no_partial_plan(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        _catalog(
            mocked,
            _plan("cap.sleep_routine", "cap.evening_walk"),
            labels={"cap.sleep_routine": "Режим сна"},
        )
        resp = _get(client)

    assert resp.status_code == 502
    assert resp.json()["error"] == "plan_step_unlabelled"
    assert "plan" not in resp.json()


def test_c6_a_plan_without_steps_is_not_no_plan(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        _catalog(mocked, _plan())
        resp = _get(client)

    assert resp.status_code == 502
    assert resp.json()["error"] == "plan_step_unlabelled"


def test_c7_the_subject_is_the_person_from_the_signed_init_data(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        fake = _catalog(mocked, _plan("cap.sleep_routine"))
        _get(client)

    assert fake.get_plan_and_proposal.call_args.kwargs == {"external_user_id": EXT}
    assert fake.capability_details.call_args.kwargs["external_user_id"] == EXT


@pytest.mark.parametrize("failing", ["get_plan_and_proposal", "capability_details"])
def test_c8_a_catalog_refusal_is_not_no_plan(client, bot_user, failing: str) -> None:
    with patch(CLIENT) as mocked:
        fake = _catalog(mocked, _plan("cap.sleep_routine"))
        getattr(fake, failing).side_effect = PlanEngineUnavailableError("down")
        resp = _get(client)

    assert resp.status_code == 502
    assert resp.json()["error"] == "ayla_unavailable"


def test_c9_without_the_miniapp_signature_nothing_is_read(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        resp = _get(client, auth=False)

    assert resp.status_code == 401
    mocked.assert_not_called()


def test_c10_the_catalogs_effect_comes_as_why_and_only_where_it_exists(client, bot_user) -> None:
    """«Почему этот шаг?» — курируемый «ожидаемый эффект» способности; у шага
    без текста — ``null``: экран ссылку не показывает, текст не сочиняется."""
    with patch(CLIENT) as mocked:
        _catalog(
            mocked,
            _plan("cap.sleep_routine", "cap.evening_walk"),
            effects={"cap.sleep_routine": "Помогает ложиться и вставать в одно время."},
        )
        resp = _get(client)

    assert resp.status_code == 200, resp.content[:300]
    steps = resp.json()["plan"]["steps"]
    assert steps[0]["why"] == "Помогает ложиться и вставать в одно время."
    assert steps[1]["why"] is None


# ─── предложение рядом с действующим планом ──────────────────────────────


def test_c11_a_proposal_comes_beside_the_plan_in_effect_and_names_what_it_replaces(
    client, bot_user
) -> None:
    with patch(CLIENT) as mocked:
        fake = _catalog(
            mocked,
            _plan("cap.sleep_routine", plan_id=ACTIVE_ID),
            proposal=_plan("cap.evening_walk", plan_id=PROPOSAL_ID, status="proposed"),
        )
        resp = _get(client)

    assert resp.status_code == 200, resp.content[:300]
    body = resp.json()
    assert body["plan"]["plan_id"] == ACTIVE_ID
    assert body["proposal"] == {
        "plan_id": PROPOSAL_ID,
        "replaces_plan_id": ACTIVE_ID,
        "steps": [{"step_id": "s-0", "label": "Вечерняя прогулка", "why": None}],
    }
    # Подписи обоих планов — одним запросом.
    assert fake.capability_details.call_count == 1
    assert fake.capability_details.call_args.kwargs["keys"] == [
        "cap.sleep_routine",
        "cap.evening_walk",
    ]


def test_c12_a_proposal_that_cannot_be_shown_does_not_hide_the_plan(client, bot_user) -> None:
    with patch(CLIENT) as mocked:
        _catalog(
            mocked,
            _plan("cap.sleep_routine", plan_id=ACTIVE_ID),
            labels={"cap.sleep_routine": "Режим сна"},
            proposal=_plan("cap.evening_walk", plan_id=PROPOSAL_ID, status="proposed"),
        )
        resp = _get(client)

    assert resp.status_code == 200
    assert resp.json()["plan"]["plan_id"] == ACTIVE_ID
    assert resp.json()["proposal"] is None


def test_c13_beside_a_paused_plan_no_replacement_is_offered(client, bot_user) -> None:
    """Каталог заменяет ДЕЙСТВУЮЩИЙ план; у приостановленного заменять нечего."""
    with patch(CLIENT) as mocked:
        _catalog(
            mocked,
            _plan("cap.sleep_routine", plan_id=ACTIVE_ID, status="paused"),
            proposal=_plan("cap.evening_walk", plan_id=PROPOSAL_ID, status="proposed"),
        )
        resp = _get(client)

    assert resp.json()["plan"]["plan_id"] == ACTIVE_ID  # положительный контроль
    assert resp.json()["proposal"] is None
