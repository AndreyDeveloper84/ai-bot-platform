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
    monkeypatch.setattr(discovery, "discover_masters", lambda **kw: [])
    result = CompletionResult(
        text="", tool_calls=[ToolCall(id="t1", name="show_masters", arguments={})]
    )
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("маникюр")
    assert "не нашлось" in reply.text
    assert reply.action_data is None


def test_plain_turn_no_tool_call(monkeypatch) -> None:
    result = CompletionResult(text="Привет! Чем помочь?", tool_calls=[])
    monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

    reply = discovery.generate_discovery_reply("привет")
    assert reply.text == "Привет! Чем помочь?"
    assert reply.action_data is None
