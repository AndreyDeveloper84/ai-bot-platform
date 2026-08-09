"""Regression tests for the DRF-963 review findings (PR #1160, ось correctness).

Each test here exists because a reviewer produced a concrete failing input.
They are kept together so the reasons stay legible: every one of them
guards a way the widened routing could hurt a live pilot conversation.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.skills.base import SkillContext, SkillResult
from apps.skills.menu import matching
from apps.skills.menu.matching import (
    CALLBACK_MENU_BOOK,
    looks_like_booking_request,
    mentions_service,
    tenant_service_stems,
)
from apps.skills.menu.replies import FALLBACK_TEXT
from apps.skills.menu.skill import MenuSkill


def _ctx(text: str) -> SkillContext:
    conversation = MagicMock()
    conversation.tenant = None
    return SkillContext(
        conversation=conversation,
        bot_user=MagicMock(),
        message_text=text,
    )


class TestNoSecondRegistryWalk:
    """Finding #1 — an abandoned anketa FSM killed the «Записаться» button.

    The first implementation re-entered ``registry.dispatch`` with the
    canonical phrase. Skills above booking don't all honour ``intent``:
    nutrition_anketa claims any non-``cb:`` text while its FSM is alive, so
    the tap (which anketa declines) came back as «Хочу записаться» on the
    second walk and was swallowed — permanently, since the FSM has no TTL.
    """

    def test_menu_tap_calls_booking_directly(self):
        booking = MagicMock()
        booking.name = "booking"
        booking.handle.return_value = SkillResult(reply_text="Выберите мастера:")

        with (
            patch("apps.skills.registry.registered", return_value=[booking]),
            patch("apps.skills.registry.dispatch") as dispatch,
        ):
            result = MenuSkill().handle(_ctx(CALLBACK_MENU_BOOK))

        dispatch.assert_not_called()  # no second registry walk
        booking.handle.assert_called_once()
        assert booking.handle.call_args.args[0].message_text == "Хочу записаться"
        assert result.reply_text == "Выберите мастера:"

    def test_a_greedy_upstream_skill_cannot_intercept_the_tap(self):
        """The exact shape of the bug: a skill registered ABOVE booking
        that claims any plain text must never see the canonical phrase."""
        greedy = MagicMock()
        greedy.name = "nutrition_anketa"
        greedy.matches.return_value = True
        greedy.handle.return_value = SkillResult(reply_text="Какая у тебя цель?")
        booking = MagicMock()
        booking.name = "booking"
        booking.handle.return_value = SkillResult(reply_text="Выберите мастера:")

        with patch("apps.skills.registry.registered", return_value=[greedy, booking]):
            result = MenuSkill().handle(_ctx(CALLBACK_MENU_BOOK))

        greedy.handle.assert_not_called()
        assert result.reply_text == "Выберите мастера:"

    def test_missing_booking_skill_degrades_to_menu(self):
        with patch("apps.skills.registry.registered", return_value=[]):
            result = MenuSkill().handle(_ctx(CALLBACK_MENU_BOOK))
        assert result.reply_text == FALLBACK_TEXT


class TestTypedCallbackLookalikeIsNotACallback:
    """Security finding N-1 — user text must never reach a log line.

    On MAX a tapped payload and a typed message arrive in the SAME field,
    so a customer can type «cb:menu:…». Matching on the bare prefix meant
    arbitrary text was treated as a malformed callback and echoed into the
    logs — against #842 W3 CRIT-2 (log lengths, never content), and logs
    sit outside the 152-ФЗ erasure cascade.
    """

    def test_typed_lookalike_with_pii_is_treated_as_ordinary_text(self, caplog):
        pii = "cb:menu:мой телефон +79001234567, Иванова Мария"
        with caplog.at_level("INFO"):
            result = MenuSkill().handle(_ctx(pii))

        assert result.reply_text == FALLBACK_TEXT
        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "79001234567" not in logged
        assert "Иванова" not in logged

    def test_unknown_but_well_formed_slug_logs_only_a_length(self, caplog):
        with caplog.at_level("INFO"):
            result = MenuSkill().handle(_ctx("cb:menu:retired_button"))

        assert result.reply_text == FALLBACK_TEXT
        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "retired_button" not in logged
        assert "payload_len" in logged

    @pytest.mark.parametrize(
        "text",
        [
            "cb:menu:book extra",
            "cb:menu:BOOK",
            "cb:menu:book:1",
            "cb:menu:",
            "cb:menu:книга",
        ],
    )
    def test_malformed_payloads_are_not_callbacks(self, text):
        from apps.skills.menu.matching import is_menu_callback

        assert is_menu_callback(text) is False

    def test_real_slugs_still_match(self):
        from apps.skills.menu.matching import MENU_CALLBACK_TEXT, is_menu_callback

        for callback in [*MENU_CALLBACK_TEXT, "cb:menu:help"]:
            assert is_menu_callback(callback) is True, callback


class TestHandoffIsGatedOnExplicitIntent:
    """Finding #3 — a backend blip could mute dialogues wholesale.

    ``should_handoff`` makes the channel create an AdminTask and flip the
    conversation to HUMAN_HANDOFF, which silences the bot until an operator
    closes it. Booking raises it on every provider failure. Before DRF-963
    a matcher false positive cost an echo; it must not now cost a muted
    conversation plus an operator task.
    """

    @staticmethod
    def _booking_that_handoffs():
        booking = MagicMock()
        booking.name = "booking"
        booking.handle.return_value = SkillResult(
            reply_text="Не получилось оформить запись — переключаю на менеджера.",
            should_handoff=True,
            handoff_reason="booking_yclients_failure",
        )
        return booking

    def test_inferred_intent_suppresses_the_handoff(self):
        booking = self._booking_that_handoffs()
        with patch("apps.skills.registry.registered", return_value=[booking]):
            result = MenuSkill().handle(_ctx("Хочу массаж"))

        assert result.should_handoff is False
        assert result.reply_text == FALLBACK_TEXT

    def test_explicit_tap_still_escalates(self):
        """The customer asked to book and the backend is down — a human
        SHOULD take over. Suppressing here would hide a real outage."""
        booking = self._booking_that_handoffs()
        with patch("apps.skills.registry.registered", return_value=[booking]):
            result = MenuSkill().handle(_ctx(CALLBACK_MENU_BOOK))

        assert result.should_handoff is True
        assert result.handoff_reason == "booking_yclients_failure"

    def test_non_infrastructure_reasons_are_never_swallowed(self):
        """Suppression is an allowlist of transient provider failures.

        Booking's reason vocabulary is extensible; a future
        legally-sensitive or payment-dispute escalation must not be eaten
        by a routing helper just because the intent was inferred.
        """
        booking = MagicMock()
        booking.name = "booking"
        booking.handle.return_value = SkillResult(
            reply_text="Передаю менеджеру.",
            should_handoff=True,
            handoff_reason="legally_sensitive",
        )
        with patch("apps.skills.registry.registered", return_value=[booking]):
            result = MenuSkill().handle(_ctx("Хочу массаж"))

        assert result.should_handoff is True
        assert result.handoff_reason == "legally_sensitive"

    def test_normal_booking_replies_pass_through_on_both_paths(self):
        booking = MagicMock()
        booking.name = "booking"
        booking.handle.return_value = SkillResult(reply_text="Выберите время:")
        with patch("apps.skills.registry.registered", return_value=[booking]):
            assert MenuSkill().handle(_ctx("Хочу массаж")).reply_text == "Выберите время:"
            assert MenuSkill().handle(_ctx(CALLBACK_MENU_BOOK)).reply_text == "Выберите время:"


class TestCatalogReadIsFailSoftForReal:
    """Finding #2 — the guard covered building the queryset, not running it."""

    @pytest.mark.django_db
    def test_construction_failure_degrades_instead_of_propagating(self):
        """Failure while BUILDING the queryset. The lazy-iteration path —
        the one the original guard actually missed — is the sibling test
        below, which patches ``QuerySet.__iter__``."""
        from django.db import OperationalError

        from apps.catalog.models import CatalogService
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-lazy", name="Menu Lazy")
        with (
            patch.object(
                CatalogService.objects.__class__,
                "filter",
                side_effect=OperationalError("server closed the connection"),
            ),
            tenant_scope(tenant),
        ):
            assert tenant_service_stems(tenant) == ()

    @pytest.mark.django_db
    def test_iteration_failure_degrades_too(self):
        """Patch at the iterator level — the exact path the guard missed."""
        from django.db.models.query import QuerySet

        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-lazy2", name="Menu Lazy 2")
        with (
            patch.object(QuerySet, "__iter__", side_effect=RuntimeError("connection lost")),
            tenant_scope(tenant),
        ):
            assert tenant_service_stems(tenant) == ()


