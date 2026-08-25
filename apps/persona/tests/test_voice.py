"""One source for who the assistant says it is.

The product introduced itself under three names decided in three unrelated
files: «Ayla» in the customer concierge, «Помощник» hardcoded in a master's
AI drafts, and per-tenant `brand_voice.persona` in the FAQ and booking
skills. A customer who received a reply a master sent from a draft met a
different assistant than the one they had been talking to. Nobody chose
that — three files each chose sensibly on their own.

These tests hold the two properties that keep it from happening again:

* every surface takes its name from one table, so a difference is a
  decision someone can read;
* the offline mirror of the frozen library constant is identical to the
  constant — nothing compared them before, and a divergence would have
  read as «CI says one thing, production says another».
"""

from __future__ import annotations

import pytest

from apps.persona.voice import (
    DEFAULT_SALON_PERSONA,
    SALON_ADMIN_NAME,
    SALON_BUSINESS_NAME,
    SURFACE_MARKETPLACE,
    SURFACE_SALON,
    _FROZEN_MIRROR,
    assistant_identity,
    frozen_voice_fields,
    known_surfaces,
)


class TestTheMirrorMatchesTheLibrary:
    def test_every_mirrored_field_is_identical(self):
        """A hand-copied constant with no check is a constant that rots."""

        pytest.importorskip("ayla_ai_core")
        from ayla_ai_core import AYLA_MARKETPLACE_VOICE as voice

        assert _FROZEN_MIRROR["assistant_name"] == voice.assistant_name
        assert _FROZEN_MIRROR["business_name"] == voice.business_name
        assert _FROZEN_MIRROR["domain"] == voice.domain
        assert _FROZEN_MIRROR["off_topic_redirect"] == voice.off_topic_redirect

    def test_the_reader_prefers_the_library(self):
        pytest.importorskip("ayla_ai_core")
        from ayla_ai_core import AYLA_MARKETPLACE_VOICE as voice

        assert frozen_voice_fields()["assistant_name"] == voice.assistant_name


class TestSurfaceIdentity:
    def test_the_marketplace_is_ayla(self):
        assert assistant_identity(SURFACE_MARKETPLACE).name == "Ayla"

    def test_the_salon_is_pomoshchnik(self):
        """Deliberate, per master-mobile §M6 — one identity inside a salon.

        Pinned so the difference stays a decision. Changing it is a product
        call and an edit to `_SURFACE_NAMES`; this test is what makes that
        edit visible instead of incidental.
        """

        assert assistant_identity(SURFACE_SALON).name == "Помощник"

    def test_every_surface_has_exactly_one_name(self):
        names = {s: assistant_identity(s).name for s in known_surfaces()}

        assert all(names.values()), f"a surface with no name: {names}"

    def test_shared_fields_do_not_vary_by_surface(self):
        # Only the name differs. Domain and the off-topic line describe the
        # product, not the doorway a person came through.
        market = assistant_identity(SURFACE_MARKETPLACE)
        salon = assistant_identity(SURFACE_SALON)

        assert market.domain == salon.domain
        assert market.business_name == salon.business_name
        assert market.off_topic_redirect == salon.off_topic_redirect

    def test_an_unknown_surface_does_not_raise(self):
        # A typo should hand someone the wrong-but-sane name, not a
        # traceback in the middle of their conversation.
        assert assistant_identity("nonsense").name == "Ayla"  # type: ignore[arg-type]


class TestDefaultSalonPersona:
    def test_the_persona_is_built_from_its_parts(self):
        assert DEFAULT_SALON_PERSONA == (
            f"{SALON_ADMIN_NAME}, администратор салона «{SALON_BUSINESS_NAME}»"
        )

    def test_the_parts_are_the_pilot_salon(self):
        # Pinned so a product rename is an edit here, visible in a diff,
        # not a string someone changed inside a skill module.
        assert SALON_ADMIN_NAME == "Алина"
        assert SALON_BUSINESS_NAME == "Формула тела"


