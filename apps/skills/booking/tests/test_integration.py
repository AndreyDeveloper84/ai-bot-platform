"""End-to-end booking flow test (DRF-839 / Phase 1 / B3 + B5).

Drives the whole flow with B5's 2-button confirm gate:

    "запиши на массаж к Ольге"  →  show_slots
                                →  confirm_booking (preview only)
                                →  cb:book:confirm:<token> tap
                                →  BookingRequest row created

with mocked LLM provider + mocked YClients client.
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
from apps.integrations.yclients import AvailableTime, BookingRecord, Service, Staff
from apps.llm.protocol import CompletionResult, ToolCall, UnknownTenantError
from apps.llm.router import reset_router_cache
from apps.skills.base import SkillContext
from apps.skills.booking.skill import BookingSkill
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


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


class FakeYClients:
    def __init__(self) -> None:
        self.staff_rows: list[Staff] = []
        self.services_rows: list[Service] = []
        self.dates: list[str] = []
        self.times: list[AvailableTime] = []
        self.create_record_response: BookingRecord | None = None
        self.create_calls: list[dict[str, Any]] = []

    def get_staff(self, *, staff_id: int | None = None) -> list[Staff]:
        return list(self.staff_rows)

    def get_services(
        self,
        *,
        staff_id: int | None = None,
        category_id: int | None = None,
    ) -> list[Service]:
        return list(self.services_rows)

    def get_available_dates(self, **_: Any) -> list[str]:
        return list(self.dates)

    def get_available_times(self, **_: Any) -> list[AvailableTime]:
        return list(self.times)

    def create_record(self, **kwargs: Any) -> BookingRecord:
        self.create_calls.append(kwargs)
        return self.create_record_response or BookingRecord(record_id=42, record_hash="h", raw={})

    def get_user_records(self) -> list[Any]:
        return []


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


class TestEndToEnd:
    def test_user_books_olga_for_massage(self, db) -> None:
        tenant = Tenant.objects.create(slug="e2e", name="E2E")
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="e2e-1",
            chat_id="e2e-1",
            phone="79991234567",
            client_name="Anna",
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
        context = SkillContext(
            conversation=conv,
            bot_user=bu,
            message_text="запиши на массаж к Ольге на 14:00",
            trace_id="t-e2e",
        )

        # YClients fixture: 1 service, 1 master "Ольга", 1 slot.
        client = FakeYClients()
        client.services_rows = [
            Service(
                id=22,
                title="Массаж спины",
                price_min=1500.0,
                price_max=2500.0,
                duration_s=3600,
                category_id=None,
                raw={},
            )
        ]
        client.staff_rows = [
            Staff(
                id=11,
                name="Ольга",
                specialization="Массаж",
                rating=4.7,
                avatar="",
                position="master",
                raw={},
            )
        ]
        client.dates = ["2026-05-20"]
        client.times = [
            AvailableTime(
                time="14:00",
                datetime="2026-05-20T14:00:00",
                seance_length_s=3600,
            )
        ]
        client.create_record_response = BookingRecord(record_id=777, record_hash="h", raw={})

        # LLM jumps straight to confirm_booking (the user already named
        # both master + slot in the message). Two LLM calls: first picks
        # the tool, second renders the receipt.
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
            _completion(text="Готово, Анна! Записала к Ольге на 20.05 в 14:00."),
        ]

        from apps.llm.providers.openai_provider import OpenAIProvider

        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(OpenAIProvider, "complete", side_effect=completions),
        ):
            with tenant_scope(tenant):
                preview = BookingSkill().handle(context)

        # Step 1: preview-only — no record yet.
        assert preview.should_handoff is False
        assert preview.tool_calls_made == [tc]
        assert BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bu).count() == 0
        assert client.create_calls == []
        # Action data carries the inline keyboard with the gate token.
        assert preview.action_data is not None
        token = preview.action_data["pending_action"]["token"]

        # Step 2: simulate the ✅ tap on the inline keyboard.
        from apps.bookings.callbacks import BookingGateCallbackSkill

        tap_context = SkillContext(
            conversation=conv,
            bot_user=bu,
            message_text=f"cb:book:confirm:{token}",
            trace_id="t-e2e-tap",
        )
        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
        ):
            with tenant_scope(tenant):
                tap_result = BookingGateCallbackSkill().handle(tap_context)

        # Step 3: confirm the actual booking + record created.
        assert tap_result.should_handoff is False
        rows = BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bu)
        assert rows.count() == 1
        row = rows.first()
        assert row is not None
        assert "yclients_record_id=777" in row.comment
        assert row.source == "bot"
        # And the YClients HTTP create was called once.
        assert len(client.create_calls) == 1
        assert client.create_calls[0]["staff_id"] == 11
        assert client.create_calls[0]["services"] == [22]


class TestLLMY3StaleUUIDEndToEnd:
    """Issue #473 acceptance: stale UUID through the booking skill MUST
    produce a friendly handoff, NOT a 500.

    Pre-#473: ``cost_tracker._read_tenant_caps`` soft-failed to
    «generous defaults» on stale tenant_id → typo'd id silently ran
    against a fictitious budget.

    Phase 1 (#473): same call now raises ``UnknownTenantError(LLMError)``
    inside ``provider.complete()``. The booking skill envelope catches
    LLMError and returns ``should_handoff=True`` + ``reason='llm_error'``
    so the customer sees a polite handoff instead of an exception
    crash. End-to-end here means: real BookingSkill().handle() invoked,
    the LLMError surfaces through the production envelope, friendly
    handoff returned.
    """

    def test_stale_tenant_uuid_through_booking_skill_yields_friendly_handoff(self, db) -> None:
        tenant = Tenant.objects.create(slug="stale-uuid", name="Stale UUID test")
        bu = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="stale-1",
            chat_id="stale-1",
            phone="79991234567",
            client_name="Anna",
        )
        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bu)
        context = SkillContext(
            conversation=conv,
            bot_user=bu,
            message_text="запиши меня",
            trace_id="t-stale-uuid",
        )

        # YClients fixture: present so the prefetch doesn't trip a
        # different handoff path. The LLM error must dominate.
        client = FakeYClients()
        client.services_rows = [
            Service(
                id=22,
                title="Massage",
                price_min=0,
                price_max=0,
                duration_s=3600,
                category_id=None,
                raw={},
            )
        ]
        client.staff_rows = [
            Staff(
                id=11,
                name="Olga",
                specialization="",
                rating=0.0,
                avatar="",
                position="master",
                raw={},
            )
        ]

        # Simulate the production scenario: ``provider.complete``
        # internally invokes ``cost_tracker.enforce_caps`` which raises
        # ``UnknownTenantError`` for the stale tenant. We inject this
        # at the provider boundary so the production envelope wiring
        # is exercised end-to-end without faking the cost_tracker
        # internals.
        from apps.llm.providers.openai_provider import OpenAIProvider

        with (
            patch(
                "apps.integrations.yclients.get_yclients_client",
                return_value=client,
            ),
            patch.object(
                OpenAIProvider,
                "complete",
                side_effect=UnknownTenantError("tenant_id=<stale-uuid> not found in Tenant table"),
            ),
        ):
            with tenant_scope(tenant):
                result = BookingSkill().handle(context)

        # Acceptance criterion: friendly handoff, NOT a raised
        # exception bubbling up to 500.
        assert result.should_handoff is True
        assert result.handoff_reason == "llm_error"
        # No BookingRequest written — the LLM call failed before any
        # write would occur.
        assert BookingRequest.all_tenants.filter(tenant=tenant, bot_user=bu).count() == 0
