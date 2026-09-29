"""Where a bot's Mini App lives — one accessor for every button builder (DRF-1361).

The registry keeps ``web_app`` / ``miniapp_url`` PER BOT
(``MAX_BOT_<SLUG>_WEB_APP`` / ``_MINIAPP_URL``, :mod:`apps.channels.bot_registry`),
and a declared entry reads only its own prefix — the global
``MAX_BOT_WEB_APP`` / ``MAX_MINIAPP_URL`` are not a fallback for it
(``.env.staging.template``). The salon path honours that: it runs under
:func:`~apps.channels.bot_context.bot_scope` and ``staff_menu`` reads the
entry. The client path did not: ``handle_global_max_event`` runs with no bot
in scope, and the welcome keyboard, the marketplace menu and the visit
buttons read the GLOBAL settings — so the client bot's registry value was
invisible to the very buttons it was declared for, and «fill one variable»
could not fix it (owner decision 24.08, DRF-1361).

### What this module decides

:func:`miniapp_target` answers «which Mini App does the bot in this
conversation open?» in one place, so the ladder «``web_app`` → link →
nothing» that used to be written three times (welcome, marketplace,
visits) has one source:

1. the bot in scope (:func:`~apps.channels.bot_context.current_bot`) — the
   salon path, and any caller that entered ``bot_scope`` deliberately;
2. otherwise the registry entry serving the nationwide stream
   (``resolve_by_stream("max_global", effective_registry())``) — the client
   path, which never enters ``bot_scope``;
3. otherwise the global settings — the single-bot mode and the body of
   existing tests (``bot_registry.with_legacy_fallback``).

### What this module does NOT do

It does not enter ``bot_scope`` on the client path. ``current_bot()`` also
drives :func:`apps.channels.max.outbound._token`: putting the ``max_global``
entry in scope would make every client-bound send use that entry's
``api_token`` instead of ``settings.MAX_BOT_TOKEN`` — a change of radius
from «Mini App buttons» to «all client outbound», refused by the main
window on 19.09. This accessor touches ``web_app`` / ``miniapp_url`` only;
the token path is untouched, and a test pins that.

It sets no values: the client bot's Mini App handle at MAX is the owner's
open decision (DRF-1361 «Границы»).
"""

from __future__ import annotations

from typing import NamedTuple

GLOBAL_STREAM = "max_global"


class MiniAppTarget(NamedTuple):
    """``web_app`` opens the Mini App inside MAX; ``miniapp_url`` is the external-link fallback."""

    web_app: str
    miniapp_url: str
    #: Where the pair came from — for logs and tests, never for behaviour.
    source: str


def miniapp_target() -> MiniAppTarget:
    """The Mini App the bot of this conversation opens — entry first, settings last."""

    from django.conf import settings

    from apps.channels.bot_context import current_bot
    from apps.channels.bot_registry import effective_registry, resolve_by_stream

    entry = current_bot()
    source = "scope"
    if entry is None:
        entry = resolve_by_stream(GLOBAL_STREAM, effective_registry())
        source = "registry:max_global"
    if entry is not None:
        return MiniAppTarget(
            web_app=(getattr(entry, "web_app", "") or ""),
            miniapp_url=(getattr(entry, "miniapp_url", "") or ""),
            source=source,
        )
    return MiniAppTarget(
        web_app=(getattr(settings, "MAX_BOT_WEB_APP", "") or ""),
        miniapp_url=(getattr(settings, "MAX_MINIAPP_URL", "") or ""),
        source="settings",
    )


__all__ = ["GLOBAL_STREAM", "MiniAppTarget", "miniapp_target"]
