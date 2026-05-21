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
"""

from __future__ import annotations

import logging

import httpx
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)


# Only update types our parser handles. The MAX API offers more
# (``bot_started`` lifecycle, ``message_edited``, ``chat_title_changed``,
# etc.) but adding them here without parser support poisons the PEL —
# the worker raises ParseError, the consumer doesn't ACK, MAX retries
# forever, the bot goes silent until the entry is drained by hand
# (dev incident 2026-05-21). When a new parser branch lands, add the
# matching update_type here in the same PR.
DEFAULT_UPDATE_TYPES: tuple[str, ...] = (
    "message_created",
    "message_callback",
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
        secret = options["secret"] or getattr(settings, "MAX_WEBHOOK_SECRET", "")
        update_types = tuple(options["update_types"] or DEFAULT_UPDATE_TYPES)

        token = getattr(settings, "MAX_BOT_TOKEN", "")
        if not token:
            raise CommandError("MAX_BOT_TOKEN is empty — cannot subscribe.")
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
