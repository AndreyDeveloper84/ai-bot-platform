"""§2.4 (owner 11.09), slice S2-2 — the person's context is closed to a SHADOW (DRF-1700).

One gate, four readers, two doors where a status is born. Every test has
its other side: the same call with a LINKED (or client-contour) shell gives
the answer, so a refusal is the gate declining, not the reader breaking.
"""

from __future__ import annotations

import uuid

import pytest
from django.utils import timezone

from apps.consent.memory import can_store_green_memory
from apps.consent.models import ConsentRecord
from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import salon_customer as sc
from apps.identity.services.person_context_gate import (
    REASON_SHADOW,
    REASON_UNRESOLVED,
    person_context_access,
)
from apps.nutrition_proactive import prefs
from apps.persona.memory_commands import (
    handle_memory_command,
    memory_show_chips,
    render_memory_summary,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def salon() -> Tenant:
    return Tenant.objects.create(slug="s22-salon", name="Салон")


@pytest.fixture
def global_bot() -> Tenant:
    obj, _ = Tenant.all_objects.get_or_create(
        slug=GLOBAL_BOT_TENANT_SLUG, defaults={"name": "Клиентский бот"}
    )
    return obj


def _shell(tenant: Tenant, cid: str, status: str | None = None, **kw) -> BotUser:
    bu = BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id=cid, **kw)
    if status is not None:
        BotUser.all_tenants.filter(pk=bu.pk).update(
            customer_status=status, customer_status_at=timezone.now()
        )
        bu.refresh_from_db()
    return bu


def _remembered(ayla_id: uuid.UUID, tenant: Tenant) -> None:
    upc = UserPersonalContext.objects.create(user_id=ayla_id)
    MemoryEntry.objects.create(
        user_id=ayla_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
        consent_at=timezone.now(),
        source_tenant_id=tenant.id,
    )


# --- the gate itself -------------------------------------------------------------


class TestTheGate:
    def test_client_contour_passes_whatever_the_status(self, global_bot):
        for status in BotUser.CustomerStatus:
            bu = _shell(global_bot, f"1{status.value[:3]}", status=status.value)
            assert person_context_access(bu) is None, status

    def test_linked_salon_shell_passes(self, salon):
        assert person_context_access(_shell(salon, "2001", status="linked")) is None

    def test_shadow_refuses_by_name(self, salon):
        r = person_context_access(_shell(salon, "2002", status="shadow"))
        assert r is not None and r.reason == REASON_SHADOW

    def test_unresolved_refuses_by_its_own_name(self, salon):
        """«The rule has not been applied» is not a permission — and it is not
        «shadow» either: the two need different people to act."""
        r = person_context_access(_shell(salon, "2003"))
        assert r is not None and r.reason == REASON_UNRESOLVED

    def test_the_refusal_is_logged_with_its_reason(self, salon, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="apps.identity.services.person_context_gate"):
            person_context_access(_shell(salon, "2004", status="shadow"))
        assert any("reason=shadow" in rec.getMessage() for rec in caplog.records)


# --- the four readers, both sides -------------------------------------------------


class TestTheReadersAskFirst:
    def test_green_memory_consent_is_not_enough_for_a_shadow(self, salon):
        ayla = uuid.uuid4()
        linked = _shell(salon, "3001", status="linked", ayla_user_id=ayla)
        shadow = _shell(salon, "3002", status="shadow", ayla_user_id=uuid.uuid4())
        for bu in (linked, shadow):
            ConsentRecord.all_tenants.create(
                tenant=salon,
                bot_user=bu,
                consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
                granted=True,
                source="test:fixture",
            )
        assert can_store_green_memory(linked) is True
        assert can_store_green_memory(shadow) is False

    def test_memory_summary_and_chips_are_empty_for_a_shadow(self, salon):
        ayla = uuid.uuid4()
        _remembered(ayla, salon)
        linked = _shell(salon, "3003", status="linked", ayla_user_id=ayla)
        shadow = _shell(salon, "3004", status="shadow", ayla_user_id=ayla)
        assert "веган" in render_memory_summary(linked).lower()
        assert memory_show_chips(linked)
        assert "веган" not in render_memory_summary(shadow).lower()
        assert memory_show_chips(shadow) == []

    def test_memory_commands_fall_through_for_a_shadow(self, salon):
        ayla = uuid.uuid4()
        _remembered(ayla, salon)
        linked = _shell(salon, "3005", status="linked", ayla_user_id=ayla)
        shadow = _shell(salon, "3006", status="shadow", ayla_user_id=ayla)
        assert handle_memory_command(
            user_id=ayla, text="покажи что знаешь обо мне", bot_user=linked
        )
        assert (
            handle_memory_command(user_id=ayla, text="покажи что знаешь обо мне", bot_user=shadow)
            is None
        )

    def test_nutrition_prefs_are_empty_and_unwritable_for_a_shadow(self, salon):
        stored = {"nutrition_proactive": {"daily_report_time": "09:00"}}
        linked = _shell(salon, "3007", status="linked", context=stored)
        shadow = _shell(salon, "3008", status="shadow", context=stored)
        assert prefs.get_prefs(linked) == {"daily_report_time": "09:00"}
        assert prefs.get_prefs(shadow) == {}
        merged = prefs.merge_prefs(shadow, {"daily_report_time": "10:00"})
        assert merged == stored, "nothing merged for a shadow; the context comes back untouched"
        assert prefs.merge_prefs(linked, {"daily_report_time": "10:00"})["nutrition_proactive"] == {
            "daily_report_time": "10:00"
        }


# --- the doors where a status is born ----------------------------------------------


class TestStatusIsBornWhereTheLinkIs:
    def test_a_new_salon_shell_is_classified_at_creation(self, salon, global_bot, settings):
        from apps.identity.services.resolver import resolve_or_create_bot_user
        from apps.tenancy.context import tenant_scope

        settings.STRICT_TENANT_SCOPE = "strict"
        _shell(global_bot, "4001")  # the person is already in the client contour
        with tenant_scope(salon):
            known = resolve_or_create_bot_user(channel="max", channel_user_id="4001")
            stranger = resolve_or_create_bot_user(channel="max", channel_user_id="4002")
        assert known.customer_status == BotUser.CustomerStatus.LINKED
        assert known.customer_source == BotUser.CustomerSource.SALON_ASSISTANT
        assert stranger.customer_status == BotUser.CustomerStatus.SHADOW
        assert stranger.customer_status_at is not None
        assert person_context_access(known) is None
        assert person_context_access(stranger) is not None

    def test_writing_an_identity_link_advances_shadow_to_linked(self, salon):
        shadow = _shell(salon, "4003", status="shadow")
        assert person_context_access(shadow) is not None
        written = sc.advance_to_linked("max", "4003")
        assert written == 1
        shadow.refresh_from_db()
        assert shadow.customer_status == BotUser.CustomerStatus.LINKED
        assert shadow.customer_source == BotUser.CustomerSource.SALON_ASSISTANT
        assert person_context_access(shadow) is None
        # Already LINKED is left alone.
        assert sc.advance_to_linked("max", "4003") == 0

    def test_ensure_ayla_link_advances_the_person(self, salon, monkeypatch):
        from apps.identity.services import ayla_link

        shadow = _shell(salon, "4004", status="shadow")
        ayla_link._persist(shadow, uuid.uuid4(), is_proxy=False)
        shadow.refresh_from_db()
        assert shadow.customer_status == BotUser.CustomerStatus.LINKED
