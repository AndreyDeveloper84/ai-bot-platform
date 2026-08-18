"""The employee's thread with the assistant (DRF-1061 step 0).

Until now the salon bot kept no history at all, so there was nothing an
assistant could be built on. These tests pin the three properties that make
the new thread safe to build on:

* **it is not the customer thread** — separate tables, and opening one
  leaves ``Conversation`` untouched;
* **one active thread per person per salon**, so two turns arriving
  together cannot fork the history;
* **the tenant invariant holds on every write**, the same way
  ``record_message`` holds it — a handler that resolves a thread in one
  scope and writes to it in another must fail loudly, not quietly.
"""

from __future__ import annotations

import uuid

import pytest

from apps.conversations.models import (
    Conversation,
    Message,
    StaffAssistantMessage,
    StaffAssistantThread,
)
from apps.conversations.staff_assistant import (
    close_staff_thread,
    recent_staff_history,
    record_staff_message,
    resolve_active_staff_thread,
)
from apps.identity.models import BotUser
from apps.tenancy.context import tenant_scope
from apps.tenancy.exceptions import CrossTenantError
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="thread-salon", name="Формула тела")


@pytest.fixture
def other_tenant() -> Tenant:
    return Tenant.objects.create(slug="thread-other", name="Другой салон")


def _person(tenant: Tenant, channel_user_id: str = "88001") -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=channel_user_id,
    )


