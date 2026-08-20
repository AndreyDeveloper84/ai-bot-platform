"""show_masters tool path in the discovery reply (#1020). LLM + marketplace mocked."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from apps.llm.protocol import CompletionResult, ToolCall
from apps.marketplace.dto import MasterCard
from apps.orchestrator import discovery


class _Provider:
    default_completion_model = "m"

    def __init__(self, result: CompletionResult) -> None:
        self._result = result

    async def complete(self, messages, model: str = "", tools=None):  # noqa: ANN001
        # Discovery must offer the show_masters tool and route tenant-less.
        assert any(t["name"] == "show_masters" for t in (tools or []))
        return self._result


class _Router:
    def __init__(self, provider: _Provider) -> None:
        self._provider = provider

    def get_provider(self, tenant=None, *, skill: str = "", op: str = "complete"):  # noqa: ANN001
        assert tenant is None  # tenant-less by design
        return self._provider


def _card(name: str, tid=None, mid=None, sid=None, service_name: str = "") -> MasterCard:
    return MasterCard(
        tenant_id=tid or uuid4(),
        master_id=mid or uuid4(),
        name=name,
        specialization="маникюр",
        rating=Decimal("4.8"),
        photo_url="",
        city="Пенза",
        service_id=sid,
        service_name=service_name,
    )


def test_show_masters_tool_renders_cards_and_handoff_buttons(monkeypatch) -> None:
    tid, mid = uuid4(), uuid4()
    monkeypatch.setattr(discovery, "discover_masters", lambda **kw: [_card("Анна", tid, mid)])
    result = CompletionResult(
        text="", tool_calls=[ToolCall(id="t1", name="show_masters", arguments={"city": "Пенза"})]
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("хочу маникюр в Пензе")
    assert "Анна" in reply.text
    assert reply.action_data is not None
    buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
    assert buttons[0]["callback"] == f"cb:discover:book:{tid}:{mid}"


def test_show_masters_resolved_service_rides_the_callback(monkeypatch) -> None:
    """DRF-962: a card with an unambiguously matched service renders a 3-id
    callback (tenant:master:service) and surfaces the service on the card
    line, so the tap enters booking with the service context."""
    tid, mid, sid = uuid4(), uuid4(), uuid4()
    seen_kwargs: dict = {}

    def fake_discover(**kw):
        seen_kwargs.update(kw)
        return [_card("Анна", tid, mid, sid, service_name="Спортивный массаж")]

    monkeypatch.setattr(discovery, "discover_masters", fake_discover)
    result = CompletionResult(
        text="",
        tool_calls=[
            ToolCall(
                id="t1",
                name="show_masters",
                arguments={"city": "Пенза", "specialization": "спортивный массаж"},
            )
        ],
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("Город Пенза, хочу спортивный")
    assert seen_kwargs.get("resolve_service") is True
    assert "Спортивный массаж" in reply.text
    assert reply.action_data is not None
    buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
    assert buttons[0]["callback"] == f"cb:discover:book:{tid}:{mid}:{sid}"


def test_show_masters_no_results_graceful(monkeypatch) -> None:
    """A real query that matches nothing → honest no-match line.

    DRF-1201 changed the arguments from ``{}`` to a real specialization: an
    empty-argument call no longer reaches ``discover_masters`` at all (see
    ``test_show_masters_without_criteria_asks_instead_of_listing_catalogue``),
    so ``{}`` would no longer exercise what this test is about — the empty
    RESULT of a genuine query.
    """
    monkeypatch.setattr(discovery, "discover_masters", lambda **kw: [])
    result = CompletionResult(
        text="",
        tool_calls=[
            ToolCall(id="t1", name="show_masters", arguments={"specialization": "маникюр"})
        ],
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("маникюр")
    assert "не нашлось" in reply.text
    assert reply.action_data is None


def test_show_masters_without_criteria_asks_instead_of_listing_catalogue(monkeypatch) -> None:
    """DRF-1201 — prohibition #22: no arbitrary catalogue fallback.

    ``show_masters`` declares ``"required": []``, so a criteria-less call is
    legal and used to run ``discover_masters()`` unfiltered — the whole
    cross-tenant catalogue, alphabetically, under «Вот мастера, которые могут
    подойти:». Canon's boundary (BOT-003 §9) is «if additional useful
    information can realistically enable a responsible recommendation,
    continue discovery only as needed under Q3», so the turn must be a
    question. The marketplace read must not happen at all.
    """

    def _must_not_run(**kw):  # noqa: ANN003
        raise AssertionError(f"catalogue read reached with no criteria: {kw}")

    monkeypatch.setattr(discovery, "discover_masters", _must_not_run)
    result = CompletionResult(
        text="", tool_calls=[ToolCall(id="t1", name="show_masters", arguments={})]
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("покажи мастеров")

    assert reply.text == discovery.NO_CRITERIA_QUESTION
    assert "Вот мастера" not in reply.text
    # Not the no-match line either: masters DO exist, we just weren't told
    # what to look for. Saying "не нашлось" here would be false.
    assert "не нашлось" not in reply.text
    assert reply.action_data is None


def test_show_masters_blank_criteria_are_no_criteria(monkeypatch) -> None:
    """Whitespace-only arguments are absent arguments.

    ``_bookable_qs`` deliberately treats a blank ``specialization`` as "no
    filter supplied" — which is exactly the unfiltered read this guard exists
    to keep out of a conversational turn.
    """

    def _must_not_run(**kw):  # noqa: ANN003
        raise AssertionError(f"catalogue read reached with blank criteria: {kw}")

    monkeypatch.setattr(discovery, "discover_masters", _must_not_run)
    result = CompletionResult(
        text="",
        tool_calls=[
            ToolCall(
                id="t1",
                name="show_masters",
                arguments={"city": "   ", "specialization": ""},
            )
        ],
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("покажи мастеров")
    assert reply.text == discovery.NO_CRITERIA_QUESTION


def test_city_alone_is_enough_to_search(monkeypatch) -> None:
    """The guard fires only on NO criteria — one filter still searches."""
    monkeypatch.setattr(discovery, "discover_masters", lambda **kw: [_card("Анна")])
    result = CompletionResult(
        text="", tool_calls=[ToolCall(id="t1", name="show_masters", arguments={"city": "Пенза"})]
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("Пенза")
    assert "Анна" in reply.text


def test_plain_turn_no_tool_call(monkeypatch) -> None:
    result = CompletionResult(text="Привет! Чем помочь?", tool_calls=[])
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("привет")
    assert reply.text == "Привет! Чем помочь?"
    assert reply.action_data is None
