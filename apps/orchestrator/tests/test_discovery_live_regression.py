"""Live-scenario regression for the Controlled-Pilot discovery blocker (DRF-945).

Reproduces the conversation that failed in production, as high up the pipeline
as is deterministic:

    user:      «ищу массаж, что можешь предложить»
    assistant: уточняет тип массажа и город
    user:      «Город Пенза, хочу спортивный»
    bot:       «По вашему запросу мастеров пока не нашлось — уточните город или услугу.»

Only the LLM is faked — it is asked to emit the ``show_masters`` tool call the
real model emitted, with the same arguments. Everything below that is real:
tool dispatch, the cross-tenant marketplace carve-out, the ORM join through
``MasterService``, and the card renderer that produced the fallback string.
That keeps the test honest about the part that broke (retrieval) without
pinning brittle LLM prose.

The salon fixture is synthetic — no hardcoded formula-tela, no real master.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from django.conf import settings

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import discovery
from apps.tenancy.models import Tenant

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(
        "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
        reason="Cyrillic ILIKE folding requires Postgres; the fallback assertion "
        "would pass vacuously on SQLite.",
    ),
]

# The exact string the user saw. If retrieval regresses, this comes back.
_ZERO_RESULT_FALLBACK = "По вашему запросу мастеров пока не нашлось"

_HISTORY = [
    {"role": "user", "content": "ищу массаж, что можешь предложить"},
    {
        "role": "assistant",
        "content": "Подскажите, какой массаж вас интересует и в каком вы городе?",
    },
]


class _Provider:
    default_completion_model = "m"

    def __init__(self, result: CompletionResult) -> None:
        self._result = result

    async def complete(self, messages, model: str = "", tools=None):  # noqa: ANN001
        assert any(t["name"] == "show_masters" for t in (tools or []))
        return self._result


class _Router:
    def __init__(self, provider: _Provider) -> None:
        self._provider = provider

    def get_provider(self, tenant=None, *, skill: str = "", op: str = "complete"):  # noqa: ANN001
        assert tenant is None  # discovery is tenant-less by design
        return self._provider


def _ts() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def penza_massage_salon() -> CatalogMaster:
    """A synthetic Penza salon offering sports + classic massage.

    ``specialization`` is left EMPTY on purpose — that is the production shape
    (nothing populates it), and it is precisely why the old implementation
    returned zero.
    """
    tenant = Tenant.objects.create(slug="salon-penza-live", name="Salon Penza", city="Пенза")
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Массажист Пилот",
        specialization="",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=_ts(),
    )
    for name, slug in (("Спортивный массаж", "sport-massage"), ("Классический массаж", "classic")):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=slug,
            name=name,
            is_active=True,
            # The Ayla canonical id: the production shape (services are
            # Ayla-synced) and the deliverability key the DRF-962 service
            # resolution requires before stamping a service on the card.
            ayla_service_id=uuid4(),
            external_updated_at=_ts(),
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
    return master


def _run_turn(monkeypatch, *, tool_args: dict) -> discovery.DiscoveryReply:
    result = CompletionResult(
        text="",
        tool_calls=[ToolCall(id="t1", name="show_masters", arguments=tool_args)],
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))
    return discovery.generate_discovery_reply(
        "Город Пенза, хочу спортивный",
        history=_HISTORY,
    )


@pytest.mark.parametrize(
    "specialization",
    [
        "спортивный массаж",  # model resolves the full service name
        "спортивный",  # model forwards the user's adjective verbatim
    ],
    ids=["full-service-name", "adjective-only"],
)
def test_live_turn_returns_a_master_not_the_fallback(
    settings, monkeypatch, penza_massage_salon: CatalogMaster, specialization: str
) -> None:
    """The regression itself: this turn must not produce the zero-result line.

    Parametrized over both argument shapes the tool call can plausibly carry,
    since the model's normalization of «хочу спортивный» is not contractual.
    Flag ON = the pilot path; it is also the deliverability precondition for
    the service to be stamped on the card (DRF-962).
    """
    settings.BOOKING_VIA_AYLA_REST = True
    reply = _run_turn(
        monkeypatch,
        tool_args={"city": "Пенза", "specialization": specialization},
    )

    assert _ZERO_RESULT_FALLBACK not in reply.text
    # Assert the WHOLE rendered line, not just that the name appears somewhere.
    # A substring check happily passes on «• Массажист Пилот —  · Пенза», the
    # dangling-dash rendering an empty specialization used to produce — and an
    # empty specialization is the common case on this very path. The resolved
    # service rides the line since DRF-962: the button carries it into
    # booking, so the user must see what they are tapping into.
    assert "• Массажист Пилот · Спортивный массаж · Пенза" in reply.text
    assert " —  " not in reply.text
    assert reply.action_data is not None
    buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
    # DRF-962: the callback must carry tenant + master + the resolved service —
    # a two-id payload dead-ends on the booking skill's stale-context guard.
    callback = buttons[0]["callback"]
    assert callback.startswith("cb:discover:book:")
    assert len(callback.removeprefix("cb:discover:book:").split(":")) == 3


def test_master_is_reachable_without_any_specialization_text(
    monkeypatch, penza_massage_salon: CatalogMaster
) -> None:
    """Guards the actual root cause, not just the symptom.

    If someone re-introduces a dependency on ``CatalogMaster.specialization``,
    this fails — the fixture's master has none, and only the service relation
    can satisfy the query.
    """
    assert penza_massage_salon.specialization == ""

    reply = _run_turn(
        monkeypatch,
        tool_args={"city": "Пенза", "specialization": "спортивный массаж"},
    )

    assert "Массажист Пилот" in reply.text


def test_genuinely_absent_service_still_falls_back(
    monkeypatch, penza_massage_salon: CatalogMaster
) -> None:
    """A real zero must still refuse — the fix must not answer everything.

    DRF-1283 changed WHAT the refusal says, not whether it fires. The old line
    asked the client to «уточните город или услугу» after they had named both,
    which reads as «я вас не понял» — a worse failure than the missing result,
    because it denies understanding a sentence we understood. The refusal now
    names back what was searched for and asks only for what was not given.
    """
    reply = _run_turn(
        monkeypatch,
        tool_args={"city": "Пенза", "specialization": "наращивание ресниц"},
    )

    assert reply.action_data is None
    # Refuses…
    assert "Массажист Пилот" not in reply.text
    # …while showing the request was understood, both halves of it.
    assert "наращивание ресниц" in reply.text
    assert "Пенза" in reply.text
    # …and without asking for what the client already said.
    assert _ZERO_RESULT_FALLBACK not in reply.text
    assert "уточните город или услугу" not in reply.text


def test_wrong_city_still_falls_back(monkeypatch, penza_massage_salon: CatalogMaster) -> None:
    reply = _run_turn(
        monkeypatch,
        tool_args={"city": "Москва", "specialization": "спортивный массаж"},
    )

    assert "Массажист Пилот" not in reply.text
    assert "Москва" in reply.text
    assert _ZERO_RESULT_FALLBACK not in reply.text


def test_no_criteria_at_all_still_gets_the_generic_line(
    monkeypatch, penza_massage_salon: CatalogMaster
) -> None:
    """The old wording survives for the one case it is right for.

    With neither half named there is nothing to acknowledge, so asking for the
    city AND the service is the honest question rather than a deaf one.
    """
    from apps.orchestrator.discovery import render_no_match

    assert _ZERO_RESULT_FALLBACK in render_no_match().text