class TestCatalogStemQuality:
    """Findings #5 and #6 — unstable ordering and leaky stopwords."""

    @pytest.mark.django_db
    def test_gift_card_does_not_become_a_service_stem(self):
        """«Подарочная карта» is a routine catalog row; the stem «карта»
        would route «Карта не прошла, что делать» into a booking flow."""
        from django.utils import timezone

        from apps.catalog.models import CatalogService
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-giftcard", name="Menu GiftCard")
        for i, name in enumerate(["Подарочная карта", "Массаж спины 60 минут"]):
            CatalogService.all_tenants.create(
                tenant=tenant,
                external_id=7100 + i,
                external_updated_at=timezone.now(),
                name=name,
                is_active=True,
            )
        with tenant_scope(tenant):
            stems = tenant_service_stems(tenant)

        assert "массаж" in stems
        assert "карта" not in stems
        assert "подарочная" not in stems
        assert "минут" not in stems

    @pytest.mark.django_db
    def test_stopwords_cover_every_inflection(self):
        """Exact-match filtering dropped «программа» but kept «программы»,
        and the kept form matched everything the dropped one would have."""
        from django.utils import timezone

        from apps.catalog.models import CatalogService
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-infl", name="Menu Infl")
        for i, name in enumerate(["Спа программы для двоих", "Скидки и акции"]):
            CatalogService.all_tenants.create(
                tenant=tenant,
                external_id=7200 + i,
                external_updated_at=timezone.now(),
                name=name,
                is_active=True,
            )
        with tenant_scope(tenant):
            stems = tenant_service_stems(tenant)

        for leaked in ("программы", "скидки", "акции"):
            assert leaked not in stems, leaked

    @pytest.mark.django_db
    def test_catalog_read_is_deterministically_ordered(self):
        """A LIMIT without an ORDER BY has no stable row order in Postgres,
        so the same phrase could route differently on consecutive turns.

        Asserted against the emitted SQL, not against a mocked manager —
        the point is what the database is actually asked for.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="menu-order", name="Menu Order")
        with tenant_scope(tenant), CaptureQueriesContext(connection) as captured:
            tenant_service_stems(tenant)

        catalog_sql = [q["sql"] for q in captured.captured_queries if "catalogservice" in q["sql"]]
        assert catalog_sql, "expected a catalog read"
        assert all("ORDER BY" in sql.upper() for sql in catalog_sql), catalog_sql


class TestStemCoverage:
    def test_cleaning_service_matches_in_the_accusative(self):
        """Finding #4 — «чистка» was stored unstemmed, so only the
        nominative matched."""
        assert mentions_service("Хочу чистку") is True
        assert mentions_service("запишите на чистку") is True

    def test_catalog_stems_are_only_consulted_when_needed(self):
        """Finding #8 — the DB round-trip must not fire for turns the
        cheap signals already answer."""
        calls: list[int] = []

        def _stems() -> tuple[str, ...]:
            calls.append(1)
            return ()

        # Availability phrasing decides before the catalog is consulted.
        assert looks_like_booking_request("есть окошко на завтра", extra_stems=_stems) is True
        assert calls == []
        # A seed service word decides too.
        assert looks_like_booking_request("хочу массаж", extra_stems=_stems) is True
        assert calls == []
        # Only an otherwise-unmatched turn pays for the lookup.
        assert looks_like_booking_request("ыаывпаып", extra_stems=_stems) is False
        assert calls == [1]

    def test_prefix_stopword_helper_is_inflection_proof(self):
        for word in ("программа", "программы", "программу", "карта", "карты", "скидки"):
            assert matching._is_stopword(word) is True, word
        for word in ("массаж", "маникюр", "криолиполиз"):
            assert matching._is_stopword(word) is False, word
