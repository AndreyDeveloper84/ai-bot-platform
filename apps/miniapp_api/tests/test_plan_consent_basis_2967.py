# ruff: noqa: F811 — фикстуры соседних наборов импортируются и принимаются параметрами
"""DRF-2967 — утверждение основания для каталога и его отказы.

Каталог — вторая линия гейта плана. Реестра согласий у него нет, поэтому
бот на каждом вызове, который план обрабатывает, называет основание: вид
согласия, версию текста и время выдачи действующей записи. Каталог
сравнивает это время с известным ему отзывом и отвечает своими отказами —
бот показывает их теми же именами, что и собственный гейт.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.integrations.ayla.plan_engine_client import (
    PlanConsentRequiredError,
    PlanDeletionInProgressError,
)
from apps.miniapp_api.tests.test_customer_assistant_2799 import (
    _person_shell as _bare_person_shell,
)
from apps.miniapp_api.tests.test_plan_basis_gate_2967 import (  # noqa: F401 — fixtures
    _fresh_quota,
    _miniapp,
)
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import CLIENT, _post
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import (  # noqa: F401 — fixtures
    bot_user as proxy_person,
)
from apps.miniapp_api.tests.test_plan_from_chat_e2e_2885 import (  # noqa: F401 — fixtures
    _ask,
    _bot_token,
    _concierge,
    _no_ayla_link,
    _no_intent_llm,
    _on,
    _redis,
    _state_store,
    catalog,
    tenant,
    wire,
)
from apps.orchestrator import plan_engine_card as card
from apps.orchestrator import plan_gate
from apps.orchestrator.tests.test_plan_engine_card_2885 import TOKEN, FakeCatalog

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
PERSON = "2967201"
SAVE = f"{card.CB_SAVE_PREFIX}{TOKEN}"


def _grant(shell: BotUser, *, version: str = "privacy-v2.0") -> ConsentRecord:
    return record_global_consent(shell, source="test", document_version=version)


def _captured(record: ConsentRecord, at: datetime) -> None:
    ConsentRecord.all_tenants.filter(pk=record.pk).update(captured_at=at)


# ─── что утверждается ────────────────────────────────────────────────────────


def test_b1_the_basis_names_the_active_grant(tenant) -> None:
    shell = _bare_person_shell(tenant, PERSON)
    record = _grant(shell)
    record.refresh_from_db()

    basis = plan_gate.plan_consent_basis(shell)

    assert basis == {
        "type": "personal_data",
        "document_version": "privacy-v2.0",
        "granted_at": record.captured_at.isoformat(),
    }
    # С часовым поясом: без него каталог считает, что утверждения нет.
    assert datetime.fromisoformat(basis["granted_at"]).tzinfo is not None


def test_b2_a_grant_without_a_text_version_is_named_unversioned(tenant) -> None:
    shell = _bare_person_shell(tenant, PERSON)
    _grant(shell, version="")

    basis = plan_gate.plan_consent_basis(shell)

    assert basis is not None
    assert basis["document_version"] == "unversioned"


def test_b3_after_a_withdrawal_and_a_new_grant_the_new_grant_is_named(tenant) -> None:
    """Каталог сравнивает время: утверждение по старой записи после отзыва
    он отверг бы. Называется НОВАЯ запись, отозванная — никогда."""
    shell = _bare_person_shell(tenant, PERSON)
    now = timezone.now()
    old = _grant(shell, version="privacy-v1.0")
    _captured(old, now - timedelta(days=30))
    ConsentRecord.all_tenants.filter(pk=old.pk).update(withdrawn_at=now - timedelta(days=10))
    assert plan_gate.plan_consent_basis(shell) is None  # отозвано — утверждать нечего

    new = _grant(shell, version="privacy-v2.0")
    _captured(new, now - timedelta(days=1))

    basis = plan_gate.plan_consent_basis(shell)

    assert basis is not None
    assert basis["document_version"] == "privacy-v2.0"
    assert datetime.fromisoformat(basis["granted_at"]) == now - timedelta(days=1)


def test_b4_no_grant_no_basis(tenant) -> None:
    shell = _bare_person_shell(tenant, PERSON)
    other = _bare_person_shell(tenant, "2967202")
    _grant(other)
    assert plan_gate.plan_consent_basis(other) is not None  # близнец: у согласившегося есть

    assert plan_gate.plan_consent_basis(shell) is None


# ─── утверждение едет в каталог ──────────────────────────────────────────────


def test_b5_the_basis_rides_with_compose_and_save(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    shell = _bare_person_shell(tenant, PERSON)
    _grant(shell)
    expected = plan_gate.plan_consent_basis(shell)
    assert expected is not None

    _ask(client, card.CB_COMPOSE, as_user=PERSON)
    saved = _ask(client, SAVE, as_user=PERSON)

    assert saved.json()["answer"] == f"{card.PLAN_SAVED} · {card.TEST_MARK}"
    assert catalog.composed[0]["consent"] == expected
    assert catalog.saved[0]["consent"] == expected


def test_b5_the_miniapp_compose_endpoint_sends_the_basis(
    client: Client, proxy_person: BotUser, _miniapp
) -> None:
    expected = plan_gate.plan_consent_basis(proxy_person)
    assert expected is not None

    with patch(CLIENT) as mocked:
        mocked.return_value.compose_decision.return_value = {
            "outcome": "NO_GOAL",
            "decision": None,
        }
        _post(client)

    assert mocked.return_value.compose_decision.call_args.kwargs["consent"] == expected


# ─── отказы каталога — теми же именами ───────────────────────────────────────

CATALOG_REFUSALS = [
    (PlanDeletionInProgressError("deletion_in_progress"), plan_gate.PLAN_DELETION_REQUESTED),
    (PlanConsentRequiredError("withdrawn"), plan_gate.PLAN_CONSENT_REQUIRED),
    (PlanConsentRequiredError("not_attested"), plan_gate.PLAN_CONSENT_REQUIRED),
]


@pytest.mark.parametrize(("error", "name"), CATALOG_REFUSALS)
def test_b6_a_catalog_refusal_on_compose_is_named_like_the_bots_own(
    error: Exception, name: str, client: Client, tenant, wire, catalog: FakeCatalog, monkeypatch
) -> None:
    _grant(_bare_person_shell(tenant, PERSON))

    def _refuse(**kwargs: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(catalog, "compose_decision", _refuse)

    answer = _ask(client, card.CB_COMPOSE, as_user=PERSON).json()["answer"]

    assert answer == f"{name} · {card.TEST_MARK}"


@pytest.mark.parametrize(("error", "name"), CATALOG_REFUSALS)
def test_b6_a_catalog_refusal_on_save_is_named_like_the_bots_own(
    error: Exception, name: str, client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    _grant(_bare_person_shell(tenant, PERSON))
    _ask(client, card.CB_COMPOSE, as_user=PERSON)
    catalog.save_error = error

    answer = _ask(client, SAVE, as_user=PERSON).json()["answer"]

    assert answer == f"{name} · {card.TEST_MARK}"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (PlanDeletionInProgressError("deletion_in_progress"), 423, "deletion_requested"),
        (PlanConsentRequiredError("withdrawn"), 403, "plan_consent_required"),
    ],
)
def test_b7_the_miniapp_shows_a_catalog_refusal_like_the_bots_own(
    error: Exception, status: int, code: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    with patch(CLIENT) as mocked:
        mocked.return_value.compose_decision.side_effect = error
        response = _post(client)

    assert response.status_code == status, response.content
    assert response.json()["error"] == code
