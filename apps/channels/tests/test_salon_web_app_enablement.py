"""Turning ``MAX_BOT_SALON_WEB_APP`` back on, end to end (DRF-1504).

Everything below already has unit tests. What none of them cover is the
*seam*: that the environment variable an operator types on the pilot is
the same string that ends up in ``BotEntry.web_app``, and that this one
value simultaneously

* makes ``invite_link`` exist at all
  (:func:`apps.admin_api.views_invite._bot_start_link`), and
* makes the salon bot build an ``open_app`` button
  (:func:`apps.channels.max.staff_menu.menu_buttons`) whose payload MAX
  will accept rather than answer ``400 proto.payload`` to.

Both halves are driven from one env dict here, because they are switched
by one variable and were, on 2026-08-30, broken by one variable. The
existing suites each hand-build a ``BotEntry(web_app=...)`` and therefore
stay green even if the ``_WEB_APP`` suffix stops being read — which would
leave the pilot exactly as dark as it is today with nothing failing.

### Why the colon is no longer a reason not to set it

The variable was rolled back on 2026-08-30 because the staff menu's
``open_app`` payload was then ``cb:staff:open_app`` and MAX rejects a
colon. ``0c3593a`` (#1337) replaced it with the flat
``staff_menu.OPEN_APP_PAYLOAD`` (``staff_open_app``) and added Guard 3 in
``apps/channels/max/outbound.py``, which raises at the producer instead
of letting a 400 take down the whole reply.
:class:`TestTheColonBlockerIsGone` is that claim as an executable
assertion rather than a note in a runbook.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
import uuid

import pytest

from apps.channels.bot_registry import (
    parse_registry,
    resolve_by_slug,
    resolve_by_tenant_stream,
)
from apps.channels.max.outbound import _button_to_max
from apps.channels.max.salon_handler import SALON_STREAM
from apps.channels.max.staff_menu import menu_buttons

#: What MAX accepts in an ``open_app`` payload, spelled out rather than
#: imported — measured against the live API on 2026-08-30, see
#: ``apps/channels/tests/test_staff_menu.py`` for the sweep. A test that
#: reads the rule out of the code it guards cannot catch that code being
#: wrong about the rule.
MAX_OPEN_APP_PAYLOAD_RE = re.compile(r"[A-Za-z0-9_-]{0,512}")

#: The pilot salon's public MAX handle, which is also its Mini App name.
#: Already in the repository — ``apps/admin_api/views_invite.py`` quotes
#: the link the owner opened on 30.08. A bot handle is public by
#: construction, unlike the API token beside it in the same env block.
PILOT_SALON_WEB_APP = "id583403546770_3_bot"

PILOT_TENANT = "formula-tela"

#: The pilot's MAX block as it must read once DRF-1504 is applied. The
#: secrets are placeholders; the *names* are the deliverable, and the
#: last line is the one that is commented out on the pilot today.
PILOT_ENV_WITH_WEB_APP = {
    "MAX_BOTS": "client,salon",
    "MAX_BOT_CLIENT_WEBHOOK_SECRET": "pilot-client-secret",  # pragma: allowlist secret
    "MAX_BOT_CLIENT_API_TOKEN": "pilot-client-token",  # pragma: allowlist secret
    "MAX_BOT_CLIENT_STREAM": "max_global",
    "MAX_BOT_SALON_WEBHOOK_SECRET": "pilot-salon-secret",  # pragma: allowlist secret
    "MAX_BOT_SALON_API_TOKEN": "pilot-salon-token",  # pragma: allowlist secret
    "MAX_BOT_SALON_TENANT_SLUG": PILOT_TENANT,
    "MAX_BOT_SALON_STREAM": "max_salon",
    "MAX_BOT_SALON_WEB_APP": PILOT_SALON_WEB_APP,
}

#: The pilot as it stands today — the same block with that one line
#: commented out. Every negative assertion below is driven from this, so
#: that "it works when set" and "it degrades honestly when unset" are
#: statements about the same configuration differing in one key.
PILOT_ENV_TODAY = {
    key: value for key, value in PILOT_ENV_WITH_WEB_APP.items() if key != "MAX_BOT_SALON_WEB_APP"
}


class _Role:
    """The smallest role context ``menu_buttons`` reads."""

    def __init__(self, *, admin: bool = False, master: bool = False):
        self.is_owner = admin
        self.is_admin = False
        self.is_receptionist = False
        self.is_master = master


def _salon_entry(env: dict[str, str]):
    return resolve_by_slug("salon", parse_registry(env))


def _app_buttons(entry):
    return [b for b in menu_buttons(_Role(admin=True), entry) if b.get("web_app")]


def _start_link(env: dict[str, str], settings) -> str:
    """``invite_link`` as the admin API would build it under ``env``."""

    from apps.admin_api.views_invite import _bot_start_link

    settings.MAX_BOT_REGISTRY = parse_registry(env)
    return _bot_start_link(SimpleNamespace(slug=PILOT_TENANT), uuid.uuid4())


class TestTheVariableIsTheOneToSet:
    """``MAX_BOT_SALON_WEB_APP`` — the exact name, on the salon entry."""

    def test_the_env_suffix_lands_in_the_registry_entry(self):
        entry = _salon_entry(PILOT_ENV_WITH_WEB_APP)

        assert entry is not None
        assert entry.web_app == PILOT_SALON_WEB_APP

    def test_the_salon_entry_is_the_one_the_invite_path_resolves(self):
        """Both coordinates matter — tenant alone would find the client bot.

        ``_bot_start_link`` looks the entry up by ``(tenant, stream)``, so
        ``MAX_BOT_SALON_TENANT_SLUG`` and ``MAX_BOT_SALON_STREAM`` are
        prerequisites of this variable, not decoration around it.
        """

        registry = parse_registry(PILOT_ENV_WITH_WEB_APP)
        entry = resolve_by_tenant_stream(PILOT_TENANT, SALON_STREAM, registry)

        assert entry is not None
        assert entry.slug == "salon"
        assert entry.web_app == PILOT_SALON_WEB_APP

    def test_the_global_web_app_is_not_a_substitute(self):
        """``MAX_BOT_WEB_APP`` names the *client* bot's Mini App.

        Setting the global instead would send staff into the customer app
        and build a start link naming the wrong bot — whose
        ``bot_started`` lands on ``ingress:max``, where nothing reads
        invitations. Declared entries read only their own prefix, and
        this pins that: the global leaves the salon entry mute.
        """

        entry = _salon_entry({**PILOT_ENV_TODAY, "MAX_BOT_WEB_APP": PILOT_SALON_WEB_APP})

        assert entry is not None
        assert entry.web_app == ""


class TestSettingItLightsUpBothHalves:
    def test_the_invite_link_becomes_a_real_link(self, settings):
        from apps.admin_api.views_invite import MASTER_INVITE_PAYLOAD_PREFIX

        link = _start_link(PILOT_ENV_WITH_WEB_APP, settings)

        assert link.startswith(f"https://max.ru/{PILOT_SALON_WEB_APP}?start=")
        assert f"?start={MASTER_INVITE_PAYLOAD_PREFIX}" in link

    def test_the_staff_menu_gains_a_working_open_app_button(self):
        entry = _salon_entry(PILOT_ENV_WITH_WEB_APP)

        buttons = _app_buttons(entry)

        assert len(buttons) == 1, buttons
        wire = _button_to_max(buttons[0])
        assert wire["type"] == "open_app"
        assert wire["web_app"] == PILOT_SALON_WEB_APP
        assert MAX_OPEN_APP_PAYLOAD_RE.fullmatch(wire["payload"]), wire


class TestWithoutItNothingBreaks:
    """The pilot's state today: honest refusal, not a crash and not a lie."""

    def test_the_invite_link_is_empty(self, settings):
        assert _start_link(PILOT_ENV_TODAY, settings) == ""

    def test_the_menu_omits_the_button_instead_of_building_a_dead_one(self):
        entry = _salon_entry(PILOT_ENV_TODAY)

        assert entry is not None, "the salon bot itself is still declared"
        assert _app_buttons(entry) == []

    def test_the_rest_of_the_menu_still_answers(self):
        """The 30.08 outage was the whole reply dying, not one button.

        Whatever this variable does, the day and requests buttons must
        survive it — which is the half that was actually lost that day.
        """

        entry = _salon_entry(PILOT_ENV_TODAY)

        labels = [b["label"] for b in menu_buttons(_Role(admin=True), entry)]

        assert any("Сегодня" in x for x in labels)
        assert any("Заявки" in x for x in labels)


class TestTheColonBlockerIsGone:
    """Why the 30.08 rollback reason no longer applies."""

    def test_the_button_max_rejected_is_not_the_button_built_today(self):
        """The payload that took the salon bot down was ``cb:staff:open_app``."""

        entry = _salon_entry(PILOT_ENV_WITH_WEB_APP)

        payload = _button_to_max(_app_buttons(entry)[0])["payload"]

        assert ":" not in payload, payload
        assert payload != "cb:staff:open_app"

    def test_a_colon_payload_would_still_be_refused_at_the_producer(self):
        """Guard 3 is what makes re-enabling safe rather than merely lucky.

        Paired with the test above on purpose: "today's payload has no
        colon" is a fact about one string, while this is the property
        that keeps the next one honest. A colon now costs a ``ValueError``
        in a test run, not a silent salon for its masters.
        """

        with pytest.raises(ValueError, match="flat slug"):
            _button_to_max(
                {
                    "label": "🏠 Кабинет салона",
                    "callback": "cb:staff:open_app",
                    "web_app": PILOT_SALON_WEB_APP,
                }
            )
