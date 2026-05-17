"""Telegram outbound proxy helper (Phase 1 / CH1, DRF-848).

api.telegram.org is blocked from Russian IPs (Roskomnadzor). Every
outbound HTTPS call from the Telegram channel adapter MUST be routed
through a proxy or the delivery fails silently.

The helper reads two settings in priority order:

1. ``TELEGRAM_PROXY`` — Telegram-specific override. Use this when the
   alerting module's existing OpenAI proxy isn't suitable (different
   provider, different egress IPs, etc.).
2. ``OPENAI_PROXY`` — fallback. The same proxy that unblocks OpenAI /
   Anthropic API calls almost always works for Telegram too, so most
   deployments will only configure one of the two env vars.

Returns the dict shape ``requests`` / ``httpx`` expect for the
``proxies=`` kwarg (``{"https": url, "http": url}``) or ``None`` when
neither setting is configured (local dev, tests, dormant tenant).
Local dev MUST keep working without a proxy; CI tests assert this.

Single source of truth — :mod:`apps.channels.telegram.outbound` is
the only consumer in this package; the alerting module's
``_telegram_proxies`` is duplicated by design (alerting cannot import
from a channels package because the channels package may import audit /
events which page on errors, creating an import cycle).
"""

from __future__ import annotations

from django.conf import settings


def get_proxies() -> dict[str, str] | None:
    """Pick the active outbound proxy URL for Telegram traffic.

    Reads ``TELEGRAM_PROXY`` (priority) then ``OPENAI_PROXY`` (fallback)
    from Django settings. Returns the ``requests``-compatible proxies
    dict or ``None`` when both are empty.

    Returns:
      ``{"https": url, "http": url}`` when a proxy URL is configured;
      ``None`` otherwise. Pass the return value verbatim to
      ``requests.post(..., proxies=get_proxies())``.

    Why both ``https`` AND ``http``:
      Some proxy URLs negotiate the scheme themselves (CONNECT tunnel
      for HTTPS); ``requests`` matches the dict key against the
      *destination* URL scheme, so we set both to handle any future
      ``http://api.telegram.org`` fallback (Telegram redirects http→https
      but the request library still does the scheme lookup before the
      redirect).
    """
    proxy = getattr(settings, "TELEGRAM_PROXY", "") or getattr(settings, "OPENAI_PROXY", "") or ""
    proxy = str(proxy).strip()
    if not proxy:
        return None
    return {"https": proxy, "http": proxy}
