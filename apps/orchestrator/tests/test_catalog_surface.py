"""DRF-1304 — salons & services as concierge tools on the global path.

The concierge could show masters (``show_masters``) but had no tool for the
two questions the live owner asked on 23.08: «какие салоны у нас есть?» and
«что у вас есть по лицу». Covers the tool specs, the dispatcher mapping, the
deterministic executor/renderers (real mirror data or an honest «нет»), and
the concierge wiring end-to-end — one LLM pass, no rephrasing pass, so the
turn's cost does not grow.
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
    CATALOG_TOOL_ACTIONS,
    NO_SERVICE_CRITERIA_QUESTION,
    SHOW_SALONS_TOOL_SPEC,
    SHOW_SERVICES_TOOL_SPEC,
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
        # No keyboards on this path (DRF-1220) — tools, not buttons.
        assert reply.action_data is None

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

        reply = execute_catalog_tool("show_services", {"salon": "bodyformula"})

        assert reply is not None
        assert "Массаж спины — от 1700 ₽ · 45 мин" in reply.text
        assert "Карбокситерапия+пилинг" in reply.text
        # Zero / missing price and missing duration are omitted, not invented.
        assert "от 0 ₽" not in reply.text
        assert "None" not in reply.text

    def test_show_services_unknown_salon_named_back(self):
        _salon("s1", "BodyFormula", city="Пенза")

        reply = execute_catalog_tool("show_services", {"salon": "Афродита-несуществующая"})

        assert reply is not None
        assert "Афродита-несуществующая" in reply.text
        assert "пока нет" in reply.text

    def test_show_services_known_salon_without_services(self):
        _salon("s1", "BodyFormula", city="Пенза")

        reply = execute_catalog_tool("show_services", {"salon": "bodyformula"})

        assert reply is not None
        assert "не загружены" in reply.text

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
