"""The export must report its own composition — completely (DRF-1370).

Two halves.

``TestTheDeclarationMatchesTheInventory`` is the ratchet. The registry
``apps.identity.personal_fields.PERSONAL_FIELDS`` is already a complete
inventory of the personal slots in this repository — ``personal_field_guard``
discovers them from the code and fails on any it cannot find a line for. These
tests bind the export's coverage table to that inventory in BOTH directions,
so neither of the two silent failures can ship:

* a personal field is added and nobody decides whether the subject sees it;
* a field is removed and the export keeps explaining a column that is gone.

``TestTheExportSaysWhatItHolds`` is the other half: the payload itself. It
checks that the preferences a person set on our own screen come back in their
own export — they did not before — and that the ``coverage`` block naming
everything else is actually in the file, not just in this module.
"""

from __future__ import annotations

import uuid

import pytest

from apps.identity.export_coverage import (
    EXCLUSIONS,
    KNOWN_LIMITS,
    NON_REGISTRY_STORES,
    REASONS,
    SECTIONS,
    build_coverage_section,
)
from apps.identity.models import BotUser, UserPreferences
from apps.identity.personal_fields import PERSONAL_FIELDS
from apps.identity.services.privacy import export_personal_data
from apps.tenancy.models import Tenant


def _registry_sites() -> set[str]:
    return {field.site for field in PERSONAL_FIELDS}


class TestTheDeclarationMatchesTheInventory:
    def test_every_stored_slot_has_an_export_decision(self):
        """A personal field with no decision is the failure this prevents.

        The message names the slots so the person who added one is told what
        to do, not merely that something is wrong.
        """
        decided = set(SECTIONS) | set(EXCLUSIONS)
        undecided = sorted(_registry_sites() - decided)
        assert not undecided, (
            "personal slots with no export decision — add each to "
            "apps/identity/export_coverage.py, either to SECTIONS (the JSON key "
            "its value appears under) or to EXCLUSIONS with a reason: "
            f"{undecided}"
        )

    def test_no_decision_outlives_its_slot(self):
        """A reason for a column that no longer exists misleads the reader."""
        stale = sorted((set(SECTIONS) | set(EXCLUSIONS)) - _registry_sites())
        assert not stale, (
            "export coverage explains slots the registry no longer has — "
            "delete these lines from apps/identity/export_coverage.py: "
            f"{stale}"
        )

    def test_a_slot_is_either_exported_or_withheld_never_both(self):
        assert not (set(SECTIONS) & set(EXCLUSIONS))

    def test_every_exclusion_names_a_reason_that_exists(self):
        missing = sorted({slug for slug in EXCLUSIONS.values() if slug not in REASONS})
        assert not missing, f"EXCLUSIONS point at reason slugs with no prose: {missing}"

    def test_no_reason_is_a_shrug(self):
        """A one-line «not exported» is a way of not looking at it.

        Same floor, and the same argument, as ``personal_field_guard`` applies
        to the registry's own ``why`` column.
        """
        for slug, prose in {**REASONS, **NON_REGISTRY_STORES}.items():
            assert len(prose) >= 120, f"reason «{slug}» is too short to be a reason"
        for limit in KNOWN_LIMITS:
            assert len(limit) >= 120

    def test_no_reason_outlives_the_slots_that_used_it(self):
        """Prose kept for a column nobody has any more rots the same way a
        stale decision does — so the reasons ratchet in both directions too.

        This fired for real: DRF-1371 removed ``UserPreferences.allergies``
        while this branch was open. The test above named the dead decision;
        this one named the orphaned paragraph standing behind it.
        """
        orphaned = sorted(set(REASONS) - set(EXCLUSIONS.values()))
        assert not orphaned, f"REASONS nothing points at any more: {orphaned}"

    def test_yellow_and_red_are_named_as_withheld(self):
        """«Не добавлять молча» is satisfied by saying so, not by omitting."""
        assert "identity.MemoryEntry:yellow" in NON_REGISTRY_STORES
        assert "identity.MemoryEntry:red" in NON_REGISTRY_STORES

    def test_the_conversation_and_the_diary_are_named(self):
        """The two stores DRF-1370 called out that the registry cannot see."""
        assert "conversations.Message.content" in NON_REGISTRY_STORES
        assert "conversations.Conversation.skill_state" in NON_REGISTRY_STORES


