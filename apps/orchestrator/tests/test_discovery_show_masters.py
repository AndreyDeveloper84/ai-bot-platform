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
    """DRF-962: a card with an unambiguously matched service renders a callback
    carrying tenant:master:service and surfaces the service on the card line,
    so the tap enters booking with the service context.

    DRF-1324 appended a FOURTH segment — the request that surfaced these
    masters, so the ask-the-service menu behind an unresolved tap can be
    narrowed by it. The three ids are still asserted positionally and exactly;
    the ref is asserted by decoding it back rather than by its spelling, since
    its encoding is not the contract — surviving the round trip is.
    """
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
    ids, _, ref = buttons[0]["callback"].rpartition(":")
    assert ids == f"cb:discover:book:{tid}:{mid}:{sid}"
    assert discovery.decode_query_ref(ref) == ["спорти", "массаж"]


def test_show_masters_no_results_graceful(monkeypatch) -> None:
    """A real query that matches nothing → honest no-match line.

    «Honest» is the DRF-1283 bar: the line must show the request was
    understood, not ask for what the client already said.

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
    # DRF-1283 — the refusal names back what was searched for instead of
    # asking the client to «уточнить услугу» they had just named.
    assert "маникюр" in reply.text
    assert "уточните город или услугу" not in reply.text
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


class TestEmptyRatingIsNotShown:
    """DRF-1224 — «★ 0.00» must never reach a user.

    Observed on the pilot: «массаж в Пензе» answered with four cards, every
    one of them «★ 0.00». The rating domain is 1..5 (``booking_rating_in_
    range_or_null``, ``apps/eventbus/consumers/reviews.py`` rejects anything
    outside it), so a stored ``0.00`` cannot mean «rated zero» — it can only
    mean «nothing behind it». Reviews are blocked upstream on the pilot, so
    that is EVERY card, and the ``is not None`` guard let all of them
    through.

    Same class as the em-dash bug documented in ``_render_master_cards``:
    a guard written for the value that never arrives, while the value that
    does arrive walks straight past it.
    """

    def _line(self, **overrides) -> str:
        card = MasterCard(
            tenant_id=uuid4(),
            master_id=uuid4(),
            name="Архипкин Денис",
            specialization=overrides.pop("specialization", ""),
            rating=overrides.pop("rating", Decimal("0.00")),
            photo_url="",
            city=overrides.pop("city", "Пенза"),
            service_id=overrides.pop("service_id", None),
            service_name=overrides.pop("service_name", ""),
        )
        assert not overrides, overrides
        return discovery._render_master_cards([card]).text.splitlines()[1]

    def test_zero_rating_renders_no_star(self) -> None:
        assert "★" not in self._line(rating=Decimal("0.00"))

    def test_none_rating_still_renders_no_star(self) -> None:
        assert "★" not in self._line(rating=None)

    def test_real_rating_still_shown(self) -> None:
        assert "★ 4.80" in self._line(rating=Decimal("4.80"))

    def test_zero_rating_leaves_no_dangling_separator(self) -> None:
        """The em-dash lesson: an omitted part must not leave its glue.

        Every optional part carries its own « · » / « — » PREFIX, so an
        empty one should vanish whole. Pin it — this is exactly the shape
        that regressed before.
        """
        line = self._line(rating=Decimal("0.00"), specialization="", service_id=None, city="")
        assert line == "• Архипкин Денис"

    def test_no_double_separator_in_any_combination(self) -> None:
        """All 16 on/off combinations of spec × service × rating × city."""
        sid = uuid4()
        for spec in ("", "Массажист"):
            for service in ((None, ""), (sid, "Спортивный массаж")):
                for rating in (None, Decimal("0.00"), Decimal("4.80")):
                    for city in ("", "Пенза"):
                        line = self._line(
                            specialization=spec,
                            service_id=service[0],
                            service_name=service[1],
                            rating=rating,
                            city=city,
                        )
                        assert "  " not in line, line
                        assert " ·  " not in line, line
                        assert " —  " not in line, line
                        assert not line.endswith(("·", "—", " ")), line

    def test_named_service_missing_does_not_leave_bare_separator(self) -> None:
        """``service_name`` is normalised to ``""`` next to a real
        ``service_id`` (apps/marketplace/discovery.py:240 — ``service_name
        or ""``), so the id-only guard can render a bare « · ». Not observed
        on the pilot; same bug shape, one character away."""
        line = self._line(service_id=uuid4(), service_name="", city="Пенза")
        assert " ·  " not in line
        assert line == "• Архипкин Денис · Пенза"

    def test_pilot_reproduction_all_four_cards(self, monkeypatch) -> None:
        """End-to-end through the tool path, exactly as the pilot answered."""
        cards = [
            MasterCard(
                tenant_id=uuid4(),
                master_id=uuid4(),
                name=name,
                specialization="",
                rating=Decimal("0.00"),
                photo_url="",
                city="Пенза",
            )
            for name in ("Архипкин Денис", "Сазонова Инна", "Тихонова Ольга")
        ]
        monkeypatch.setattr(discovery, "discover_masters", lambda **kw: cards)
        result = CompletionResult(
            text="",
            tool_calls=[
                ToolCall(
                    id="t1",
                    name="show_masters",
                    arguments={"city": "Пенза", "specialization": "массаж"},
                )
            ],
        )
        monkeypatch.setattr(discovery, "get_router", lambda: _Router(_Provider(result)))

        reply = discovery.generate_discovery_reply("массаж в Пензе")
        assert "★" not in reply.text
        assert "0.00" not in reply.text
        assert "Архипкин Денис" in reply.text
