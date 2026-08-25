"""``manage.py max_subscribe_webhook`` — whose subscription is this? (DRF-1092)

### The defect these tests pin

A MAX subscription is scoped by the ``Authorization`` token. Both pilot
bots post to the **same** ingress URL and are told apart downstream by the
webhook secret, so the token is the only thing that decides which bot this
call subscribes.

The command read ``settings.MAX_BOT_TOKEN`` and nothing else. An operator
following DRF-1092 — "subscribe the salon bot, explicitly with
``message_created``, ``message_callback``, ``bot_started``" — would run it,
get ``POST … → 200 update_types=message_created,message_callback,bot_started``
on stdout, and have subscribed the **client** bot. The salon bot receives
nothing; the salon staff type an ``AYLA-XXXX`` code into it and it never
answers. Nothing in the output distinguishes that from success.

This is the same shape as the 2026-05-21 incident the command was written
to prevent: the subscription call returned 2xx and the bot was silent
anyway. That one was a missing ``update_types``; this one is the wrong
credential. Both are invisible at the call site, which is why they are
pinned here rather than left to a runbook step.
"""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.channels.bot_registry import BotEntry

URL = "https://api-dev.gobeauty.site/api/v1/ingress/max/"

CLIENT = BotEntry(
    slug="client",
    webhook_secret="client-secret",  # pragma: allowlist secret
    api_token="client-token",  # pragma: allowlist secret
    stream="max_global",
)
SALON = BotEntry(
    slug="salon",
    webhook_secret="salon-secret",  # pragma: allowlist secret
    api_token="salon-token",  # pragma: allowlist secret
    tenant_slug="formula-tela",
    stream="max_salon",
)


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = ""


@pytest.fixture
def captured(monkeypatch) -> dict[str, Any]:
    """Record what would go to MAX instead of sending it."""

    seen: dict[str, Any] = {}

    def fake_delete(url, **kwargs):
        seen["delete_headers"] = kwargs.get("headers", {})
        return _Resp(200)

    def fake_post(url, **kwargs):
        seen["post_headers"] = kwargs.get("headers", {})
        seen["body"] = kwargs.get("json", {})
        return _Resp(200)

    monkeypatch.setattr(
        "apps.channels.management.commands.max_subscribe_webhook.httpx.delete",
        fake_delete,
    )
    monkeypatch.setattr(
        "apps.channels.management.commands.max_subscribe_webhook.httpx.post",
        fake_post,
    )
    return seen


@pytest.fixture
def two_bots(monkeypatch):
    """A deployment that declared both bots, as the pilot must."""

    monkeypatch.setattr(
        "apps.channels.management.commands.max_subscribe_webhook.effective_registry",
        lambda: (CLIENT, SALON),
    )


class TestBotSelection:
    def test_salon_is_subscribed_with_the_salon_credentials(
        self, captured, two_bots, settings
    ) -> None:
        """The whole point: --bot salon must not send the client's token."""

        settings.MAX_BOT_TOKEN = CLIENT.api_token
        settings.MAX_WEBHOOK_SECRET = CLIENT.webhook_secret

        call_command("max_subscribe_webhook", "--bot", "salon", "--url", URL, stdout=StringIO())

        assert captured["post_headers"]["Authorization"] == SALON.api_token
        assert captured["body"]["secret"] == SALON.webhook_secret
        # And the DELETE half too — it is scoped by the same token, so a
        # client token here removes the CLIENT's subscription while claiming
        # to reset the salon's.
        assert captured["delete_headers"]["Authorization"] == SALON.api_token

    def test_token_and_secret_come_from_the_same_entry(self, captured, two_bots) -> None:
        """A token from one bot with a secret from another is unroutable.

        It subscribes the token's bot, and every update that bot then
        delivers carries a header the ingress gate does not recognise — a
        401 loop that looks like a MAX outage.
        """

        call_command("max_subscribe_webhook", "--bot", "client", "--url", URL, stdout=StringIO())

        assert captured["post_headers"]["Authorization"] == CLIENT.api_token
        assert captured["body"]["secret"] == CLIENT.webhook_secret

    def test_unknown_slug_is_refused_not_silently_defaulted(self, two_bots, settings) -> None:
        """A typo must not quietly become "the client bot"."""

        settings.MAX_BOT_TOKEN = CLIENT.api_token
        settings.MAX_WEBHOOK_SECRET = CLIENT.webhook_secret

        with pytest.raises(CommandError) as exc:
            call_command("max_subscribe_webhook", "--bot", "saloon", "--url", URL)

        message = str(exc.value)
        assert "saloon" in message
        # Names the choices, so the operator can fix it without reading code.
        assert "client" in message
        assert "salon" in message

    def test_no_secret_reaches_stdout(self, captured, two_bots) -> None:
        """Slugs are printable; the values they resolve to are not."""

        out = StringIO()
        call_command("max_subscribe_webhook", "--bot", "salon", "--url", URL, stdout=out)

        printed = out.getvalue()
        assert "salon" in printed
        assert SALON.api_token not in printed
        assert SALON.webhook_secret not in printed


class TestLegacyBehaviourUnchanged:
    """Omitting --bot must behave exactly as before this flag existed."""

    def test_settings_are_used_when_no_bot_is_named(self, captured, settings) -> None:
        settings.MAX_BOT_TOKEN = "legacy-token"  # pragma: allowlist secret
        settings.MAX_WEBHOOK_SECRET = "legacy-secret"  # pragma: allowlist secret

        call_command("max_subscribe_webhook", "--url", URL, stdout=StringIO())

        assert captured["post_headers"]["Authorization"] == "legacy-token"
        assert captured["body"]["secret"] == "legacy-secret"  # pragma: allowlist secret

    def test_empty_token_still_refuses(self, settings) -> None:
        settings.MAX_BOT_TOKEN = ""
        settings.MAX_WEBHOOK_SECRET = "legacy-secret"  # pragma: allowlist secret

        with pytest.raises(CommandError, match="MAX_BOT_TOKEN is empty"):
            call_command("max_subscribe_webhook", "--url", URL)

    def test_empty_secret_still_refuses(self, settings) -> None:
        """An unauthenticated webhook is worse than no webhook."""

        settings.MAX_BOT_TOKEN = "legacy-token"  # pragma: allowlist secret
        settings.MAX_WEBHOOK_SECRET = ""

        with pytest.raises(CommandError, match="refusing to register an unauthenticated"):
            call_command("max_subscribe_webhook", "--url", URL)


class TestUpdateTypes:
    """The 2026-05-21 incident: a subscription without callbacks is silent."""

    def test_default_covers_the_three_the_platform_parses(self, captured, settings) -> None:
        settings.MAX_BOT_TOKEN = "legacy-token"  # pragma: allowlist secret
        settings.MAX_WEBHOOK_SECRET = "legacy-secret"  # pragma: allowlist secret

        call_command("max_subscribe_webhook", "--url", URL, stdout=StringIO())

        assert captured["body"]["update_types"] == [
            "message_created",
            "message_callback",
            "bot_started",
        ]

    def test_explicit_flags_are_honoured(self, captured, two_bots) -> None:
        """DRF-1092 requires naming them; naming them must actually work."""

        call_command(
            "max_subscribe_webhook",
            "--bot",
            "salon",
            "--url",
            URL,
            "--update-type",
            "message_created",
            "--update-type",
            "message_callback",
            "--update-type",
            "bot_started",
            stdout=StringIO(),
        )

        assert captured["body"]["update_types"] == [
            "message_created",
            "message_callback",
            "bot_started",
        ]
