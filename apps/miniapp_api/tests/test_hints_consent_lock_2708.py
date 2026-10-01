"""DRF-2708 / owner decision §47.3 — the hints toggle cannot lie after a revocation.

«После отзыва согласия тумблер не должен позволять создать ложное состояние
„подсказки включены“.» Until now the revocation was a reset, not a lock: the
column went off, and the very next tap put it back on — the screen said
«включено» and promised to write first while every message stayed blocked by
the withdrawn consent.

Driven through the real Mini App endpoints. The live-consent case is the
control: without it «the toggle did not turn on» would also be true of a
toggle that never works.

Not here, on purpose: the explanation the person reads instead of the switch
(§47.3, second paragraph) and anything on the screen.
"""

# ruff: noqa: F811 — fixtures are imported from the consents suite and requested by name

from __future__ import annotations

import json

import pytest

from apps.audit.models import AuditLog
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_customer_consents import (  # noqa: F401
    _bot_token,
    _no_ayla_link,
    _revoke,
    auth,
    bot_user,
    hints_url,
    revoke_url,
    tenant,
    url,
)
from apps.notifications.proactive import consent_blocker

pytestmark = pytest.mark.django_db


def _set(client, hints_url, auth, enabled: bool):
    return client.post(
        hints_url, data=json.dumps({"enabled": enabled}), content_type="application/json", **auth
    )


def _hints(client, url, auth) -> dict:
    return client.get(url, **auth).json()["proactive_hints"]


def _row(bot_user) -> BotUser:
    return BotUser.all_tenants.get(pk=bot_user.pk)


def _enable_audits() -> int:
    return sum(
        1
        for row in AuditLog.all_tenants.filter(action="consent.proactive_hints_changed")
        if row.payload.get("enabled") is True
    )


class TestRevokedConsentLocksTheToggle:
    def test_turning_on_is_refused_and_nothing_changes(
        self,
        client,
        bot_user,
        url,
        hints_url,
        revoke_url,
        auth,
    ) -> None:
        assert _revoke(client, revoke_url, auth).status_code == 200
        audits_before = _enable_audits()

        res = _set(client, hints_url, auth, True)

        assert res.status_code == 409
        assert res.json()["error"] == "consent_withdrawn"
        assert _hints(client, url, auth)["enabled"] is False
        assert _row(bot_user).proactive_messages_opt_out is True
        assert _enable_audits() == audits_before  # no «enabled» row was written

    def test_turning_off_is_never_refused(
        self,
        client,
        bot_user,
        url,
        hints_url,
        revoke_url,
        auth,
    ) -> None:
        _revoke(client, revoke_url, auth)

        res = _set(client, hints_url, auth, False)

        assert res.status_code == 200
        assert res.json()["proactive_hints"]["enabled"] is False

    def test_the_document_says_it_cannot_be_enabled_and_why(
        self,
        client,
        bot_user,
        url,
        revoke_url,
        auth,
    ) -> None:
        before = _hints(client, url, auth)
        assert (before["can_enable"], before["blocked_reason"]) == (True, "")

        _revoke(client, revoke_url, auth)

        after = _hints(client, url, auth)
        assert (after["enabled"], after["can_enable"], after["blocked_reason"]) == (
            False,
            False,
            "consent_withdrawn",
        )

    def test_the_block_is_named_a_withdrawal_not_an_opt_out(
        self,
        client,
        bot_user,
        revoke_url,
        auth,
    ) -> None:
        """The same legal fact through the door people actually use."""
        _revoke(client, revoke_url, auth)

        assert _row(bot_user).proactive_messages_opt_out is True
        assert consent_blocker(_row(bot_user)) == "consent_withdrawn"


class TestALiveConsentLeavesTheToggleAlone:
    def test_the_toggle_turns_off_and_back_on(
        self,
        client,
        bot_user,
        url,
        hints_url,
        auth,
    ) -> None:
        assert _set(client, hints_url, auth, False).json()["proactive_hints"]["enabled"] is False

        res = _set(client, hints_url, auth, True)

        assert res.status_code == 200
        assert res.json()["proactive_hints"]["enabled"] is True
        assert _row(bot_user).proactive_messages_opt_out is False

    def test_a_new_consent_returns_the_switch_not_the_setting(
        self,
        client,
        bot_user,
        url,
        hints_url,
        revoke_url,
        auth,
    ) -> None:
        """«При повторной выдаче согласия подсказки не включать автоматически» —
        the person turned them off by revoking; what comes back is the ability
        to turn them on."""
        _revoke(client, revoke_url, auth)
        record_global_consent(_row(bot_user), source="test:regrant")

        state = _hints(client, url, auth)
        assert (state["enabled"], state["can_enable"]) == (False, True)

        res = _set(client, hints_url, auth, True)
        assert res.status_code == 200
        assert res.json()["proactive_hints"]["enabled"] is True