class TestTheCoverageBlock:
    def test_nothing_renders_as_undeclared(self):
        section = build_coverage_section()
        for row in section["withheld"]:
            assert "СОСТАВ НЕ ОБЪЯВЛЕН" not in row["reason"]
            assert row["reason"]

    def test_every_registry_slot_appears_exactly_once(self):
        section = build_coverage_section()
        listed = [site for sites in section["included"].values() for site in sites]
        listed += [row["field"] for row in section["withheld"] if row["field"] in _registry_sites()]
        assert sorted(listed) == sorted(_registry_sites())
        assert len(listed) == len(set(listed))

    def test_the_interim_window_is_declared(self):
        """The export's own residual gap, written down rather than discovered."""
        assert any("развёртк" in limit for limit in build_coverage_section()["known_limits"])


@pytest.mark.django_db
class TestTheExportSaysWhatItHolds:
    @pytest.fixture
    def bot_user(self) -> BotUser:
        tenant = Tenant.objects.create(slug="cov-test", name="Coverage Test")
        return BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="777",
            chat_id="777",
            ayla_user_id=uuid.uuid4(),
        )

    def test_the_preferences_a_person_set_come_back_in_their_own_export(self, bot_user):
        """The plainest under-report in the file, before DRF-1370.

        Someone opened the profile screen, switched promo on and retention off,
        entered a birthday — and their «выгрузить мои данные» came back without
        a word about any of it.
        """
        UserPreferences.all_tenants.create(
            bot_user=bot_user,
            tenant=bot_user.tenant,
            notify_retention=False,
            notify_promo=True,
            birthday_date="1991-05-17",
        )

        payload = export_personal_data(bot_user, client=_NoAyla())

        assert payload["preferences"] == [
            {
                "notify_reminders": True,
                "notify_retention": False,
                "notify_promo": True,
                "notify_birthday": True,
                "birthday_date": "1991-05-17",
                "updated_at": payload["preferences"][0]["updated_at"],
            }
        ]

    def test_the_special_categories_are_named_but_not_carried(self, bot_user):
        """Yellow and red are declared as withheld — «не молча» is satisfied by
        saying so in the very file the person is handed.

        ``UserPreferences.allergies`` used to be the third entry here. DRF-1371
        removed the column while this branch was open, and the coverage ratchet
        turned that into a failing test naming the dead line, rather than a
        stale paragraph nobody would have reread.
        """
        UserPreferences.all_tenants.create(bot_user=bot_user, tenant=bot_user.tenant)

        payload = export_personal_data(bot_user, client=_NoAyla())

        withheld = {row["field"] for row in payload["coverage"]["withheld"]}
        assert "identity.MemoryEntry:yellow" in withheld
        assert "identity.MemoryEntry:red" in withheld

    def test_the_file_carries_its_own_composition(self, bot_user):
        payload = export_personal_data(bot_user, client=_NoAyla())

        coverage = payload["coverage"]
        assert coverage["explanation"]
        assert coverage["included"]["preferences"]
        assert coverage["withheld"]
        assert coverage["known_limits"]
        # Every section the coverage claims to fill is a real key of the file.
        for section in coverage["included"]:
            assert section in payload

    def test_the_bot_side_memory_profile_is_in_the_file(self, bot_user):
        """`summary` is the largest thing we hold in free text, and it was missing."""
        from apps.identity.models import UserPersonalContext

        UserPersonalContext.objects.create(
            user_id=bot_user.ayla_user_id,
            summary="Ходит раз в три недели, любит тишину.",
            language_preferred="ru",
        )

        payload = export_personal_data(bot_user, client=_NoAyla())

        assert payload["personal_context"]["summary"] == "Ходит раз в три недели, любит тишину."
        assert payload["personal_context"]["language_preferred"] == "ru"
        assert payload["personal_context"]["minor_lock"] is False


class _NoAyla:
    """Upstream stub — an empty Ayla export, so the bot half is what is asserted."""

    def get_personal_data_export(self, *, ayla_user_id: str) -> dict:
        return {}

    def delete_personal_data(self, *, ayla_user_id: str) -> None:  # pragma: no cover
        return None

    def close(self) -> None:
        return None
