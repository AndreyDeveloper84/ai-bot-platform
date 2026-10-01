"""DRF-2697 — the prompt paragraph of persistent memory asks consent itself.

STORE != PERMISSION TO USE. ``render_current_personal_context`` used to take a
bare user id: the consent gate lived in the one caller, 275 lines above the
call, and a second caller with ``bot_user.ayla_user_id`` in hand would have got
the paragraph without anyone asking. The reader now takes the person and asks
``can_store_green_memory`` at the moment of use.

One synthetic fact sits in storage in every case; only the consent state
differs. The valid case is the positive control — without it «the paragraph is
absent» would also be true of a reader that renders nothing at all.

There is no EXPIRED case: ``ConsentRecord`` carries no expiry. A live consent is
``granted AND withdrawn_at IS NULL``, and that is the whole state space here.
"""

from __future__ import annotations

import inspect
import uuid

import pytest

from apps.consent.services import record_global_consent, withdraw_personal_data_for_bot_users
from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.persona.memory_surface import render_current_personal_context

pytestmark = pytest.mark.django_db

FACT_WORD = "веган"


@pytest.fixture(autouse=True)
def _strict_scope(settings):
    settings.STRICT_TENANT_SCOPE = "strict"


def _person_with_the_fact(channel_user_id: str, *, linked: bool = True):
    ayla_uid = uuid.uuid4()
    bot_user = resolve_or_create_global_bot_user(
        channel="max",
        channel_user_id=channel_user_id,
        ayla_user_id=ayla_uid if linked else None,
    )
    upc = UserPersonalContext.objects.create(user_id=ayla_uid)
    MemoryEntry.objects.create(
        user_id=ayla_uid,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )
    return bot_user, ayla_uid


def _fact_is_stored(ayla_uid: uuid.UUID) -> bool:
    return MemoryEntry.objects.filter(
        user_id=ayla_uid,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        soft_deleted_at__isnull=True,
    ).exists()


class TestTheSameFactUnderDifferentConsent:
    def test_a_valid_consent_renders_the_fact(self):
        bot_user, _ = _person_with_the_fact("2697-valid")
        record_global_consent(bot_user, source="welcome")

        paragraph = render_current_personal_context(bot_user)

        assert paragraph is not None
        assert FACT_WORD in paragraph.lower()

    def test_an_absent_consent_renders_nothing(self):
        bot_user, ayla_uid = _person_with_the_fact("2697-absent")

        assert render_current_personal_context(bot_user) is None
        assert _fact_is_stored(ayla_uid)

    def test_a_revoked_consent_renders_nothing_while_the_fact_stays_in_storage(self):
        bot_user, ayla_uid = _person_with_the_fact("2697-revoked")
        record_global_consent(bot_user, source="welcome")
        assert FACT_WORD in (render_current_personal_context(bot_user) or "").lower()

        assert withdraw_personal_data_for_bot_users([bot_user], source="test") >= 1

        assert render_current_personal_context(bot_user) is None
        assert _fact_is_stored(ayla_uid)  # retention is not this gate's business

    def test_a_consent_that_could_not_be_checked_renders_nothing(self, monkeypatch):
        bot_user, ayla_uid = _person_with_the_fact("2697-unknown")
        record_global_consent(bot_user, source="welcome")

        def _unavailable(*args, **kwargs):
            raise RuntimeError("consent registry unavailable")

        monkeypatch.setattr("apps.consent.memory.has_global_consent", _unavailable)

        assert render_current_personal_context(bot_user) is None  # and does not raise
        assert _fact_is_stored(ayla_uid)


class TestTheGateCannotBeWalkedAround:
    def test_a_bare_user_id_gets_nothing_even_when_the_person_consented(self):
        """The old calling convention. A caller that has only the id — which is
        exactly what a caller skipping the gate has — is answered with nothing."""
        bot_user, ayla_uid = _person_with_the_fact("2697-bare-id")
        record_global_consent(bot_user, source="welcome")
        assert render_current_personal_context(bot_user) is not None  # the control

        assert render_current_personal_context(ayla_uid) is None  # type: ignore[arg-type]

    def test_a_consented_person_without_an_ayla_subject_gets_nothing(self):
        bot_user, _ = _person_with_the_fact("2697-unlinked", linked=False)
        record_global_consent(bot_user, source="welcome")

        assert render_current_personal_context(bot_user) is None

    def test_every_prompt_reader_of_memory_takes_the_person_not_an_id(self):
        """Readers that put persistent memory into the prompt decide consent
        themselves, so each is handed the person. A reader growing a ``user_id``
        first parameter again is a reader someone else has to gate."""
        from apps.orchestrator.memory_block import build_concierge_memory_block
        from apps.orchestrator.said_memory import render_said_block, said_facts

        for reader in (
            render_current_personal_context,
            build_concierge_memory_block,
            render_said_block,
            said_facts,
        ):
            first = next(iter(inspect.signature(reader).parameters))
            assert first == "bot_user", f"{reader.__name__}({first}, …)"
