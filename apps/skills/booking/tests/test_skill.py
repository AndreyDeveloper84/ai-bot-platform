"""BookingSkill tests (DRF-839 / Phase 1 / B3).

Mocks the LLM provider + the ``get_yclients_client`` factory so the
skill's two-call tool-use loop runs end-to-end in-process.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.booking.models import BookingRequest
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients import (
    AvailableTime,
    BookingRecord,
    Service,
    Staff,
    YClientsAPIError,
    YClientsUnavailableError,
)
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.router import reset_router_cache
from apps.orchestrator.intent_router import IntentDecision
from apps.skills.base import SkillContext, SkillResult
from apps.skills.booking.skill import BookingSkill
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings: pytest.FixtureRequest):
    settings.BASE_DIR = tmp_path  # type: ignore[attr-defined]
    settings.LLM_PROVIDER = "openai"  # type: ignore[attr-defined]
    settings.SKILL_LLM_PROVIDER = {}  # type: ignore[attr-defined]
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="booking-skill", name="Booking Skill")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="u1",
        chat_id="u1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def context(tenant: Tenant, bot_user: BotUser) -> SkillContext:
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
    return SkillContext(
        conversation=conv,
        bot_user=bot_user,
        message_text="запиши на массаж",
        trace_id="t-booking",
    )


# ---------------------------------------------------------------------------
# Fake YClients client + LLM helpers
# ---------------------------------------------------------------------------


class FakeYClients:
    def __init__(self) -> None:
        self.staff_rows: list[Staff] = []
        self.services_rows: list[Service] = []
        self.dates: list[str] = []
        self.times: list[AvailableTime] = []
        self.create_record_response: BookingRecord | None = None
        self.create_record_exc: Exception | None = None
        self.staff_exc: Exception | None = None
        self.services_exc: Exception | None = None
        self.create_calls: list[dict[str, Any]] = []

    def get_staff(self, *, staff_id: int | None = None) -> list[Staff]:
        if self.staff_exc is not None:
            raise self.staff_exc
        return list(self.staff_rows)

    def get_services(
        self,
        *,
        staff_id: int | None = None,
        category_id: int | None = None,
    ) -> list[Service]:
        if self.services_exc is not None:
            raise self.services_exc
        return list(self.services_rows)

    def get_available_dates(
        self,
        *,
        staff_id: int | None = None,
        service_ids: list[int] | None = None,
    ) -> list[str]:
        return list(self.dates)

    def get_available_times(
        self,
        *,
        staff_id: int,
        date: str,
        service_ids: list[int] | None = None,
    ) -> list[AvailableTime]:
        return list(self.times)

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        if self.create_record_exc is not None:
            raise self.create_record_exc
        if self.create_record_response is None:
            return BookingRecord(record_id=12345, record_hash="h", raw={})
        return self.create_record_response

    def get_user_records(self) -> list[Any]:
        return []


def _staff(id_: int, name: str = "Olga", spec: str = "Массаж") -> Staff:
    return Staff(
        id=id_,
        name=name,
        specialization=spec,
        rating=4.5,
        avatar="",
        position="master",
        raw={},
    )


def _service(id_: int, title: str = "Массаж") -> Service:
    return Service(
        id=id_,
        title=title,
        price_min=1500.0,
        price_max=2500.0,
        duration_s=3600,
        category_id=None,
        raw={},
    )


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="gpt-4o-mini-mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


def _patch_provider_complete(side_effects: list[Any]) -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider

    return patch.object(OpenAIProvider, "complete", side_effect=side_effects)


def _patch_yclients(client: FakeYClients) -> Any:
    return patch("apps.integrations.yclients.get_yclients_client", return_value=client)


# ---------------------------------------------------------------------------
# matches()
# ---------------------------------------------------------------------------


class TestMatches:
    def test_intent_booking_matches(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="x",
            intent=IntentDecision(
                intent="booking", skill="booking", confidence=0.9, risk_level="low"
            ),
        )
        assert BookingSkill().matches(ctx) is True

    def test_intent_other_does_not_match(self, context: SkillContext) -> None:
        ctx = SkillContext(
            conversation=context.conversation,
            bot_user=context.bot_user,
            message_text="x",
            intent=IntentDecision(intent="faq", skill="faq", confidence=0.9, risk_level="low"),
        )
        assert BookingSkill().matches(ctx) is False

    def test_keyword_fallback_when_no_intent(self, context: SkillContext) -> None:
        # context fixture text starts with "запиши" — keyword match.
        assert BookingSkill().matches(context) is True

    def test_keyword_fallback_negative(self, tenant: Tenant, bot_user: BotUser) -> None:
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
        ctx = SkillContext(
            conversation=conv,
            bot_user=bot_user,
            message_text="привет",
        )
        assert BookingSkill().matches(ctx) is False


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


class TestShowMastersFlow:
    def test_lists_masters(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга"), _staff(12, "Иван")]
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "массаж"})
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Вот наши мастера: Ольга, Иван."),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert isinstance(result, SkillResult)
        assert result.should_handoff is False
        assert "Ольга" in result.reply_text or "мастер" in result.reply_text.lower()
        assert result.tool_calls_made == [tc]


class TestShowSlotsFlow:
    def test_lists_slots(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.dates = ["2026-05-20"]
        client.times = [
            AvailableTime(time="14:00", datetime="2026-05-20T14:00:00", seance_length_s=3600)
        ]
        tc = ToolCall(id="c1", name="show_slots", arguments={"master_id": 11})
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Свободно в 14:00."),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert "14:00" in result.reply_text or "Свободно" in result.reply_text


class TestConfirmBookingFlow:
    def test_returns_preview_card_no_record_yet(
        self, context: SkillContext, tenant: Tenant, bot_user: BotUser
    ) -> None:
        """B5: confirm_booking is preview-only.

        The skill returns a 2-button card; no YClients call happens
        until the user taps ✅ (covered by the gate-callback tests).
        """
        from apps.booking.models import PendingBookingAction

        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11, "Ольга")]
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Записываю в 14:00 — подтверждаете?"),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        # No BookingRequest created at preview time.
        assert BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bot_user).count() == 0
        # No YClients create call.
        assert client.create_calls == []
        # Action data carries the keyboard.
        assert result.action_data is not None
        assert "attachments" in result.action_data
        assert "pending_action" in result.action_data
        assert result.action_data["pending_action"]["kind"] == PendingBookingAction.Kind.CONFIRM


class TestShowMyBookingsFlow:
    def test_returns_text(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_my_bookings", arguments={})
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Записей пока нет."),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False


class TestDirectReply:
    def test_no_tool_call_passes_through(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        completions = [_completion(text="Привет! Чем помочь?")]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is False
        assert "Привет" in result.reply_text
        assert result.tool_calls_made == []


# ---------------------------------------------------------------------------
# Handoff paths (the four distinct reasons)
# ---------------------------------------------------------------------------


class TestHandoffPaths:
    def test_no_masters_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = []  # empty staff list
        tc = ToolCall(id="c1", name="show_masters", arguments={"service_name": "x"})
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_no_masters"

    def test_confirm_booking_preview_does_not_call_yclients(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        """B5: confirm_booking is preview-only.

        A YClients failure during create can no longer happen at the
        skill layer — the create moved to ``execute_confirm`` invoked
        by the gate-callback. Skill-layer YClients failure paths still
        cover prefetch + slot-fetch (other tests). This test asserts
        the preview-only contract.
        """
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        # ``create_record_exc`` is irrelevant at the preview stage; we
        # set it to prove it's NOT invoked.
        client.create_record_exc = YClientsAPIError("http_400")
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="Записываю — подтверждаете?"),
        ]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        # Preview success — no handoff, no create.
        assert result.should_handoff is False
        assert client.create_calls == []
        assert result.action_data is not None

    def test_invalid_master_id_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(id="c1", name="show_slots", arguments={"master_id": 9999})
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_invalid_master_id"

    def test_invalid_service_id_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 9999,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_invalid_service_id"

    def test_unknown_tool_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_rows = [_service(22)]
        tc = ToolCall(id="c1", name="search_some_other_tool", arguments={})
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_unknown_tool"

    def test_health_check_required_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        from django.utils import timezone as dj_timezone

        from apps.catalog.models import CatalogService

        # Catalog row flagged as requiring a health check.
        with tenant_scope(tenant):
            CatalogService.objects.create(
                tenant=tenant,
                external_id=22,
                external_updated_at=dj_timezone.now(),
                slug="massage-gated",
                name="Массаж (по показаниям)",
                requires_health_check=True,
            )
        client = FakeYClients()
        client.services_rows = [_service(22)]
        client.staff_rows = [_staff(11)]
        client.create_record_response = BookingRecord(record_id=555, record_hash="", raw={})
        tc = ToolCall(
            id="c1",
            name="confirm_booking",
            arguments={
                "master_id": 11,
                "service_id": 22,
                "slot_datetime": "2026-05-20T14:00:00",
            },
        )
        completions = [_completion(tool_calls=[tc])]
        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_health_check_required"

    def test_yclients_prefetch_failure_handoff(self, context: SkillContext, tenant: Tenant) -> None:
        client = FakeYClients()
        client.services_exc = YClientsUnavailableError("circuit_open")
        with _patch_yclients(client):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)
        assert result.should_handoff is True
        assert result.handoff_reason == "booking_yclients_failure"


# ---------------------------------------------------------------------------
# Tool spec wiring
# ---------------------------------------------------------------------------


class TestToolSpecWiring:
    def test_all_eight_specs_passed_on_first_call(
        self, context: SkillContext, tenant: Tenant
    ) -> None:
        captured_tools: list[Any] = []

        async def fake_complete(
            self_provider: Any,
            messages: list[dict[str, Any]],
            *,
            model: str | None = None,
            temperature: float = 0.0,
            tools: list[dict[str, Any]] | None = None,
            max_tokens: int | None = None,
        ) -> CompletionResult:
            captured_tools.append(tools)
            return _completion(text="ok")

        client = FakeYClients()
        client.services_rows = [_service(22)]
        from apps.llm.providers.openai_provider import OpenAIProvider

        with _patch_yclients(client), patch.object(OpenAIProvider, "complete", fake_complete):
            with tenant_scope(tenant):
                BookingSkill().handle(context)
        assert captured_tools
        first_tools = captured_tools[0]
        assert first_tools is not None
        names = {spec["name"] for spec in first_tools}
        assert names == {
            "cancel_booking",
            "reschedule_booking",
            "show_masters",
            "show_slots",
            "confirm_booking",
            "show_my_bookings",
            "calc_price",
            "buy_certificate",
        }


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_booking_registered(self) -> None:
        from apps.skills.registry import registered

        names = [s.name for s in registered()]
        assert "booking" in names
