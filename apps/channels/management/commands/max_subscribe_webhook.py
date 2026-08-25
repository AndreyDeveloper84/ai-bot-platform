"""Register the MAX webhook subscription for the configured bot.

Replaces the ad-hoc curl recipe that operators were running by hand.
The previous workflow (`POST /subscriptions` with only `{url, secret}`)
omitted ``update_types`` — MAX then defaulted to delivering only a
subset of events, and ``message_callback`` updates never reached the
bot (2026-05-21 dev incident: welcome inline-keyboard taps appeared to
do nothing because the callback webhook was silently dropped).

This command always passes an explicit list of update types, defaulting
to the ones the platform actually handles today
(:data:`DEFAULT_UPDATE_TYPES`). Idempotent: DELETEs the existing
subscription for the URL first, then re-POSTs — MAX has no PUT.

### Usage

    python manage.py max_subscribe_webhook \
        --url https://api-dev.gobeauty.site/api/v1/ingress/max/ \
        --update-type message_created --update-type message_callback

The ``--url`` is required (no sensible default — dev and prod differ).
``--secret`` defaults to ``settings.MAX_WEBHOOK_SECRET``; if that is
empty the command refuses to subscribe rather than register an
unauthenticated webhook (MAX won't send ``X-Max-Bot-Api-Secret`` without
the field, and our ingress view rejects unauthenticated POSTs).

### ``--bot`` — which bot is being subscribed (DRF-1092)

A MAX subscription is scoped by the ``Authorization`` token, not by the
URL. Both bots post to the *same* ingress URL and are told apart by the
webhook secret (``apps.channels.bot_registry``), so the token is the
only thing deciding whose subscription this call creates.

Until this flag existed the token was always ``settings.MAX_BOT_TOKEN``
— the client bot. Running the command intending to subscribe the salon
bot therefore DELETEd and re-POSTed the **client** bot's subscription
and printed ``POST … → 200``. The salon bot stayed unsubscribed, MAX
delivered nothing to it, and the only evidence was a success line. The
operator cannot tell the two outcomes apart from the output — which is
the same shape as the 2026-05-21 incident above: the subscription call
succeeded and the bot went silent anyway.

    python manage.py max_subscribe_webhook --bot salon --url <ingress-url>

``--bot <slug>`` takes the token AND the secret from the registry entry
for that slug, so the pair cannot be mismatched by hand. Slugs come from
``MAX_BOTS``; with ``MAX_BOTS`` unset the only entry is the synthesized
legacy bot. Omitting ``--bot`` keeps the previous behaviour exactly.
"""

from __future__ import annotations

import logging

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.channels.bot_registry import BotEntry, effective_registry

logger = logging.getLogger(__name__)


# Only update types our parser handles. The MAX API offers more
# (``message_edited``, ``chat_title_changed``, etc.) but adding them
# here without parser support poisons the PEL — the worker raises
# ParseError, the consumer doesn't ACK, MAX retries forever, the bot
# goes silent until the entry is drained by hand (dev incident
# 2026-05-21). When a new parser branch lands, add the matching
# update_type here in the same PR.
#
# ``bot_started`` is the channel analog of Telegram's /start — fires
# when a new user first activates the bot. Parser synthesises
# ``text="/start"`` so welcome skill matches; first contact gets the
# inline-keyboard greeting without depending on whether the MAX client
# also auto-sends a /start text message.
DEFAULT_UPDATE_TYPES: tuple[str, ...] = (
    "message_created",
    "message_callback",
    "bot_started",
)


