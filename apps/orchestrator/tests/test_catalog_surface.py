"""DRF-1304 — salons & services as concierge tools on the global path.

The concierge could show masters (``show_masters``) but had no tool for the
two questions the live owner asked on 23.08: «какие салоны у нас есть?» and
«что у вас есть по лицу». Covers the tool specs, the dispatcher mapping, the
deterministic executor/renderers (real mirror data or an honest «нет»), and
the concierge wiring end-to-end — one LLM pass, no rephrasing pass, so the
turn's cost does not grow.

Since the owner's 23.08 call (docs/REPLY_CONCIERGE_SURFACE.md — «показывать
кнопками, а не абзацем»), the cards also carry chips, and the chips have their
own contract: a chip may exist ONLY where the tap really executes. That is what
``TestCatalogChips`` pins — the callback shape, the by-id reads behind each tap,
and the two cases that must NOT get a chip (a salon with no services, a service
nobody bookable performs).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, discovery
from apps.orchestrator.concierge import _dispatch_tool, generate_concierge_reply
from apps.orchestrator.discovery import (
    CALLBACK_CATALOG_MASTERS_PREFIX,
    CALLBACK_CATALOG_SALONS,
    CALLBACK_CATALOG_SERVICES_PREFIX,
    CALLBACK_DISCOVER_BOOK_PREFIX,
    CATALOG_STALE_CARD_TEXT,
    CATALOG_TOOL_ACTIONS,
    NO_SERVICE_CRITERIA_QUESTION,
    SHOW_SALONS_TOOL_SPEC,
    SHOW_SERVICES_TOOL_SPEC,
    execute_catalog_callback,
    execute_catalog_tool,
    has_service_criteria,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _ts() -> datetime:
    return datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


def _salon(slug: str, name: str, *, city: str = "", address: str = ""):
    """One salon: tenant + one bookable master (the platform's definition of
    «салон на витрине»). ``address`` rides in the master's mirrored raw —
    exactly where the Ayla specialists feed puts it."""
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug=slug, name=name, city=city)
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=f"Мастер {name}",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        raw={"address": address} if address else {},
    )
    return tenant


def _service(tenant, name: str, *, price: str | None = None, duration: int | None = None):
    from apps.catalog.models import CatalogService

    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        slug=name[:40].lower().replace(" ", "-"),
        name=name,
        is_active=True,
        price_from=Decimal(price) if price is not None else None,
        duration_min=duration,
    )


def _offer(tenant, service):
    """Bind the salon's master to the service — the MasterService edge whose
    EXISTENCE is the statement «this master performs this service» (see the
    model). Without it a service is listed but not bookable, which is a normal
    mirror state and the reason a service chip is conditional."""
    from apps.catalog.models import CatalogMaster, MasterService

    master = CatalogMaster.all_tenants.filter(tenant=tenant).first()
    return MasterService.all_tenants.create(tenant=tenant, master=master, service=service)


def _buttons(reply):
    """The chips of a reply, in the channel-agnostic [{label, callback}] shape
    the MAX handler reads (``_build_attachments``, envelope form)."""
    if reply.action_data is None:
        return []
    return reply.action_data["attachments"][0]["payload"]["buttons"]


def _bot_user_and_conversation(prefix: str):
    from apps.conversations.services import resolve_active_global_conversation
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=f"{prefix}-uid",
        chat_id=f"{prefix}-chat",
    )
    conversation = resolve_active_global_conversation(bot_user)
    return bot_user, conversation


def _router_returning(provider):
    router = Mock()
    router.get_provider.return_value = provider
    return router


class TestToolSpecs:
    def test_catalog_actions_match_specs(self):
        assert CATALOG_TOOL_ACTIONS == {"show_salons", "show_services"}
        assert SHOW_SALONS_TOOL_SPEC["name"] == "show_salons"
        assert SHOW_SERVICES_TOOL_SPEC["name"] == "show_services"

    def test_dispatch_tool_maps_catalog_calls(self):
        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="show_salons", arguments='{"city": "Пенза"}')
        )
        result = _dispatch_tool(tool_call, None)
        assert result.action_type == "show_salons"
        assert result.action_data["arguments"] == {"city": "Пенза"}

        tool_call = SimpleNamespace(
            function=SimpleNamespace(name="show_services", arguments='{"query": "лицо"}')
        )
        result = _dispatch_tool(tool_call, None)
        assert result.action_type == "show_services"
        assert result.action_data["arguments"] == {"query": "лицо"}

    def test_dispatch_tool_unknown_still_degrades(self):
        tool_call = SimpleNamespace(function=SimpleNamespace(name="order_pizza", arguments="{}"))
        result = _dispatch_tool(tool_call, None)
        assert result.action_type == "ask_clarification"
        assert result.action_data["reason"] == "unknown_tool:order_pizza"


class TestExecuteCatalogTool:
    def test_unknown_tool_returns_none(self):
        assert execute_catalog_tool("order_pizza", {}) is None

    def test_services_without_criteria_asks_instead_of_dumping_catalog(self, monkeypatch):
        # BOT-003 §9 / prohibition #22, applied to services: no filter → a
        # clarifying question, and the marketplace read must not happen.
        def _must_not_run(**kwargs):
            raise AssertionError(f"catalog read reached with no criteria: {kwargs}")

        monkeypatch.setattr(discovery, "discover_services", _must_not_run)

        reply = execute_catalog_tool("show_services", {})

        assert reply is not None
        assert reply.text == NO_SERVICE_CRITERIA_QUESTION

    def test_has_service_criteria(self):
        assert not has_service_criteria(None, None, None)
        assert not has_service_criteria("  ", "", None)
        assert has_service_criteria("BodyFormula", None, None)
        assert has_service_criteria(None, "Пенза", None)
        assert has_service_criteria(None, None, "лицо")

    def test_show_salons_renders_real_mirror_rows(self):
        _salon("s1", "BodyFormula", city="Пенза", address="Пенза, ул. Леонова, 15а")
        t2 = _salon("s2", "Безадресный", city="Пенза")  # address empty — pilot shape
        _service(t2, "Массаж спины")

        reply = execute_catalog_tool("show_salons", {"city": "Пенза"})

        assert reply is not None
        assert "BodyFormula" in reply.text
        assert "ул. Леонова, 15а" in reply.text
        assert "Безадресный" in reply.text
        # An empty address must not leak as «None».
        assert "None" not in reply.text
        # Owner's call 23.08: a chip per salon that has something to show.
        # BodyFormula has no services here, so it gets a line and no chip —
        # the tap would open «услуги пока не загружены».
        assert [b["label"] for b in _buttons(reply)] == ["Безадресный"]
        assert _buttons(reply)[0]["callback"] == f"{CALLBACK_CATALOG_SERVICES_PREFIX}{t2.id}"

    def test_show_salons_empty_is_honest(self):
        reply = execute_catalog_tool("show_salons", {"city": "Сочи"})

        assert reply is not None
        assert "Сочи" in reply.text
        assert "нет" in reply.text

    def test_show_services_by_salon_with_price_and_duration(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины", price="1700", duration=45)
        _service(tenant, "Карбокситерапия+пилинг", price="0")  # В-4 — never «бесплатно»
        _service(tenant, "Без цены")

        reply = execute_catalog_tool(
            "show_services", {"salon": "bodyformula"}, said="какие услуги в BodyFormula"
        )

        assert reply is not None
        assert "Массаж спины — от 1700 ₽ · 45 мин" in reply.text
        assert "Карбокситерапия+пилинг" in reply.text
        # Zero / missing price and missing duration are omitted, not invented.
        assert "от 0 ₽" not in reply.text
        assert "None" not in reply.text

    def test_show_services_unknown_salon_named_back(self):
        # DRF-1355 — this is the case the grounding check must NOT swallow.
        # The argument lands on no salon we have, so nothing in the catalog
        # could have suggested it and the person is its only possible source:
        # the call goes through and gets the honest «нет такого салона».
        _salon("s1", "BodyFormula", city="Пенза")

        reply = execute_catalog_tool(
            "show_services",
            {"salon": "Афродита-несуществующая"},
            said="что есть в Афродита-несуществующая",
        )

        assert reply is not None
        assert "Афродита-несуществующая" in reply.text
        assert "пока нет" in reply.text

    def test_show_services_known_salon_without_services(self):
        _salon("s1", "BodyFormula", city="Пенза")

        reply = execute_catalog_tool(
            "show_services", {"salon": "bodyformula"}, said="что делают в BodyFormula"
        )

        assert reply is not None
        assert "не загружены" in reply.text

    def test_show_services_known_salon_query_miss_names_both(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")

        reply = execute_catalog_tool(
            "show_services",
            {"salon": "bodyformula", "query": "такойуслугинет"},
            said="есть ли такойуслугинет в BodyFormula",
        )

        assert reply is not None
        # The salon IS here — «услуги не загружены» would be a lie about a
        # loaded catalog; the miss is about the query.
        assert "не загружены" not in reply.text
        assert "такойуслугинет" in reply.text

    def test_show_services_query_no_match_is_honest(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")

        reply = execute_catalog_tool("show_services", {"query": "такойуслугинет"})

        assert reply is not None
        assert "такойуслугинет" in reply.text
        assert "нет" in reply.text


class TestConciergeCatalogTurn:
    def test_catalog_tool_specs_reach_the_model(self, monkeypatch):
        captured: dict = {}

        async def _complete(messages, model: str = "", tools=None):
            captured["tools"] = tools
            return CompletionResult(text="ok")

        provider = AsyncMock()
        provider.complete.side_effect = _complete
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation("drf1304-tools")

        generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        tool_names = {t["name"] for t in captured["tools"]}
        assert {"show_salons", "show_services"} <= tool_names

    def test_show_salons_tool_call_returns_mirror_data(self, monkeypatch):
        _salon("s1", "BodyFormula", city="Пенза", address="Пенза, ул. Леонова, 15а")
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[ToolCall(id="t1", name="show_salons", arguments={"city": "Пенза"})],
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation("drf1304-salons")

        reply = generate_concierge_reply(
            "какие салоны у нас есть?", bot_user=bot_user, conversation=conversation
        )

        assert reply.persisted is True
        assert "BodyFormula" in reply.text
        assert "ул. Леонова" in reply.text
        # One LLM pass: the deterministic render must not have re-entered the
        # model to rephrase the catalog (turn cost does not grow).
        assert provider.complete.await_count == 1

    def test_show_services_tool_call_returns_prices(self, monkeypatch):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж лица", price="1500", duration=30)
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[
                ToolCall(id="t1", name="show_services", arguments={"salon": "BodyFormula"})
            ],
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation("drf1304-services")

        reply = generate_concierge_reply(
            "что есть в BodyFormula?", bot_user=bot_user, conversation=conversation
        )

        assert reply.persisted is True
        assert "Массаж лица — от 1500 ₽ · 30 мин" in reply.text
        assert provider.complete.await_count == 1

    def test_show_services_without_criteria_asks(self, monkeypatch):
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[ToolCall(id="t1", name="show_services", arguments={})],
        )
        monkeypatch.setattr(concierge, "get_router", lambda: _router_returning(provider))
        bot_user, conversation = _bot_user_and_conversation("drf1304-nocrit")

        reply = generate_concierge_reply(
            "какие у вас услуги?", bot_user=bot_user, conversation=conversation
        )

        assert reply.persisted is True
        assert reply.text == NO_SERVICE_CRITERIA_QUESTION

    def test_system_prompt_carries_catalog_tools(self):
        prompt = concierge.build_concierge_system_prompt()
        assert "show_salons" in prompt
        assert "show_services" in prompt
        # The pre-DRF-1304 «этих данных пока нет» boundary contradicted the
        # tools — the boundary that survives is «never invent».
        assert "этих данных пока нет" not in prompt
        assert "не выдумывай" in prompt


# --------------------------------------------------------------------------- #
# Acceptance: chips (owner's call 23.08) — every chip must really execute      #
# --------------------------------------------------------------------------- #


class TestCatalogChips:
    """The rule these tests exist for: «чип обязан вести к тому, что
    действительно исполнится. Кнопка, ведущая в „я вас не понял", хуже
    отсутствия кнопки — человек уже потратил на неё доверие»."""

    def test_salon_chip_carries_the_tenant_id_not_the_name(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")

        reply = execute_catalog_tool("show_salons", {"city": "Пенза"})

        assert _buttons(reply) == [
            {
                "label": "BodyFormula",
                "callback": f"{CALLBACK_CATALOG_SERVICES_PREFIX}{tenant.id}",
            }
        ]

    def test_salon_tap_opens_exactly_that_salon(self):
        # Names that contain one another are why the callback carries an id: a
        # name substring would answer this tap with both salons' services.
        formula = _salon("s1", "Формула", city="Пенза")
        formula_tela = _salon("s2", "Формула тела", city="Пенза")
        _service(formula, "Стрижка")
        _service(formula_tela, "Массаж спины")

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{formula.id}")

        assert reply is not None
        assert "Стрижка" in reply.text
        assert "Массаж спины" not in reply.text

    def test_service_chip_only_where_someone_can_perform_it(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        bookable = _service(tenant, "Массаж спины", price="1700", duration=45)
        _service(tenant, "Никем не оказывается")
        _offer(tenant, bookable)

        reply = execute_catalog_tool(
            "show_services", {"salon": "bodyformula"}, said="что есть в BodyFormula"
        )

        # Both services are REAL and both are shown — only the chip differs.
        assert "Массаж спины" in reply.text
        assert "Никем не оказывается" in reply.text
        assert _buttons(reply) == [
            {
                "label": "Массаж спины",
                "callback": f"{CALLBACK_CATALOG_MASTERS_PREFIX}{bookable.id}",
            }
        ]

    def test_service_tap_shows_masters_ready_to_book_that_service(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        service = _service(tenant, "Массаж спины", price="1700", duration=45)
        _offer(tenant, service)

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{service.id}")

        assert reply is not None
        assert "Мастер BodyFormula" in reply.text
        # The booking button carries the service the user TAPPED (DRF-962), so
        # the next step is not the stale-context dead end.
        callback = _buttons(reply)[0]["callback"]
        assert callback.startswith(CALLBACK_DISCOVER_BOOK_PREFIX)
        assert callback.endswith(f":{service.id}")

    def test_whole_chain_salon_to_service_to_master_is_tappable(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        service = _service(tenant, "Массаж спины")
        _offer(tenant, service)

        salons = execute_catalog_tool("show_salons", {})
        services = execute_catalog_callback(_buttons(salons)[0]["callback"])
        masters = execute_catalog_callback(_buttons(services)[0]["callback"])

        assert "Массаж спины" in services.text
        assert "Мастер BodyFormula" in masters.text
        assert _buttons(masters)[0]["callback"].startswith(CALLBACK_DISCOVER_BOOK_PREFIX)

    def test_vanished_salon_answers_instead_of_falling_through(self):
        import uuid

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{uuid.uuid4()}")

        assert reply is not None
        assert reply.text == CATALOG_STALE_CARD_TEXT

    def test_malformed_ref_answers_instead_of_raising(self):
        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_SERVICES_PREFIX}не-uuid")

        assert reply is not None
        assert reply.text == CATALOG_STALE_CARD_TEXT

    def test_service_whose_masters_left_is_honest(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        service = _service(tenant, "Массаж спины")
        # Chip rendered while the edge existed; the edge is gone by the tap.

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_MASTERS_PREFIX}{service.id}")

        assert reply is not None
        assert "не к кому" in reply.text
        # DRF-1492 — the refusal is still honest AND no longer a dead end: it
        # used to end at «спросите, что ещё есть в этом салоне», which named a
        # salon this branch can no longer identify and left the person typing.
        assert [b["callback"] for b in _buttons(reply)] == [CALLBACK_CATALOG_SALONS]

    def test_salon_with_an_empty_catalog_says_so_on_tap(self):
        tenant = _salon("s1", "BodyFormula", city="Пенза")

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{tenant.id}")

        assert reply is not None
        assert "не загружены" in reply.text

    def test_foreign_callback_is_left_to_the_ladder(self):
        # Not a catalog callback — the caller must keep matching its own
        # branches rather than receive an answer meant for someone else.
        assert execute_catalog_callback("cb:visit:card:abc") is None
        assert execute_catalog_callback("какие салоны у вас есть") is None

    def test_city_is_not_printed_twice(self):
        # Live pilot 23.08: every mirrored address already opens with the city
        # («Пенза, ул. Карпинского, 33А»), so gluing city + address printed
        # «SPAtrium — Пенза, Пенза, ул. Карпинского, 33А».
        tenant = _salon("s1", "SPAtrium", city="Пенза", address="Пенза, ул. Карпинского, 33А")
        _service(tenant, "Массаж спины")

        reply = execute_catalog_tool("show_salons", {"city": "Пенза"})

        assert "• SPAtrium — Пенза, ул. Карпинского, 33А" in reply.text
        assert "Пенза, Пенза" not in reply.text

    def test_address_from_another_city_still_shows_both(self):
        # Mirror drift: then the city and the address really are two facts.
        tenant = _salon("s1", "Выездной", city="Пенза", address="Москва, Тверская, 1")
        _service(tenant, "Массаж спины")

        reply = execute_catalog_tool("show_salons", {})

        assert "• Выездной — Пенза, Москва, Тверская, 1" in reply.text

    def test_five_real_salons_render_whole(self):
        # Real Penza rows (docs/catalog/MARKET_PENZA.md) are long: at the
        # model's 600-char prose budget this list came out cut mid-word, with
        # chips for salons the text no longer named.
        rows = [
            ("Центр коррекции фигуры «Afrodita»", "Пенза, ул. Московская, 74, БЦ «Московский»"),
            ("Медиклиник", "Пенза, ул. Суворова, 122а"),
            ("BodyFormula", "Пенза, ул. Леонова, 15а"),
            ("Студия красоты «Багира»", "Пенза, ул. Кирова, 63"),
            ("Лаборатория красоты", "Пенза, пр-т Строителей, 41"),
        ]
        for i, (name, address) in enumerate(rows):
            tenant = _salon(f"s{i}", name, city="Пенза", address=address)
            _service(tenant, "Лазерная эпиляция подмышечных впадин")

        reply = execute_catalog_tool("show_salons", {"city": "Пенза"})

        for name, address in rows:
            assert name in reply.text
            assert address in reply.text
        assert "Нажмите на салон" in reply.text  # the tail survived too
        # Set, not list: salon order is the DB collation's business, and it
        # differs between SQLite and Postgres for mixed Latin/Cyrillic names.
        assert {b["label"] for b in _buttons(reply)} == {name for name, _ in rows}

    def test_no_chips_means_no_empty_keyboard(self):
        # An inline_keyboard attachment with an empty button list renders as a
        # broken message, not as a message without buttons.
        #
        # The positive half first (DRF-1411): the same call with a chippable
        # salon DOES draw a keyboard, so an empty one below means «no chips»
        # and not «this renderer stopped drawing anything».
        chippable = _salon("s0", "BodyFormula", city="Пенза")
        _service(chippable, "Массаж спины")
        assert _buttons(execute_catalog_tool("show_salons", {"city": "Пенза"}))

        # A salon whose mirror carries no active service: its line renders,
        # its chip does not — the tap would open an empty list.
        _salon("s1", "Пустой", city="Саранск")
        reply = execute_catalog_tool("show_salons", {"city": "Саранск"})

        assert "Пустой" in reply.text
        assert reply.action_data is None

    def test_empty_salon_list_without_a_city_offers_no_loop(self):
        # DRF-1492's other half. «В городе X салонов нет» gets a «Показать
        # салоны» chip — it drops the filter and answers «а где вы есть».
        # The city-LESS refusal must NOT: its tap would redraw this very
        # sentence, and a button that loops is worse than a full stop.
        with_city = execute_catalog_tool("show_salons", {"city": "Сочи"})
        assert [b["callback"] for b in _buttons(with_city)] == [CALLBACK_CATALOG_SALONS]

        without_city = execute_catalog_tool("show_salons", {})
        assert "Подключённых салонов пока нет" in without_city.text
        assert without_city.action_data is None

    def test_show_salons_chip_answers_with_the_salon_list(self):
        # The chip DRF-1492 added — the entry point of the catalog chain as a
        # button. Refless: nothing to go stale, and the same renderer the
        # model-called tool uses.
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")

        reply = execute_catalog_callback(CALLBACK_CATALOG_SALONS)

        assert reply is not None
        assert "BodyFormula" in reply.text
        # And the tap chain continues: the salon chip it renders opens that
        # salon's services, exactly as the typed question does.
        services = execute_catalog_callback(_buttons(reply)[0]["callback"])
        assert services is not None
        assert "Массаж спины" in services.text

    def test_stale_card_line_carries_the_move_it_names(self):
        # DRF-1492 — the line used to read «Спросите "какие салоны у вас
        # есть", и я покажу заново»: the bot knew the move, named the move,
        # and handed over the typing.
        import uuid as _uuid

        reply = execute_catalog_callback(f"{CALLBACK_CATALOG_SERVICES_PREFIX}{_uuid.uuid4()}")

        assert reply is not None
        assert reply.text == CATALOG_STALE_CARD_TEXT
        assert [b["callback"] for b in _buttons(reply)] == [CALLBACK_CATALOG_SALONS]
