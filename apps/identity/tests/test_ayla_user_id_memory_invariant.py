"""Regression-guard: «one ayla_user_id per MAX user» at the MEMORY boundary (M-B1 / #1098).

Identity resolution itself is already locked in ``test_global_bot_resolver.py``
(idempotent resolve; ``ayla_user_id`` blank-filled once then never overwritten).
The resolution is populated in production by the eventbus identity/booking
consumers (NOT touched here — eventbus is out of this stream's scope).

This guard locks the *consequence* that the Memory Foundation (Option B) depends
on: the canonical ``ayla_user_id`` is the STABLE key of a user's memory, so a
MAX user maps to exactly ONE ``UserPersonalContext`` across turns, and the same
person seen on another channel (same ``ayla_user_id``) shares that one memory
identity. If a refactor ever regressed identity→memory keying, these fail.
"""

from __future__ import annotations

import uuid

import pytest

from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_reader import read_personal_context
from apps.orchestrator.memory.personal_context import record_explicit_green_facts

pytestmark = pytest.mark.django_db(transaction=True)


def _max_user(channel_user_id: str, ayla_user_id: uuid.UUID | None, settings) -> BotUser:
    settings.STRICT_TENANT_SCOPE = "strict"
    bu = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=channel_user_id, ayla_user_id=ayla_user_id
    )
    record_global_consent(bu, source="welcome")  # PERSONAL_DATA — green write basis
    return bu


class TestOneAylaUserIdOneMemoryIdentity:
    def test_same_max_user_accretes_into_one_upc(self, settings):
        uid = uuid.uuid4()
        bu = _max_user("mb1-a", uid, settings)

        # Two turns, two distinct explicit facts.
        record_explicit_green_facts(bu, "я веган")
        record_explicit_green_facts(bu, "я вегетарианка")  # (superset cue; distinct value)

        # One UPC for this user; both facts under the SAME ayla_user_id key.
        assert UserPersonalContext.objects.filter(user_id=uid).count() == 1
        view = read_personal_context(uid)
        values = {f.content.get("value") for f in view.green_facts}
        assert {"vegan", "vegetarian"} <= values

    def test_memory_key_is_ayla_user_id_not_botuser_id(self, settings):
        uid = uuid.uuid4()
        bu = _max_user("mb1-b", uid, settings)
        record_explicit_green_facts(bu, "я веган")

        # The memory row is keyed on the canonical Ayla id, not the BotUser PK —
        # this is what makes memory cross-channel.
        entry = MemoryEntry.objects.get(user_id=uid)
        assert entry.user_id == bu.ayla_user_id
        assert entry.user_id != bu.id

    def test_distinct_max_users_have_isolated_memory(self, settings):
        uid_a, uid_b = uuid.uuid4(), uuid.uuid4()
        bu_a = _max_user("mb1-c1", uid_a, settings)
        _max_user("mb1-c2", uid_b, settings)  # user B, no facts

        record_explicit_green_facts(bu_a, "я веган")

        assert read_personal_context(uid_a).green_facts  # A remembers
        assert read_personal_context(uid_b).is_empty()  # B unaffected

    def test_same_ayla_user_id_across_channels_shares_memory(self, settings):
        # The same person on MAX and (future) Telegram = two BotUsers, ONE
        # ayla_user_id → ONE memory identity.
        uid = uuid.uuid4()
        max_user = _max_user("mb1-d", uid, settings)
        settings.STRICT_TENANT_SCOPE = "strict"
        tg_user = resolve_or_create_global_bot_user(
            channel="telegram", channel_user_id="mb1-d", ayla_user_id=uid
        )
        assert max_user.id != tg_user.id  # distinct BotUser rows
        assert max_user.ayla_user_id == tg_user.ayla_user_id == uid  # one canonical id

        # A fact learned on MAX is visible against the shared ayla_user_id.
        record_explicit_green_facts(max_user, "я веган")
        view = read_personal_context(tg_user.ayla_user_id)
        assert any(f.content.get("value") == "vegan" for f in view.green_facts)
        assert UserPersonalContext.objects.filter(user_id=uid).count() == 1
