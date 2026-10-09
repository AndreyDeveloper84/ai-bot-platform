# ruff: noqa: F811 — фикстуры соседних наборов импортируются и принимаются параметрами
"""DRF-2967 — отзыв согласия на хранение стирает несохранённое предложение плана.

Замер 09.10 (исполнением): человек собрал план в чате и открыл «Обсудить»,
затем отозвал согласие настоящей службой отзыва — в состоянии разговора
остались и предложение (шаги с подписями), и обсуждение. Гейт их не
показывал, но выход обработки хранился без основания, а после нового
согласия старая карточка «Сохранить» сохраняла предложение, собранное под
прежним согласием.

Здесь отзыв — настоящий (``withdraw_personal_data_for_bot_users``), а не
правка строки согласия: предмет узлов — сам каскад отзыва.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import Client

from apps.consent.services import record_global_consent, withdraw_personal_data_for_bot_users
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_customer_assistant_2799 import (
    _person_shell as _bare_person_shell,
)
from apps.miniapp_api.tests.test_plan_basis_gate_2967 import (  # noqa: F401 — fixtures
    _fresh_quota,
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
from apps.orchestrator.tests.test_plan_engine_card_2885 import (
    ACTIVE_ID,
    KEEP,
    PROPOSAL_ID,
    TOKEN,
    FakeCatalog,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PERSON = "2967301"
SAVE = f"{card.CB_SAVE_PREFIX}{TOKEN}"
DISCUSS = f"{card.CB_DISCUSS_PREFIX}{TOKEN}"
PLAN_KEYS = {card.STATE_KEY, card.DISCUSSION_KEY}


def _shells() -> list[BotUser]:
    return list(BotUser.all_tenants.filter(channel="max", channel_user_id=PERSON))


def _state_keys() -> set[str]:
    """Непустые ключи состояния во всех разговорах человека."""
    keys: set[str] = set()
    for conversation in Conversation.all_tenants.filter(bot_user__in=_shells()):
        state: dict[str, Any] = conversation.skill_state or {}
        keys |= {key for key, value in state.items() if value}
    return keys


def _composed_and_discussing(client: Client, tenant) -> None:
    record_global_consent(_bare_person_shell(tenant, PERSON), source="test")
    _ask(client, card.CB_COMPOSE, as_user=PERSON)
    _ask(client, DISCUSS, as_user=PERSON)
    assert _state_keys() >= PLAN_KEYS  # предложение и обсуждение лежат в состоянии


def _withdraw(capture: Any) -> None:
    with capture(execute=True):
        withdraw_personal_data_for_bot_users(_shells(), source="chat")


def test_w1_a_real_withdrawal_drops_the_proposal_and_the_discussion(
    client: Client, tenant, wire, catalog: FakeCatalog, django_capture_on_commit_callbacks
) -> None:
    _composed_and_discussing(client, tenant)

    _withdraw(django_capture_on_commit_callbacks)

    assert _state_keys() & PLAN_KEYS == set()  # empty-assert-ok: до отзыва оба ключа были


def test_w1_without_a_withdrawal_the_proposal_stays(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    """Близнец: тот же человек, отзыва нет — предложение на месте и сохраняется."""
    _composed_and_discussing(client, tenant)

    saved = _ask(client, SAVE, as_user=PERSON).json()["answer"]

    assert saved == f"{card.PLAN_SAVED} · {card.TEST_MARK}"
    assert len(catalog.saved) == 1


def test_w2_after_a_new_consent_the_old_card_saves_nothing(
    client: Client, tenant, wire, catalog: FakeCatalog, django_capture_on_commit_callbacks
) -> None:
    """Отозвал → согласился снова → нажал старую «Сохранить»: предложение
    собрано под прежним согласием и стёрто отзывом — «устарело», каталог не
    спрошен. Новая сборка под новым согласием работает."""
    _composed_and_discussing(client, tenant)
    _withdraw(django_capture_on_commit_callbacks)
    for shell in _shells():
        record_global_consent(shell, source="test")

    stale = _ask(client, SAVE, as_user=PERSON).json()["answer"]

    assert stale == f"{card.PLAN_PROPOSAL_EXPIRED} · {card.TEST_MARK}"
    assert catalog.saved == []  # empty-assert-ok: близнец w1 видит здесь одну команду

    _ask(client, card.CB_COMPOSE, as_user=PERSON)
    fresh = _ask(client, SAVE, as_user=PERSON).json()["answer"]

    assert fresh == f"{card.PLAN_SAVED} · {card.TEST_MARK}"


def test_w3_the_replace_question_survives_so_the_proposal_can_be_declined(
    client: Client, tenant, wire, catalog: FakeCatalog, django_capture_on_commit_callbacks
) -> None:
    """«Оставить текущий» открыт и под отзывом — ключ вопроса с
    идентификаторами планов отзыв не трогает."""
    record_global_consent(_bare_person_shell(tenant, PERSON), source="test")
    catalog.active_plan_id = ACTIVE_ID
    _ask(client, card.CB_COMPOSE, as_user=PERSON)
    _ask(client, SAVE, as_user=PERSON)
    assert card.REPLACE_KEY in _state_keys()

    _withdraw(django_capture_on_commit_callbacks)

    assert card.REPLACE_KEY in _state_keys()
    assert card.STATE_KEY not in _state_keys()
    kept = _ask(client, KEEP, as_user=PERSON).json()["answer"]
    assert kept == f"{card.PLAN_KEPT} · {card.TEST_MARK}"
    assert catalog.archived == [PROPOSAL_ID]


def test_w4_a_withdrawal_on_another_shell_drops_the_proposal_here(
    client: Client, tenant, wire, catalog: FakeCatalog, django_capture_on_commit_callbacks
) -> None:
    """Согласие держится на человеке, и отзыв на любой оболочке закрывает его
    целиком (``has_person_consent``). Отзыв пришёл по ДРУГОЙ оболочке, под
    другим тенантом, — предложение уходит из разговора этой."""
    from apps.consent.services import has_person_consent

    _composed_and_discussing(client, tenant)
    other = Tenant.objects.create(slug="drop-2967-other", name="Other")
    second = BotUser.all_tenants.create(tenant=other, channel="max", channel_user_id=PERSON)
    record_global_consent(second, source="test")
    assert Conversation.all_tenants.filter(bot_user=second).count() == 0  # разговор — не её

    with django_capture_on_commit_callbacks(execute=True):
        withdraw_personal_data_for_bot_users([second], source="miniapp")

    assert has_person_consent(second, "personal_data") is False
    assert _state_keys() & PLAN_KEYS == set()  # empty-assert-ok: до отзыва оба ключа были


def test_w5_a_failed_cleanup_does_not_cost_the_withdrawal(
    client: Client,
    tenant,
    wire,
    catalog: FakeCatalog,
    django_capture_on_commit_callbacks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Очистка упала — согласие всё равно отозвано, а гейт предложение не отдаёт."""
    from apps.consent.models import ConsentRecord
    from apps.conversations import services as conversation_services

    _composed_and_discussing(client, tenant)

    def _broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("state is not writable")

    monkeypatch.setattr(conversation_services, "write_skill_state", _broken)

    _withdraw(django_capture_on_commit_callbacks)

    active = ConsentRecord.all_tenants.filter(
        bot_user__in=_shells(),
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        withdrawn_at__isnull=True,
    )
    assert active.count() == 0  # empty-assert-ok: до отзыва действующая запись была
    monkeypatch.undo()
    refused = _ask(client, SAVE, as_user=PERSON).json()["answer"]
    assert refused == f"PLAN_CONSENT_REQUIRED · {card.TEST_MARK}"
