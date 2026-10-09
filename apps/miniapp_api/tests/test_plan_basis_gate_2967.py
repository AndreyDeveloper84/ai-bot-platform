# ruff: noqa: F811 — фикстуры соседних наборов импортируются и принимаются параметрами
"""DRF-2967 — план обрабатывается только человеку с основанием.

Замер 09.10: человек без согласия на хранение, с отозванным согласием и с
живой заявкой на удаление собирал и сохранял план — каталог получал оба
вызова, бот отвечал ``PLAN_SAVED``. Здесь тот же путь, что в замере:
настоящий ход чата Mini App → глобальный ход → карточка; каталог подменён
заглушкой, которая записывает вызовы.

Четыре состояния владельца:

1. согласия не было — закрыто;
2. согласие отозвано — закрыто;
3. живая заявка на удаление — закрыто;
4. действующее согласие без заявки — обычная работа.

Закрыто всё, где план ОБРАБАТЫВАЕТСЯ: сборка, правка, пересборка, обсуждение
с моделью, сохранение. Показ своего сохранённого плана открыт во всех трёх.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, UserPersonalContext
from apps.identity.services.deletion_gate import mark_deletion_requested
from apps.miniapp_api.tests.test_customer_assistant_2799 import (
    _person_shell as _bare_person_shell,
)
from apps.miniapp_api.tests.test_plan_decision_proxy_2879 import (
    BOT_TOKEN,
    CLIENT,
    NO_PLAN,
    _auth,
    _post,
)
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
from apps.orchestrator.tests.test_plan_engine_card_2885 import (
    ACTIVE_ID,
    KEEP,
    PROPOSAL_ID,
    REPLACE,
    TOKEN,
    FakeCatalog,
    _saved_plan,
)

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value

NEVER_CONSENTED = "never_consented"
CONSENT_WITHDRAWN = "consent_withdrawn"
DELETION_REQUESTED = "deletion_requested"

#: Состояние → имя отказа. Заявка на удаление закрывает и при действующем
#: согласии — у этого человека оно не отозвано.
BLOCKED = {
    NEVER_CONSENTED: plan_gate.PLAN_CONSENT_REQUIRED,
    CONSENT_WITHDRAWN: plan_gate.PLAN_CONSENT_REQUIRED,
    DELETION_REQUESTED: plan_gate.PLAN_DELETION_REQUESTED,
}

#: Свой аккаунт на состояние: лимит чата считается по человеку.
ACCOUNTS = {
    NEVER_CONSENTED: "2967101",
    CONSENT_WITHDRAWN: "2967102",
    DELETION_REQUESTED: "2967103",
}
CONSENTING = "2967104"
ACCOUNTS["consenting"] = "2967107"

#: Входы, где план обрабатывается.
PROCESSING_TAPS = {
    "compose": card.CB_COMPOSE,
    "save": f"{card.CB_SAVE_PREFIX}{TOKEN}",
    "edit": f"{card.CB_EDIT_PREFIX}{TOKEN}",
    "drop": f"{card.CB_DROP_PREFIX}{TOKEN}:0",
    "discuss": f"{card.CB_DISCUSS_PREFIX}{TOKEN}",
    "discuss_saved": f"{card.CB_DISCUSS_PREFIX}{card.DISCUSS_SAVED}",
}


@pytest.fixture(autouse=True)
def _fresh_quota() -> None:
    """Лимит чата считается по человеку и живёт в кеше между узлами."""
    from django.core.cache import cache

    cache.clear()


def _shells(person: str) -> list[BotUser]:
    return list(BotUser.all_tenants.filter(channel="max", channel_user_id=person))


def _withdraw(person: str) -> None:
    ConsentRecord.all_tenants.filter(bot_user__in=_shells(person), consent_type=PD).update(
        withdrawn_at=timezone.now()
    )


def _request_deletion(person: str) -> None:
    ayla_user_id = uuid.uuid4()
    BotUser.all_tenants.filter(channel="max", channel_user_id=person).update(
        ayla_user_id=ayla_user_id
    )
    UserPersonalContext.objects.get_or_create(user_id=ayla_user_id)
    mark_deletion_requested(ayla_user_id, request_id=str(uuid.uuid4()))


def _person_in(state: str, client: Client, tenant, catalog: FakeCatalog) -> str:
    """Человек в состоянии ``state``. У двух из трёх — уже показанное предложение.

    Отозвавший согласие и подавший заявку сначала были обычными людьми:
    собрали план и держат на экране карточку с «Сохранить». Так узлы видят
    самое опасное — нажатие на старую карточку после отзыва.
    """
    person = ACCOUNTS[state]
    shell = _bare_person_shell(tenant, person)
    if state == NEVER_CONSENTED:
        return person
    record_global_consent(shell, source="test")
    shown = _ask(client, card.CB_COMPOSE, as_user=person)
    assert shown.json()["answer"].endswith(card.QUESTION_SAVE)  # предложение на экране
    if state == CONSENT_WITHDRAWN:
        _withdraw(person)
    else:
        _request_deletion(person)
    return person


def _answer(response: Any) -> str:
    assert response.status_code == 200, response.content[:300]
    return str(response.json()["answer"])


# ─── 1–3: без основания план не обрабатывается ───────────────────────────────


@pytest.mark.parametrize("tap", sorted(PROCESSING_TAPS))
@pytest.mark.parametrize("state", sorted(BLOCKED))
def test_g1_without_a_basis_no_plan_entry_reaches_the_catalog(
    state: str, tap: str, client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    person = _person_in(state, client, tenant, catalog)
    catalog.saved_plan = _saved_plan()
    composed, read = len(catalog.composed), catalog.read

    answer = _answer(_ask(client, PROCESSING_TAPS[tap], as_user=person))

    assert answer == f"{BLOCKED[state]} · {card.TEST_MARK}"
    # Каталог о человеке не спрошен вовсе: ни сборки, ни сохранения, ни чтения.
    assert len(catalog.composed) == composed
    assert catalog.read == read
    assert catalog.saved == []  # empty-assert-ok: близнец g4 видит здесь одну команду


@pytest.mark.parametrize("state", sorted(BLOCKED))
def test_g2_the_refusal_leaves_a_next_step(
    state: str, client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    person = _person_in(state, client, tenant, catalog)

    body = _ask(client, card.CB_COMPOSE, as_user=person).json()

    assert body["answer"] == f"{BLOCKED[state]} · {card.TEST_MARK}"
    assert [button["label"] for button in body["buttons"]] == ["Меню"]


@pytest.mark.parametrize("state", sorted(BLOCKED))
def test_g3_the_persons_own_saved_plan_is_still_shown(
    state: str, client: Client, tenant, wire, catalog: FakeCatalog, settings
) -> None:
    """Отзыв закрывает обработку, сохранённый план не уничтожает (владелец 09.10)."""
    settings.PLAN_LITE_ENABLED = False
    person = _person_in(state, client, tenant, catalog)
    catalog.saved_plan = _saved_plan()
    composed = len(catalog.composed)

    answer = _answer(_ask(client, "мой план", as_user=person))

    assert answer.endswith(f"{card.PLAN_CURRENT} · {card.TEST_MARK}")
    assert "Режим сна" in answer
    assert catalog.read == 1
    assert len(catalog.composed) == composed  # показ ничего не собирает


# ─── 4: близнец — с действующим согласием всё работает ───────────────────────


def test_g4_a_consenting_person_composes_and_saves(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    record_global_consent(_bare_person_shell(tenant, CONSENTING), source="test")

    shown = _answer(_ask(client, card.CB_COMPOSE, as_user=CONSENTING))
    saved = _answer(_ask(client, PROCESSING_TAPS["save"], as_user=CONSENTING))

    assert shown.endswith(card.QUESTION_SAVE)
    assert saved == f"{card.PLAN_SAVED} · {card.TEST_MARK}"
    assert len(catalog.composed) == 1
    assert len(catalog.saved) == 1


def test_g4_a_new_grant_after_a_withdrawal_opens_the_plan_again(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    person = _person_in(CONSENT_WITHDRAWN, client, tenant, catalog)
    for shell in _shells(person):
        record_global_consent(shell, source="test")

    shown = _answer(_ask(client, card.CB_COMPOSE, as_user=person))

    assert shown.endswith(card.QUESTION_SAVE)
    assert len(catalog.composed) == 2  # до отзыва и после нового согласия


# ─── сбой чтения основания закрывает ─────────────────────────────────────────


def test_g5_a_failed_consent_read_closes_the_plan(
    client: Client, tenant, wire, catalog: FakeCatalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    from apps.consent import services as consent_services

    record_global_consent(_bare_person_shell(tenant, CONSENTING), source="test")

    def _broken(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("consent registry is not readable")

    monkeypatch.setattr(consent_services, "has_person_consent", _broken)

    answer = _answer(_ask(client, card.CB_COMPOSE, as_user=CONSENTING))

    assert answer == f"{plan_gate.PLAN_BASIS_UNAVAILABLE} · {card.TEST_MARK}"
    assert catalog.composed == []  # empty-assert-ok: близнец g4 видит здесь одну сборку


# ─── заявка на удаление — по человеку, а не по оболочке ───────────────────────


def test_g9_a_deletion_request_on_another_shell_closes_this_one(tenant) -> None:
    """У человека две оболочки (чат глобального бота и Mini App), связка с
    Ayla и заявка — на одной. Вторая, без связки, закрыта тоже; близнец —
    тот же человек до заявки."""
    from apps.tenancy.models import Tenant

    person = "2967106"
    asking = _bare_person_shell(tenant, person)
    linked_id = uuid.uuid4()
    other_tenant = Tenant.objects.create(slug="plan-gate-2967-other", name="Other")
    BotUser.all_tenants.create(
        tenant=other_tenant,
        channel="max",
        channel_user_id=person,
        ayla_user_id=linked_id,
    )
    record_global_consent(asking, source="test")
    assert asking.ayla_user_id is None
    assert plan_gate.plan_processing_refusal(asking) is None  # близнец: до заявки открыто

    UserPersonalContext.objects.get_or_create(user_id=linked_id)
    mark_deletion_requested(linked_id, request_id=str(uuid.uuid4()))

    assert plan_gate.plan_processing_refusal(asking) == plan_gate.PLAN_DELETION_REQUESTED


# ─── замена действующего плана: «Заменить» закрыта, «Оставить» открыта ───────


def _person_with_a_waiting_replacement(
    state: str, client: Client, tenant, catalog: FakeCatalog
) -> str:
    """Человек сохранил план поверх действующего — каталог держит предложение
    и ждёт «Заменить» или «Оставить». Потом основание ушло."""
    person = ACCOUNTS[state]
    record_global_consent(_bare_person_shell(tenant, person), source="test")
    catalog.active_plan_id = ACTIVE_ID
    _ask(client, card.CB_COMPOSE, as_user=person)
    asked = _ask(client, PROCESSING_TAPS["save"], as_user=person).json()
    assert [button["payload"] for button in asked["buttons"]][:2] == [REPLACE, KEEP]
    if state == CONSENT_WITHDRAWN:
        _withdraw(person)
    elif state == DELETION_REQUESTED:
        _request_deletion(person)
    return person


@pytest.mark.parametrize("state", [CONSENT_WITHDRAWN, DELETION_REQUESTED])
def test_g10_replacing_the_plan_is_closed_without_a_basis(
    state: str, client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    person = _person_with_a_waiting_replacement(state, client, tenant, catalog)

    answer = _answer(_ask(client, REPLACE, as_user=person))

    assert answer == f"{BLOCKED[state]} · {card.TEST_MARK}"
    assert catalog.replaced == []  # empty-assert-ok: близнец g10 видит здесь одну замену


def test_g10_a_never_consenting_person_replaces_nothing(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    """Гейт стоит раньше чтения состояния: чужая или старая карточка
    «Заменить» у человека без согласия отвечает отказом, а не «устарело»."""
    person = _person_in(NEVER_CONSENTED, client, tenant, catalog)

    answer = _answer(_ask(client, REPLACE, as_user=person))

    assert answer == f"{plan_gate.PLAN_CONSENT_REQUIRED} · {card.TEST_MARK}"
    assert catalog.replaced == []  # empty-assert-ok: близнец g10 видит здесь одну замену


def test_g10_a_consenting_person_replaces_the_plan(
    client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    person = _person_with_a_waiting_replacement("consenting", client, tenant, catalog)

    answer = _answer(_ask(client, REPLACE, as_user=person))

    assert answer == f"{card.PLAN_REPLACED} · {card.TEST_MARK}"
    (sent,) = catalog.replaced
    assert (sent["plan_id"], sent["replaces_plan_id"]) == (PROPOSAL_ID, ACTIVE_ID)


@pytest.mark.parametrize("state", [CONSENT_WITHDRAWN, DELETION_REQUESTED, "consenting"])
def test_g11_keeping_the_current_plan_stays_open(
    state: str, client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    """«Оставить текущий» — отказ от предложения: данных о человеке после
    него меньше, и убрать висящее предложение ему надо дать и под отзывом."""
    person = _person_with_a_waiting_replacement(state, client, tenant, catalog)

    answer = _answer(_ask(client, KEEP, as_user=person))

    assert answer == f"{card.PLAN_KEPT} · {card.TEST_MARK}"
    assert catalog.archived == [PROPOSAL_ID]
    assert catalog.replaced == []  # empty-assert-ok: строкой выше — архив, не замена


# ─── шаг → услуга → время: все три нажатия закрыты ───────────────────────────

STEP_TAPS = {
    "step": "cb:plan:step:7c1d2e3f:0",
    "offer": "cb:plan:offer:7c1d2e3f:0",
    "slot": "cb:plan:slot:7c1d2e3f:0",
}


@pytest.mark.parametrize("tap", sorted(STEP_TAPS))
@pytest.mark.parametrize("state", sorted(BLOCKED))
def test_g12_no_step_tap_passes_without_a_basis(
    state: str, tap: str, client: Client, tenant, wire, catalog: FakeCatalog
) -> None:
    """Гейт — первая проверка обработчика шага: отказ назван раньше, чем
    читается состояние («устарело») и чем спрошен каталог."""
    from apps.orchestrator import plan_step_card

    person = _person_in(state, client, tenant, catalog)
    composed, read = len(catalog.composed), catalog.read

    answer = _answer(_ask(client, STEP_TAPS[tap], as_user=person))

    assert answer == f"{BLOCKED[state]} · {card.TEST_MARK}"
    assert answer != f"{plan_step_card.PLAN_STEP_EXPIRED} · {card.TEST_MARK}"
    assert (len(catalog.composed), catalog.read) == (composed, read)


# ─── обсуждение, открытое до отзыва ──────────────────────────────────────────


@pytest.mark.parametrize("blocked_by", [None, CONSENT_WITHDRAWN, DELETION_REQUESTED])
def test_g8_an_open_discussion_stops_feeding_the_plan_to_the_model(
    blocked_by: str | None, client: Client, tenant, wire, catalog: FakeCatalog, _concierge
) -> None:
    """Обсуждение открыто, потом основание ушло: следующая реплика идёт
    модели БЕЗ плана и без инструмента «убрать шаг». Близнец (``None``) —
    то же обсуждение без отзыва: план у модели есть."""
    from apps.orchestrator.discovery import DiscoveryReply

    person = "2967105"
    record_global_consent(_bare_person_shell(tenant, person), source="test")
    seen: dict[str, Any] = {}

    def _model(text: str, *, bot_user: Any, conversation: Any, **kw: Any) -> Any:
        seen["block"] = card.render_plan_discussion_block(conversation)
        seen["removable"] = card.discussion_allows_removal(conversation)
        return DiscoveryReply(text="ок")

    _concierge.side_effect = _model
    _ask(client, card.CB_COMPOSE, as_user=person)
    opened = _answer(_ask(client, PROCESSING_TAPS["discuss"], as_user=person))
    assert opened == card.DISCUSS_OPENING
    if blocked_by == CONSENT_WITHDRAWN:
        _withdraw(person)
    elif blocked_by == DELETION_REQUESTED:
        _request_deletion(person)

    _ask(client, "а зачем мне прогулка?", as_user=person)

    assert "block" in seen  # реплика дошла до модели
    if blocked_by is None:
        assert "1. Режим сна" in seen["block"]
        assert seen["removable"] is True
    else:
        assert seen["block"] == ""  # empty-assert-ok: близнец видит здесь шаги плана
        assert seen["removable"] is False


# ─── Mini App: ручка сборки закрыта, ручка чтения открыта ────────────────────

#: Состояние → (HTTP, код). 423 при заявке — как у полки и у каталога.
MINIAPP_REFUSALS = {
    NEVER_CONSENTED: (403, "plan_consent_required"),
    CONSENT_WITHDRAWN: (403, "plan_consent_required"),
    DELETION_REQUESTED: (423, "deletion_requested"),
}


def _proxy_person_in(state: str, person: BotUser) -> None:
    """``proxy_person`` приходит с согласием — привести к состоянию."""
    if state == NEVER_CONSENTED:
        ConsentRecord.all_tenants.filter(bot_user=person).delete()
    elif state == CONSENT_WITHDRAWN:
        _withdraw(person.channel_user_id)
    else:
        _request_deletion(person.channel_user_id)


@pytest.fixture
def _miniapp(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-service-token"  # noqa: S105  # pragma: allowlist secret
    settings.PLAN_ENGINE_ENABLED = True


@pytest.mark.parametrize("state", sorted(BLOCKED))
def test_g6_the_miniapp_compose_endpoint_refuses_before_the_catalog(
    state: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    _proxy_person_in(state, proxy_person)
    status, error = MINIAPP_REFUSALS[state]

    with patch(CLIENT) as mocked:
        response = _post(client)

    assert response.status_code == status, response.content
    assert response.json()["error"] == error
    mocked.assert_not_called()


def test_g6_the_miniapp_compose_endpoint_serves_a_consenting_person(
    client: Client, proxy_person: BotUser, _miniapp
) -> None:
    with patch(CLIENT) as mocked:
        mocked.return_value.compose_decision.return_value = NO_PLAN
        response = _post(client)

    assert response.status_code == 200, response.content
    assert mocked.return_value.compose_decision.call_count == 1


@pytest.mark.parametrize("state", sorted(BLOCKED))
def test_g7_the_miniapp_read_endpoint_stays_open(
    state: str, client: Client, proxy_person: BotUser, _miniapp
) -> None:
    _proxy_person_in(state, proxy_person)

    with patch(CLIENT) as mocked:
        mocked.return_value.get_plan_and_proposal.return_value = (None, None)
        response = client.get(
            reverse("miniapp_api:customer_plan_current"), HTTP_AUTHORIZATION=_auth()
        )

    assert response.status_code == 200, response.content
    assert response.json() == {"plan": None, "proposal": None, "draft": None}
    assert mocked.return_value.get_plan_and_proposal.call_count == 1