class TestTheCallersUseIt:
    def test_the_concierge_prompt_carries_the_marketplace_name(self):
        from apps.orchestrator.concierge import build_concierge_system_prompt

        prompt = build_concierge_system_prompt()

        assert assistant_identity(SURFACE_MARKETPLACE).name in prompt

    def test_the_concierge_follows_a_marketplace_rename(self, monkeypatch):
        """The prompt must follow `_SURFACE_NAMES`, not the raw frozen dict.

        The test above passes either way: today the table holds no
        marketplace override, so the surface name and
        ``frozen_voice_fields()["assistant_name"]`` are the same word. A
        caller reading the raw field looks correct right up until someone
        renames the surface — and then the bot introduces itself one way in
        the concierge and another everywhere else, discovered by a person
        in a chat rather than by CI.

        So: rename the surface and require the prompt to notice.
        """

        import apps.persona.voice as voice_module
        from apps.orchestrator.concierge import build_concierge_system_prompt

        monkeypatch.setitem(voice_module._SURFACE_NAMES, SURFACE_MARKETPLACE, "Айла")

        prompt = build_concierge_system_prompt()

        assert assistant_identity(SURFACE_MARKETPLACE).name == "Айла"  # the setup took
        assert "Айла" in prompt
        # And the pre-rename name is gone from the self-introduction: a
        # prompt that carries both names has not renamed anything.
        assert "Ты — Ayla" not in prompt

    def test_discovery_reads_the_shared_source(self):
        from apps.orchestrator.discovery import _discovery_voice_fields

        assert _discovery_voice_fields() == frozen_voice_fields()

    def test_discovery_self_intro_line_is_unchanged(self):
        """DRF-1265 swaps the SOURCE of the name, not the sentence.

        Characterization pin: the line a user reads must be byte-identical
        before and after the refactor.
        """

        from apps.orchestrator.discovery import build_discovery_prompt

        system = build_discovery_prompt("хочу маникюр")[0]["content"]

        assert "Ты — Ayla, AI-помощник «Ayla — AI Self-Care»." in system

    def test_discovery_follows_a_marketplace_rename(self, monkeypatch):
        """The discovery prompt must take the name from `_SURFACE_NAMES`.

        Same argument as the concierge test above: reading
        ``voice['assistant_name']`` looks correct today and silently ignores
        the rename the day someone makes the product call. Rename the
        surface and require the prompt to notice.
        """

        import apps.persona.voice as voice_module
        from apps.orchestrator.discovery import build_discovery_prompt

        monkeypatch.setitem(voice_module._SURFACE_NAMES, SURFACE_MARKETPLACE, "Айла")

        system = build_discovery_prompt("хочу маникюр")[0]["content"]

        assert assistant_identity(SURFACE_MARKETPLACE).name == "Айла"  # the setup took
        assert "Ты — Айла" in system
        assert "Ты — Ayla" not in system

    def test_both_skills_read_the_persona_from_one_place(self):
        """FAQ and booking held two verbatim «Алина» literals (DRF-1265).

        The `is` assertions are the point: two equal strings typed in two
        files are equal today and free to drift tomorrow. One imported
        object cannot.
        """

        from apps.skills.booking.skill import _DEFAULT_BRAND_VOICE as booking_voice
        from apps.skills.faq.skill import _DEFAULT_BRAND_VOICE as faq_voice

        assert faq_voice.persona == booking_voice.persona == DEFAULT_SALON_PERSONA
        assert faq_voice.persona is DEFAULT_SALON_PERSONA
        assert booking_voice.persona is DEFAULT_SALON_PERSONA

    def test_the_max_welcome_names_the_same_salon(self):
        """The MAX welcome names the pilot salon from the shared constant.

        The wording itself is the ported legacy greeting (prod since
        2026-04) and stays byte-identical; what changes is that «Формула
        тела» is no longer a string only this file knows.
        """

        from apps.channels.max.handler import _WELCOME_TEXT

        assert SALON_BUSINESS_NAME in _WELCOME_TEXT
        assert _WELCOME_TEXT == (
            "Здравствуйте! 👋\n\n"
            "Это бот массажного салона «Формула тела» в Пензе.\n"
            "Помогу записаться, расскажу об услугах и отвечу на частые вопросы.\n\n"
            "Выберите раздел:"
        )

    @pytest.mark.django_db
    def test_the_master_draft_prompt_carries_the_salon_name(self):
        from django.utils import timezone

        from apps.catalog.models import CatalogMaster
        from apps.master_api.services.ai_drafts import _build_prompt_messages
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="voice-salon", name="Формула тела")
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            name="Ольга",
            external_id=None,
            external_updated_at=timezone.now(),
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
        )

        messages = _build_prompt_messages(master=master, history=[])
        system = messages[0]["content"]

        assert assistant_identity(SURFACE_SALON).name in system
        # And the marketplace name must NOT leak into a salon reply.
        assert "Ayla" not in system