class TestOpening:
    def test_a_thread_is_created_on_first_use(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person, role_at_open="admin")

        assert thread is not None
        assert thread.tenant_id == tenant.id
        assert thread.role_at_open == "admin"

    def test_the_same_person_gets_the_same_thread(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            first = resolve_active_staff_thread(person)
            second = resolve_active_staff_thread(person)

        assert first.id == second.id
        assert StaffAssistantThread.all_tenants.count() == 1

    def test_the_role_is_recorded_once_not_re_read(self, tenant):
        """What they WERE when the conversation started.

        Re-reading it every turn would defeat the point: after access is
        revoked the resolver says «customer», and the history has to keep
        explaining why the assistant answered as an admin back then.
        """

        person = _person(tenant)

        with tenant_scope(tenant):
            resolve_active_staff_thread(person, role_at_open="owner")
            again = resolve_active_staff_thread(person, role_at_open="customer")

        assert again.role_at_open == "owner"

    def test_read_only_callers_can_decline_to_create(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person, create_if_missing=False)

        assert thread is None
        assert not StaffAssistantThread.all_tenants.exists()

    def test_no_tenant_in_scope_is_loud(self, tenant):
        person = _person(tenant)

        with pytest.raises(ValueError, match="tenant in scope"):
            resolve_active_staff_thread(person)

    def test_a_bot_user_from_another_salon_is_refused(self, tenant, other_tenant):
        stranger = _person(other_tenant, "88002")

        with tenant_scope(tenant), pytest.raises(CrossTenantError):
            resolve_active_staff_thread(stranger)


class TestNotTheCustomerThread:
    def test_opening_one_leaves_conversation_untouched(self, tenant):
        """The whole reason these are separate tables.

        Eighteen modules read ``Conversation`` assuming it is a customer.
        A staff row landing there would surface an employee in their own
        inbox and in the follow-up sweep.
        """

        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            record_staff_message(thread, role="user", content="что у меня завтра")

        assert Conversation.all_tenants.count() == 0
        assert Message.all_tenants.count() == 0
        assert StaffAssistantMessage.all_tenants.count() == 1


class TestWriting:
    def test_a_turn_is_appended(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            msg = record_staff_message(thread, role="user", content="привет")

        assert msg.tenant_id == tenant.id
        assert msg.thread_id == thread.id
        assert msg.content == "привет"

    def test_last_message_at_is_bumped(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            assert thread.last_message_at is None
            record_staff_message(thread, role="user", content="привет")

        thread.refresh_from_db()
        assert thread.last_message_at is not None

    def test_writing_into_another_scope_is_refused(self, tenant, other_tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)

        # Handler bug shape: thread resolved under one salon, written under
        # another. Must be loud — a silent cross-tenant row is worse.
        with tenant_scope(other_tenant), pytest.raises(CrossTenantError):
            record_staff_message(thread, role="user", content="привет")

    def test_telemetry_rides_along(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            msg = record_staff_message(
                thread,
                role="assistant",
                content="завтра три записи",
                tool_name="my_day",
                tokens_in=120,
                tokens_out=18,
                llm_provider="openai",
                llm_model="gpt-4o-mini",
                llm_cost_usd="0.000042",
            )

        assert msg.tool_name == "my_day"
        assert msg.tokens_in == 120
        assert str(msg.llm_cost_usd) == "0.000042"

    def test_an_explicit_trace_id_is_kept(self, tenant):
        person = _person(tenant)
        trace = uuid.uuid4()

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            msg = record_staff_message(thread, role="user", content="привет", trace_id=str(trace))

        assert msg.trace_id == trace


class TestHistory:
    def test_returns_chronological_order(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            for i in range(3):
                record_staff_message(thread, role="user", content=f"line {i}")
            history = recent_staff_history(thread)

        assert [m.content for m in history] == ["line 0", "line 1", "line 2"]

    def test_keeps_only_the_last_n(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            for i in range(6):
                record_staff_message(thread, role="user", content=f"line {i}")
            history = recent_staff_history(thread, limit=2)

        assert [m.content for m in history] == ["line 4", "line 5"]

    def test_the_current_turn_can_be_excluded(self, tenant):
        # The caller records the inbound line before composing the prompt,
        # so the model must not be handed it twice.
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            record_staff_message(thread, role="user", content="старое")
            current = record_staff_message(thread, role="user", content="сейчас")
            history = recent_staff_history(thread, exclude_id=current.id)

        assert [m.content for m in history] == ["старое"]

    def test_another_persons_thread_is_not_visible(self, tenant):
        one = _person(tenant, "88010")
        two = _person(tenant, "88011")

        with tenant_scope(tenant):
            thread_one = resolve_active_staff_thread(one)
            thread_two = resolve_active_staff_thread(two)
            record_staff_message(thread_one, role="user", content="моё")
            history = recent_staff_history(thread_two)

        assert history == []


class TestOrdering:
    """Why the model carries a `seq` at all.

    This is a regression, not a hypothetical: the first run of these tests
    came back with the history shuffled. Three turns of one tool round trip
    are written within milliseconds, and a clock with coarse resolution
    stamps them identically — sorting on `created_at` alone then hands the
    model an answer before its question.
    """

    def test_positions_are_dense_and_ordered(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            for role, text in (("user", "а"), ("tool", "б"), ("assistant", "в")):
                record_staff_message(thread, role=role, content=text)

        rows = StaffAssistantMessage.all_tenants.filter(thread=thread).order_by("seq")
        assert [r.seq for r in rows] == [0, 1, 2]

    def test_order_survives_a_frozen_clock(self, tenant):
        from unittest.mock import patch

        from django.utils import timezone as dj_timezone

        person = _person(tenant)
        frozen = dj_timezone.now()

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            # Every row lands on the same timestamp — the exact condition
            # that shuffled the history before `seq` existed.
            with patch("django.utils.timezone.now", return_value=frozen):
                for text in ("вопрос", "данные", "ответ"):
                    record_staff_message(thread, role="user", content=text)
            history = recent_staff_history(thread)

        assert [m.content for m in history] == ["вопрос", "данные", "ответ"]


class TestClosing:
    def test_a_closed_thread_makes_room_for_a_new_one(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            first = resolve_active_staff_thread(person)
            close_staff_thread(first)
            second = resolve_active_staff_thread(person)

        assert second.id != first.id
        assert StaffAssistantThread.all_tenants.count() == 2

    def test_closing_keeps_the_history(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            thread = resolve_active_staff_thread(person)
            record_staff_message(thread, role="user", content="было")
            close_staff_thread(thread)

        assert StaffAssistantMessage.all_tenants.filter(thread=thread).count() == 1

    def test_a_soft_deleted_thread_makes_room_too(self, tenant):
        person = _person(tenant)

        with tenant_scope(tenant):
            first = resolve_active_staff_thread(person)
            first.mark_deleted()
            second = resolve_active_staff_thread(person)

        assert second.id != first.id
