"""MenuSkill tests (DRF-963 / U-1 + U-5 + menu callbacks).

Covers the three jobs: menu-tap translation, widened booking routing via
the ``SkillContext.intent`` contract, and the honest fallback that replaced
verbatim echo.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.skills.base import SkillContext, SkillResult
from apps.skills.menu.matching import (
    CALLBACK_MENU_BOOK,
    CALLBACK_MENU_CANCEL,
    CALLBACK_MENU_HELP,
    CALLBACK_MENU_MY_BOOKINGS,
    CALLBACK_MENU_RESCHEDULE,
    MENU_CALLBACK_TEXT,
    main_menu_buttons,
    tenant_service_stems,
)
from apps.skills.menu.skill import FALLBACK_TEXT, HELP_TEXT, MenuSkill


def _booking_stub(result: SkillResult | None = None) -> MagicMock:
    """Stand-in for the registered booking skill.

    MenuSkill resolves booking out of the registry and calls its ``handle``
    contract directly — it deliberately does NOT re-enter dispatch (a second
    registry walk let an upstream greedy skill swallow the canonical phrase;
    see test_routing_hardening.py).
    """
    booking = MagicMock()
    booking.name = "booking"
    booking.handle.return_value = result if result is not None else SkillResult()
    return booking


def _ctx(text: str, *, intent=None) -> SkillContext:
    conversation = MagicMock()
    conversation.tenant = None  # skip the optional catalog widening
    return SkillContext(
        conversation=conversation,
        bot_user=MagicMock(),
        message_text=text,
        intent=intent,
    )


class TestMatches:
    def test_claims_any_non_empty_text(self):
        assert MenuSkill().matches(_ctx("ыаывпаып")) is True
        assert MenuSkill().matches(_ctx("Хочу массаж")) is True

    def test_yields_empty_text_to_echo(self):
        """Echo owns «?» and the attachment-only «(нечем эхом)» replies."""
        assert MenuSkill().matches(_ctx("")) is False
        assert MenuSkill().matches(_ctx("   ")) is False

    def test_stands_down_when_intent_already_set(self):
        """Recursion guard — our own re-dispatch pass must not re-enter."""
        assert MenuSkill().matches(_ctx("Хочу массаж", intent=MagicMock())) is False


class TestHonestFallback:
    """U-5 — never echo."""

    def test_unrecognised_text_gets_fallback_not_echo(self):
        result = MenuSkill().handle(_ctx("ыаывпаып"))
        assert result.reply_text == FALLBACK_TEXT
        assert result.reply_text != "ыаывпаып"
        assert result.meta["reply_kind"] == "menu_fallback"

    def test_fallback_carries_the_menu_keyboard(self):
        result = MenuSkill().handle(_ctx("что-то непонятное"))
        buttons = result.action_data["attachments"][0]["payload"]["buttons"]
        assert buttons == main_menu_buttons()

    def test_stale_callback_payload_is_never_echoed(self):
        """A raw ``cb:`` payload leaking to the customer was the ugliest
        face of the echo fallback."""
        result = MenuSkill().handle(_ctx("cb:menu:does_not_exist"))
        assert result.reply_text == FALLBACK_TEXT
        assert "cb:" not in result.reply_text


class TestHelp:
    def test_help_text_and_menu(self):
        result = MenuSkill().handle(_ctx("что ты умеешь?"))
        assert result.reply_text == HELP_TEXT
        assert result.meta["reply_kind"] == "menu_help"
        assert result.action_data["attachments"][0]["payload"]["buttons"] == main_menu_buttons()

    def test_help_button_answers_locally_without_redispatch(self):
        with patch("apps.skills.registry.dispatch") as dispatch:
            result = MenuSkill().handle(_ctx(CALLBACK_MENU_HELP))
        dispatch.assert_not_called()
        assert result.reply_text == HELP_TEXT

    def test_help_is_not_framed_as_a_miss(self):
        assert HELP_TEXT != FALLBACK_TEXT
        assert "не понял" not in HELP_TEXT


class TestBookingRouting:
    """U-1 — service phrasings reach booking through the intent contract."""

    @pytest.mark.parametrize("text", ["Хочу массаж", "Мне бы маникюр", "есть окошко на завтра?"])
    def test_redispatches_with_booking_intent(self, text):
        booking_result = SkillResult(reply_text="Выберите мастера:")
        booking = _booking_stub(booking_result)
        with patch("apps.skills.registry.registered", return_value=[booking]):
            result = MenuSkill().handle(_ctx(text))

        assert result is booking_result
        routed_ctx = booking.handle.call_args.args[0]
        assert routed_ctx.intent is not None
        assert routed_ctx.intent.intent == "booking"

    def test_user_wording_is_preserved_for_the_booking_llm(self):
        """Rewriting the text would rob the booking skill of the service name."""
        booking = _booking_stub()
        with patch("apps.skills.registry.registered", return_value=[booking]):
            MenuSkill().handle(_ctx("Мне бы маникюр с покрытием"))
        assert booking.handle.call_args.args[0].message_text == "Мне бы маникюр с покрытием"

    def test_falls_back_to_menu_when_booking_is_unavailable(self):
        with patch("apps.skills.registry.registered", return_value=[]):
            result = MenuSkill().handle(_ctx("Хочу массаж"))
        assert result.reply_text == FALLBACK_TEXT

    def test_non_booking_text_never_reaches_booking(self):
        booking = _booking_stub()
        with patch("apps.skills.registry.registered", return_value=[booking]):
            MenuSkill().handle(_ctx("ыаывпаып"))
        booking.handle.assert_not_called()


class TestMenuCallbacks:
    @pytest.mark.parametrize(
        "callback",
        [
            CALLBACK_MENU_BOOK,
            CALLBACK_MENU_MY_BOOKINGS,
            CALLBACK_MENU_RESCHEDULE,
            CALLBACK_MENU_CANCEL,
        ],
    )
    def test_tap_is_translated_to_its_canonical_phrase(self, callback):
        booking = _booking_stub()
        with patch("apps.skills.registry.registered", return_value=[booking]):
            MenuSkill().handle(_ctx(callback))

        routed_ctx = booking.handle.call_args.args[0]
        assert routed_ctx.message_text == MENU_CALLBACK_TEXT[callback]
        assert routed_ctx.intent.intent == "booking"

    def test_tap_and_typed_phrase_take_the_same_route(self):
        """The button is a shortcut for typing, not a separate contract."""
        booking = _booking_stub()
        with patch("apps.skills.registry.registered", return_value=[booking]):
            MenuSkill().handle(_ctx(CALLBACK_MENU_MY_BOOKINGS))
            tapped = booking.handle.call_args.args[0]

        assert tapped.message_text == "Покажи мои записи"


class TestCatalogWidening:
    def test_tenant_service_titles_widen_the_matcher(self):
        ctx = _ctx("хочу криолиполиз")
        ctx.conversation.tenant = MagicMock()
        booking = _booking_stub()
        with (
            patch(
                "apps.skills.menu.skill.tenant_service_stems",
                return_value=("криолиполиз",),
            ),
            patch("apps.skills.registry.registered", return_value=[booking]),
        ):
            MenuSkill().handle(ctx)
        booking.handle.assert_called_once()

    @pytest.mark.django_db
    def test_reads_the_real_catalog_column(self):
        """The read is fail-soft, so a wrong column name would silently
        disable the widening instead of raising. Exercise it for real
        against the DB — a rename in apps.catalog must fail HERE, loudly.
        """
        from django.utils import timezone

        from apps.catalog.models import CatalogService
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-catalog", name="Menu Catalog")
        CatalogService.all_tenants.create(
            tenant=tenant,
            external_id=9001,
            external_updated_at=timezone.now(),
            name="Криолиполиз живота",
            is_active=True,
        )
        # The read goes through the tenant-scoped manager, so it must run
        # inside tenant_scope — exactly as the channel consumer dispatches.
        with tenant_scope(tenant):
            stems = tenant_service_stems(tenant)
        assert "криолиполиз" in stems
        # Short / generic tokens must not become stems.
        assert "живота" in stems  # 6 chars, service-specific → kept
        assert all(len(s) >= 5 for s in stems)

    @pytest.mark.django_db
    def test_out_of_tenant_scope_degrades_quietly(self):
        """The tenant-scoped manager refuses reads without a scope; the
        turn must still be answered from the seed vocabulary."""
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-noscope", name="Menu NoScope")
        assert tenant_service_stems(tenant) == ()

    def test_no_tenant_skips_the_catalog_read_entirely(self):
        """Global (tenant-less) turns must not touch the catalog."""
        assert tenant_service_stems(None) == ()

    def test_catalog_failure_degrades_to_seed_vocabulary(self):
        """A broken catalog read must not break the turn."""
        tenant = MagicMock()
        with patch(
            "apps.catalog.models.CatalogService.objects.filter",
            side_effect=RuntimeError("db down"),
        ):
            assert tenant_service_stems(tenant) == ()
