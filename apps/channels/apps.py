from __future__ import annotations

from django.apps import AppConfig


class ChannelsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.channels"

    def ready(self) -> None:
        # Import handlers module to register all channels with the
        # workers registry — `@register("ingress:<channel>")` runs
        # on import. Sprint 2 ships only `max`; Sprint 3+ siblings
        # land alongside their channel adapter modules.
        from apps.channels import handlers  # noqa: F401 — registration side effect

        # Sprint 8 review P1-cycle2: register the MAX outbound sender
        # with the orchestrator channel registry. Pipeline._send_outbound
        # used to lazy-import apps.channels.max.outbound directly —
        # feature↔feature cycle. Now the channel adapter pushes; the
        # pipeline reads.
        from apps.orchestrator.channel_registry import (
            ChannelSendError,
            register_channel,
        )

        def _max_sender(chat_id: str, text: str) -> None:
            """Adapter: orchestrator's ChannelSender contract → MAX outbound.

            Wraps :class:`MaxAPIError` (retriable transport failure) into
            :class:`ChannelSendError` so the pipeline's retry/DLQ logic
            stays channel-agnostic. Other exception types propagate
            unchanged — they're non-retriable for the pipeline either way.

            Lazy import of ``send_message`` (rather than capturing a
            closure at ready() time) preserves existing test patch
            sites: tests that ``patch("apps.channels.max.outbound.send_message")``
            keep working — the adapter re-resolves the symbol on each
            call.
            """
            from apps.channels.max.outbound import MaxAPIError, send_message

            try:
                send_message(chat_id=chat_id, text=text)
            except MaxAPIError as exc:
                raise ChannelSendError(str(exc)) from exc

        register_channel("max", _max_sender)
