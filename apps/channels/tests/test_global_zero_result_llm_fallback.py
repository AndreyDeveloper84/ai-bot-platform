"""Zero deterministic results hand the turn to the model (DRF-1283).

The live pilot turn this is about:

    человек: «покажи массажистов в пензе»
    бот:     «По вашему запросу мастеров пока не нашлось — уточните город
              или услугу.»                          (66ms, no LLM call, no
                                                     AIRequestMetric row)

Four masters in that salon massage. The retrieval bug behind THAT particular
zero is fixed in ``apps.marketplace.discovery`` and pinned there, against a
real Postgres. This module pins the structural half, which outlives the bug:
a deterministic matcher will always have a tail it cannot resolve, and zero
results is that matcher admitting it — not an answer to send.

So the branch stays (a hit is answered without a model — faster, cheaper, and
right when it hits) but it no longer owns the turn unconditionally: ``None``
back from ``generate_direct_show_masters_reply`` falls through to the
concierge, memory blocks and all.

Why this is safe only now: DRF-1102 added the branch precisely BECAUSE the
model could not be trusted with these turns — single-pass, it spent the whole
turn on the ``show_masters`` call with nothing left to say over the result,
and re-asked forever instead. DRF-1266 (multi-pass, live 23.08) removed that
constraint. The fallthrough rides on that, so the two must not drift apart.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from apps.channels.max import handler as max_handler
from apps.orchestrator.memory import short_term

pytestmark = pytest.mark.django_db


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"chat_id": chat_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _no_chat_action(monkeypatch):
    import apps.channels.max.outbound as outbound

    monkeypatch.setattr(outbound, "send_chat_action", lambda **kw: None)


@pytest.fixture(autouse=True)
def _strict(settings):
    # The global path is tenant-less by construction; make that load-bearing so
    # a stray tenant-scoped read in the new fallthrough fails here, not in prod.
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.STRICT_TENANT_REFUSE = True


@pytest.fixture(autouse=True)
def _penza_is_a_place_we_serve():
    """One bookable master in Пенза (DRF-1328).

    ``_BOOKING_TURN`` names a city, and since DRF-1328 the deterministic
    branch claims a turn only when it can account for EVERY word — a city
    counts as accounted for exactly when the marketplace has someone bookable
    there (``apps.marketplace.discovery.strip_known_cities``, live data by
    DRF-1283's design: «recognised» can only ever mean «a place this
    marketplace can serve»).

    In a contour with no masters at all, «пензе» is an unknown word and the
    turn goes to the model — which is the CORRECT outcome there, since an
    empty catalog can answer nothing. But this file is about what happens
    when the branch runs, so it has to be a contour where it can.
    """
    from datetime import datetime, timezone

    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug="salon-penza-1283", name="SPAtrium", city="Пенза")
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Архипкин Денис",
        specialization="массаж",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        external_updated_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def spy_concierge(monkeypatch):
    """The concierge LLM turn, as the handler reaches it (via the turn seam)."""
    from apps.orchestrator.discovery import DiscoveryReply

    spy = MagicMock(
        return_value=DiscoveryReply(
            text="Массаж в Пензе — такого у наших мастеров сейчас нет.", persisted=True
        )
    )
    monkeypatch.setattr("apps.orchestrator.concierge.generate_concierge_reply", spy)
    return spy


def _msg(text: str, *, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": 4242, "name": "Иван"},
            "recipient": {"chat_id": 4242, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 1, "text": text, "attachments": []},
        },
    }


def _run(text: str, *, mid: str = "z-1") -> None:
    max_handler.handle_global_max_event(_msg(text, mid=mid), trace_id=str(uuid.uuid4()))


# A turn the claim parser accepts — it names a service and nothing else
# («пензе» is a city we serve, see `_penza_is_a_place_we_serve`) — so the
# deterministic branch is the one that runs first.
_BOOKING_TURN = "покажи массажистов в пензе"


class TestZeroResultsReachTheModel:
    def test_none_from_the_deterministic_branch_runs_the_concierge(
        self, monkeypatch, mock_send, fake_redis, spy_concierge
    ) -> None:
        direct = MagicMock(return_value=None)  # the search matched nobody
        monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", direct)

        _run(_BOOKING_TURN)

        direct.assert_called_once()
        spy_concierge.assert_called_once()
        # The model's words went out — NOT the deterministic no-match line.
        assert mock_send[-1]["text"] == "Массаж в Пензе — такого у наших мастеров сейчас нет."
        assert "уточните город или услугу" not in mock_send[-1]["text"]

    def test_the_model_sees_the_users_actual_words(
        self, monkeypatch, mock_send, fake_redis, spy_concierge
    ) -> None:
        """The fallthrough must hand over the turn, not a summary of it.

        The concierge fills ``show_masters(city=…, specialization=…)`` itself,
        which is the ONE place in the funnel that parses a city out of free
        text — so it has to receive the sentence that contains one.
        """
        monkeypatch.setattr(
            max_handler, "generate_direct_show_masters_reply", MagicMock(return_value=None)
        )

        _run(_BOOKING_TURN)

        assert spy_concierge.call_args.args[0] == _BOOKING_TURN

    def test_a_hit_still_answers_without_the_model(
        self, monkeypatch, mock_send, fake_redis, spy_concierge
    ) -> None:
        """The owner's constraint: the branch stays.

        A deterministic hit is faster and cheaper than a model turn and it is
        right — the fallthrough is for zero results only, and must not quietly
        become «always ask the model».
        """
        from apps.orchestrator.discovery import DiscoveryReply

        direct = MagicMock(return_value=DiscoveryReply(text="Вот мастера, которые могут подойти:"))
        monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", direct)

        _run(_BOOKING_TURN)

        direct.assert_called_once()
        spy_concierge.assert_not_called()
        assert mock_send[-1]["text"] == "Вот мастера, которые могут подойти:"

    def test_a_non_booking_turn_never_reaches_the_deterministic_branch(
        self, monkeypatch, mock_send, fake_redis, spy_concierge
    ) -> None:
        """Unchanged routing above the branch — the fallthrough is not a rewire."""
        direct = MagicMock(return_value=None)
        monkeypatch.setattr(max_handler, "generate_direct_show_masters_reply", direct)

        _run("расскажи, как вы работаете", mid="z-2")

        direct.assert_not_called()
        spy_concierge.assert_called_once()