class Command(BaseCommand):
    help = "Register the MAX webhook subscription (DELETE + re-POST). Idempotent."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--url",
            required=True,
            help="Public HTTPS URL of the platform's MAX ingress endpoint.",
        )
        parser.add_argument(
            "--secret",
            default=None,
            help="Webhook secret. Defaults to settings.MAX_WEBHOOK_SECRET.",
        )
        parser.add_argument(
            "--bot",
            default="",
            help=(
                "Registry slug of the bot to subscribe (see MAX_BOTS). Takes the "
                "API token AND the webhook secret from that entry. Omit for the "
                "legacy single-bot behaviour (settings.MAX_BOT_TOKEN + "
                "settings.MAX_WEBHOOK_SECRET)."
            ),
        )
        parser.add_argument(
            "--update-type",
            action="append",
            dest="update_types",
            help=(
                "Repeatable. Update types to subscribe to. Defaults to: "
                + ", ".join(DEFAULT_UPDATE_TYPES)
            ),
        )

    def handle(self, *args, **options) -> None:
        url = options["url"]
        update_types = tuple(options["update_types"] or DEFAULT_UPDATE_TYPES)

        bot_slug = (options.get("bot") or "").strip()
        if bot_slug:
            entry = self._resolve_bot(bot_slug)
            token = entry.api_token
            # `--secret` still wins, but the registry value is the one ingress
            # will compare the incoming header against. Taking both from the
            # same entry is the point: bot A's token with bot B's secret
            # subscribes A and then 401s every update A delivers, which reads
            # as a network problem for days.
            secret = options["secret"] or entry.webhook_secret
            self.stdout.write(self.style.NOTICE(f"bot: {entry.slug} (stream {entry.stream})"))
        else:
            token = getattr(settings, "MAX_BOT_TOKEN", "")
            secret = options["secret"] or getattr(settings, "MAX_WEBHOOK_SECRET", "")

        if not token:
            raise CommandError(
                f"API token for bot {bot_slug!r} is empty — cannot subscribe."
                if bot_slug
                else "MAX_BOT_TOKEN is empty — cannot subscribe."
            )
        if not secret:
            raise CommandError(
                "Webhook secret is empty — refusing to register an unauthenticated "
                "webhook. Set MAX_WEBHOOK_SECRET or pass --secret."
            )

        api_base = getattr(settings, "MAX_API_BASE", "https://botapi.max.ru")
        headers = {"Authorization": token, "Content-Type": "application/json"}

        # 1. DELETE the existing subscription if any. 404 is fine — means
        # nothing was registered yet. Other non-2xx is a hard fail.
        delete_resp = httpx.delete(
            f"{api_base}/subscriptions",
            headers=headers,
            params={"url": url},
            timeout=10.0,
        )
        if delete_resp.status_code not in (200, 204, 404):
            raise CommandError(
                f"MAX DELETE /subscriptions failed: {delete_resp.status_code} {delete_resp.text[:200]}"
            )
        self.stdout.write(self.style.NOTICE(f"DELETE {url} → {delete_resp.status_code}"))

        # 2. POST the new subscription with explicit update_types.
        body = {
            "url": url,
            "secret": secret,
            "update_types": list(update_types),
        }
        post_resp = httpx.post(
            f"{api_base}/subscriptions",
            headers=headers,
            json=body,
            timeout=10.0,
        )
        if post_resp.status_code >= 400:
            raise CommandError(
                f"MAX POST /subscriptions failed: {post_resp.status_code} {post_resp.text[:200]}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"POST {url} → {post_resp.status_code} update_types={','.join(update_types)}"
            )
        )

    @staticmethod
    def _resolve_bot(slug: str) -> BotEntry:
        """Registry entry for ``slug``, or a CommandError naming the choices.

        Refusing here rather than falling back to ``MAX_BOT_TOKEN`` is
        deliberate. A typo in the slug would otherwise subscribe the *client*
        bot while the operator believes they subscribed the salon one — the
        exact failure this flag exists to remove, reintroduced by a forgiving
        default. The error lists the declared slugs; slugs are not secrets,
        the tokens they resolve to are, and neither those nor any prefix of
        them is ever printed.
        """

        registry = effective_registry()
        for entry in registry:
            if entry.slug == slug:
                return entry
        known = ", ".join(e.slug for e in registry) or "<registry is empty>"
        raise CommandError(
            f"unknown bot slug {slug!r}. Declared bots: {known}. Slugs come from "
            "MAX_BOTS; with MAX_BOTS unset the only entry is the synthesized "
            "legacy bot."
        )
