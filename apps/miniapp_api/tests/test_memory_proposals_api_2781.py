# ruff: noqa: F811 — фикстуры набора памяти импортируются и принимаются параметрами
"""DRF-2781 — ручки ответа на предложение Ayla: подтвердить / исправить (Mini App).

Логика — в ``apps.identity.services.memory_proposals`` и её узлах
(``apps/identity/tests/test_memory_proposals_2781.py``); здесь — провод:
форма ответа, коды отказов, состояние строки на экране.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.miniapp_api.tests.test_customer_memory_2133 import (  # noqa: F401 — fixtures
    _auth,
    _bot_token,
    _get,
    _green,
    _person_gate_open,
    ayla_user_id,
    bot_user,
    tenant,
    upc,
)

pytestmark = pytest.mark.django_db


def _proposal(upc: UserPersonalContext, value: str = "after_18") -> MemoryEntry:
    return _green(
        upc,
        source=MemoryEntry.SOURCE_INFERRED,
        provenance=None,
        status=MemoryEntry.STATUS_ACTIVE,
        last_inferred_at=timezone.now(),
        kind="preference",
        content={"key": "visit_time", "value": value},
    )


def _consent(bot_user: BotUser, consent_type: str) -> None:
    record_global_consent(bot_user, consent_type=consent_type, source="t", document_version="v1")


def _post(client: Client, bot_user: BotUser, name: str, entry_id, body: dict | None = None):
    return client.post(
        reverse(f"miniapp_api:{name}", kwargs={"entry_id": entry_id}),
        data=body or {},
        content_type="application/json",
        HTTP_AUTHORIZATION=_auth(bot_user.channel_user_id),
    )


def test_the_screen_reports_the_state_of_every_row(client, bot_user, upc) -> None:
    _green(upc, content={"key": "visit_time", "value": "morning"})
    _proposal(upc)

    states = {(row["state"], row["value"]) for row in _get(client, bot_user).json()["green"]}

    assert states == {("said", "morning"), ("proposed", "after_18")}


def test_confirm_returns_the_confirmed_row(client, bot_user, upc) -> None:
    _consent(bot_user, ConsentRecord.ConsentType.PREFERENCE_INFERENCE.value)
    proposal = _proposal(upc)

    response = _post(client, bot_user, "customer_memory_confirm", proposal.id)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(proposal.id)
    assert body["state"] == "confirmed"
    assert body["expires_at"] is not None


def test_confirm_without_the_inference_consent_is_409(client, bot_user, upc) -> None:
    proposal = _proposal(upc)

    response = _post(client, bot_user, "customer_memory_confirm", proposal.id)

    assert response.status_code == 409
    assert response.json()["error"] == "consent_required"


def test_correct_writes_the_persons_value(client, bot_user, upc) -> None:
    # Исправление пишет СКАЗАННОЕ — под основанием зелёной памяти, а оно
    # (``can_store_green_memory``) требует оболочки, которую контур знает
    # (S2-2, §2.4). Фикстура строит оболочку ORM-ом, мимо резолвера, — поэтому
    # статус ставится здесь, как у соседей, которые сеют память.
    BotUser.all_tenants.filter(pk=bot_user.pk).update(customer_status=BotUser.CustomerStatus.LINKED)
    bot_user.refresh_from_db(fields=["customer_status"])
    _consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value)
    proposal = _proposal(upc)

    response = _post(client, bot_user, "customer_memory_correct", proposal.id, {"value": "утро"})

    assert response.status_code == 200
    assert response.json()["state"] == "said"
    assert response.json()["value"] == "утро"
    states = {(row["state"], row["value"]) for row in _get(client, bot_user).json()["green"]}
    assert states == {("said", "утро")}


def test_correct_without_a_value_is_400_and_changes_nothing(client, bot_user, upc) -> None:
    _consent(bot_user, ConsentRecord.ConsentType.PERSONAL_DATA.value)
    proposal = _proposal(upc)

    response = _post(client, bot_user, "customer_memory_correct", proposal.id, {"value": "  "})

    assert response.status_code == 400
    assert response.json()["error"] == "bad_value"
    proposal.refresh_from_db()
    assert proposal.status == MemoryEntry.STATUS_ACTIVE


def test_a_said_fact_is_not_a_proposal(client, bot_user, upc) -> None:
    _consent(bot_user, ConsentRecord.ConsentType.PREFERENCE_INFERENCE.value)
    said = _green(upc)

    response = _post(client, bot_user, "customer_memory_confirm", said.id)

    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
