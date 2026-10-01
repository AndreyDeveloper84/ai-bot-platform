"""DRF-2708 / owner decision §47.3 — a withdrawn consent names the block.

«В audit причиной блокировки остаётся отзыв согласия. Технический ``opt_out``
может быть дополнительным эффектом, но не должен подменять юридическое
основание отзыва.»

Since §35 п.9 revoking the data-storage consent from the Mini App also sets
``proactive_messages_opt_out``. The shared gate asked the preference first and
returned its slug, so on the door people actually use the withdrawal of a
152-ФЗ consent was reported as ``opt_out``.

What is pinned here is the NAME, and only against a withdrawal: the opt-out
veto itself stays unconditional — an opted-out person is blocked in every row
below — and against every other condition it still names the block first, as
the neighbouring suites assert.
"""

from __future__ import annotations

import pytest

from apps.consent.models import ConsentRecord
from apps.consent.services import withdraw
from apps.identity.models import BotUser
from apps.notifications.proactive import BLOCK_REASONS, blocker_verdict, consent_blocker
from apps.notifications.tests.test_proactive_consent_blocker import (  # noqa: F401
    PERSONAL_DATA,
    grant,
    make_user,
    tenant,
)
from apps.tenancy.context import tenant_scope

pytestmark = pytest.mark.django_db


def _opt_out(user: BotUser) -> BotUser:
    BotUser.all_tenants.filter(pk=user.pk).update(proactive_messages_opt_out=True)
    user.refresh_from_db()
    return user


def _withdraw(user: BotUser) -> None:
    with tenant_scope(user.tenant):
        assert withdraw(user, consent_type=PERSONAL_DATA, source="test:2708") is not None


class TestTheNameOfTheBlock:
    def test_withdrawn_and_opted_out_is_called_a_withdrawal(self, tenant) -> None:  # noqa: F811
        user = make_user(tenant, suffix="w-o")
        grant(user, PERSONAL_DATA)
        _withdraw(user)
        _opt_out(user)

        assert consent_blocker(user) == "consent_withdrawn"
        verdict = blocker_verdict(user)
        assert verdict.reason == "consent_withdrawn"
        assert verdict.also == ("opt_out",)  # demoted, not dropped

    def test_opted_out_with_a_live_consent_is_called_an_opt_out(self, tenant) -> None:  # noqa: F811
        """The control: the preference still names the block when it is the
        only thing that blocks."""
        user = make_user(tenant, suffix="o")
        grant(user, PERSONAL_DATA)
        _opt_out(user)

        verdict = blocker_verdict(user)
        assert verdict.reason == "opt_out"
        assert verdict.also == ()

    def test_withdrawn_without_the_opt_out_is_unchanged(self, tenant) -> None:  # noqa: F811
        user = make_user(tenant, suffix="w")
        grant(user, PERSONAL_DATA)
        _withdraw(user)

        verdict = blocker_verdict(user)
        assert verdict.reason == "consent_withdrawn"
        assert verdict.also == ()

    def test_a_person_who_may_be_written_to_gets_no_reason(self, tenant) -> None:  # noqa: F811
        user = make_user(tenant, suffix="ok")
        grant(user, PERSONAL_DATA)

        assert consent_blocker(user) is None
        assert blocker_verdict(user).reason is None

    def test_the_slug_is_the_existing_one(self) -> None:
        """One legal fact, one name: §47.3 says «consent_revoked», the code has
        called it ``consent_withdrawn`` since DRF-1301."""
        assert "consent_withdrawn" in BLOCK_REASONS
        assert "consent_revoked" not in BLOCK_REASONS


class TestTheVetoIsUntouched:
    def test_an_opted_out_person_is_blocked_whatever_else_is_true(self, tenant) -> None:  # noqa: F811
        live = _opt_out(make_user(tenant, suffix="v-live"))
        grant(live, PERSONAL_DATA)

        gone = make_user(tenant, suffix="v-gone")
        grant(gone, PERSONAL_DATA)
        _withdraw(gone)
        _opt_out(gone)

        never = _opt_out(make_user(tenant, suffix="v-never"))  # no ConsentRecord at all

        for user in (live, gone, never):
            assert consent_blocker(user) is not None, user.channel_user_id

    def test_against_anything_but_a_withdrawal_the_opt_out_still_names_it(self, tenant) -> None:  # noqa: F811
        """Narrow on purpose: §47.3 speaks of a withdrawal. A person who never
        consented and opted out is still reported as ``opt_out``."""
        never = _opt_out(make_user(tenant, suffix="n"))
        assert ConsentRecord.all_tenants.filter(bot_user=never).count() == 0

        assert consent_blocker(never) == "opt_out"
