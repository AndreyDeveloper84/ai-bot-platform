"""Telegram REST outbound — direct HTTPS via ``requests`` (Phase 1 / CH1).

Mirrors :mod:`apps.channels.max.outbound`'s shape. No ``python-telegram-bot``
SDK dependency — direct calls to ``https://api.telegram.org/bot<token>/<method>``
match the legacy ``mysite/agents/telegram.py`` pattern and keep the
dependency footprint minimal (``requests`` is already in the lockfile).

### Critical security + ops rules

1. **Proxy mandatory in RU.** ``api.telegram.org`` is blocked from
   Russian IPs (Roskomnadzor). The proxy URL comes from
   :func:`apps.channels.telegram.proxy.get_proxies` — ``TELEGRAM_PROXY``
   first, ``OPENAI_PROXY`` as fallback. Missing both in production →
   silent 100% delivery failure.
2. **Per-tenant bot token.** Every send takes a ``tenant`` argument and
   reads ``tenant.telegram_bot_token`` for the URL. No global
   ``TELEGRAM_BOT_TOKEN`` lookup — that env var belongs to the global
   alerting channel (operator pages), NOT customer-facing traffic.
3. **Token never logged.** Error paths log ``tenant.id`` + the method
   name; the token is never serialised to logs / events / audit.
4. **Return ``r.ok``, don't unconditionally ``return True``.** The
   legacy ``mysite/agents/telegram.py`` had a bug where the helper
   returned ``True`` even for 5xx; D3 echo tests + the alerting unit
   tests caught it in dev. We mirror :func:`requests.Response.ok`
   semantics so callers (the handler, future skill outbound paths)
   know whether the message actually shipped.

### Error contract

``send_message`` / ``send_photo`` / ``answer_callback_query`` return a
boolean — ``True`` on 2xx, ``False`` on every error path (4xx, 5xx,
network failure, missing config). All failure paths log via
``logger.warning`` / ``logger.exception`` with ``tenant.id`` + method
name; the bot token is never in the log line.

The handler treats ``False`` as a transient failure: the assistant
message has already been persisted (per the pre-send write contract
inherited from :mod:`apps.channels.max.handler`), so a retry on the
next webhook tick can re-attempt without losing the reply. We do NOT
raise on failure — Telegram doesn't retry on 5xx from us either, and
crashing the webhook view leaves Telegram trying again indefinitely.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import requests  # type: ignore[import-untyped]

from apps.channels.telegram.proxy import get_proxies

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


_API_BASE = "https://api.telegram.org"
_DEFAULT_TIMEOUT = 10.0


class TelegramAPIError(Exception):
    """Telegram REST API returned a non-2xx OR the network call failed.

    The handler doesn't raise on this — the outbound helpers return
    ``False`` instead — but the exception class exists so future code
    paths that want fail-fast semantics (e.g. setup smoke tests) can
    opt in by re-raising.
    """


def _post(
    method: str,
    *,
    tenant: "Tenant",
    body: dict[str, Any],
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """Internal POST helper — single funnel for every Telegram call.

    Centralises:
      * Per-tenant token lookup (no global fallback).
      * Proxy resolution via :func:`get_proxies`.
      * Token redaction in logs.
      * ``return r.ok`` (NOT ``return True``).
      * Network-error → ``False`` with ``logger.exception``.

    Args:
      method: Telegram API method name (``sendMessage``, ``sendPhoto``,
              ``answerCallbackQuery``).
      tenant: tenant row whose ``telegram_bot_token`` authenticates the
              call. Empty token → ``False`` + warn (caller should never
              reach here, but defense in depth).
      body: JSON-serialisable body. Telegram parses ``application/json``
            for every method we use.
      timeout: request timeout in seconds. 10s is generous — Telegram's
               edge typically responds <500ms; the spare headroom
               absorbs proxy latency.

    Returns:
      ``True`` only when ``response.ok`` is True. ``False`` on missing
      token, on 4xx/5xx, and on network errors.
    """
    token = (tenant.telegram_bot_token or "").strip()
    if not token:
        logger.warning(
            "channels.telegram.outbound.no_token tenant=%s method=%s",
            tenant.id,
            method,
        )
        return False

    url = f"{_API_BASE}/bot{token}/{method}"
    proxies = get_proxies()

    try:
        response = requests.post(
            url,
            json=body,
            timeout=timeout,
            proxies=proxies,
        )
    except requests.RequestException:
        # ConnectionError, Timeout, ProxyError, etc. logger.exception
        # captures the traceback for Sentry; the token never appears
        # in this log line.
        logger.exception(
            "channels.telegram.outbound.network_error tenant=%s method=%s",
            tenant.id,
            method,
        )
        return False

    if not response.ok:
        # Telegram returns ``{"ok": false, "error_code": ..., "description": ...}``
        # bodies; the description is safe to log (no token in it).
        body_preview = ""
        try:
            body_preview = response.text[:200]
        except Exception:  # noqa: BLE001 — best-effort log preview
            body_preview = "<unreadable>"
        logger.warning(
            "channels.telegram.outbound.http_error tenant=%s method=%s status=%s body=%r",
            tenant.id,
            method,
            response.status_code,
            body_preview,
        )
        return False

    return True


def send_message(
    *,
    chat_id: str,
    text: str,
    tenant: "Tenant",
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """POST ``sendMessage`` for the given tenant's Telegram bot.

    Args:
      chat_id: Telegram chat ID (string; large signed ints stringify
               cleanly). Comes from :attr:`CanonicalEvent.chat_id`.
      text: message body, 4096 chars max per Telegram. Caller truncates
            if needed; we do not — the failure mode of an over-long
            message is a clean 400 which the helper returns as False.
      tenant: tenant whose bot token authorises the send. Required —
              there is no global fallback by design.
      reply_markup: pre-converted Telegram ``InlineKeyboardMarkup``
                    dict (from
                    :func:`apps.channels.telegram.keyboards.to_inline_keyboard`)
                    or ``None`` for no keyboard. Passed verbatim into
                    the JSON body.
      parse_mode: ``"HTML"`` / ``"MarkdownV2"`` / ``None``. Default
                  ``None`` is plain text — safe for any user-supplied
                  content. Callers that want HTML must escape via
                  ``html.escape`` first.
      timeout: HTTP request timeout in seconds.

    Returns:
      ``True`` on 2xx, ``False`` on any failure.
    """
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    if parse_mode is not None:
        body["parse_mode"] = parse_mode
    # ``disable_web_page_preview=True`` for link-heavy messages avoids
    # the embedded preview Telegram inlines under URLs — distracting for
    # transactional content like booking confirmations. Callers that
    # WANT the preview pass parse_mode=HTML and Telegram still renders
    # entities; this flag only suppresses the auto-preview block.
    body.setdefault("disable_web_page_preview", True)

    ok = _post("sendMessage", tenant=tenant, body=body, timeout=timeout)
    if ok:
        logger.info(
            "channels.telegram.outbound.sent tenant=%s chat_id=%s len=%d has_keyboard=%s",
            tenant.id,
            chat_id,
            len(text),
            bool(reply_markup),
        )
    return ok


def send_photo(
    *,
    chat_id: str,
    photo: str,
    tenant: "Tenant",
    caption: str = "",
    reply_markup: dict[str, Any] | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """POST ``sendPhoto`` for the given tenant's Telegram bot.

    Args:
      chat_id: target Telegram chat ID.
      photo: either an HTTPS URL Telegram can fetch directly, OR a
             ``file_id`` from a previously uploaded photo (Telegram
             keeps these forever per-bot — cheap to reuse). For raw
             bytes upload use multipart/form-data; not implemented in
             Phase 1 because the salon flow only re-sends server-side
             stock photos by URL.
      tenant: tenant whose bot token authorises the send.
      caption: optional caption rendered under the photo, 1024 chars
               max per Telegram. Empty allowed.
      reply_markup: optional pre-converted Telegram keyboard dict.
      timeout: HTTP request timeout.

    Returns:
      ``True`` on 2xx, ``False`` on any failure.
    """
    body: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo,
    }
    if caption:
        body["caption"] = caption
    if reply_markup is not None:
        body["reply_markup"] = reply_markup

    ok = _post("sendPhoto", tenant=tenant, body=body, timeout=timeout)
    if ok:
        logger.info(
            "channels.telegram.outbound.photo_sent tenant=%s chat_id=%s caption_len=%d",
            tenant.id,
            chat_id,
            len(caption),
        )
    return ok


def answer_callback_query(
    *,
    callback_query_id: str,
    tenant: "Tenant",
    text: str = "",
    show_alert: bool = False,
    timeout: float = _DEFAULT_TIMEOUT,
) -> bool:
    """POST ``answerCallbackQuery`` to clear the inline-button spinner.

    Telegram REQUIRES this call after every callback_query update —
    without it the user's button remains in the "loading" state for
    ~30s. The platform's MAX adapter has no equivalent (MAX clears the
    spinner client-side), so this is a Telegram-only step.

    Args:
      callback_query_id: the ``id`` from the inbound ``callback_query``
                         update. Available on the parser's
                         :class:`CanonicalEvent.raw["callback_query_id"]`.
      tenant: tenant whose bot token authorises the answer.
      text: optional toast text shown briefly to the user after the
            tap. Empty string is the default — clears the spinner
            silently, which is the right UX for transactional flows
            (the bot's outgoing message IS the feedback).
      show_alert: when True, Telegram shows a modal alert instead of a
                  toast. Used sparingly for destructive-confirm flows.
      timeout: HTTP request timeout.

    Returns:
      ``True`` on 2xx, ``False`` on any failure. The handler logs the
      failure but does not raise — the spinner will time out client-
      side after ~30s, which is annoying but not data-breaking.
    """
    body: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        body["text"] = text
    if show_alert:
        body["show_alert"] = True

    ok = _post("answerCallbackQuery", tenant=tenant, body=body, timeout=timeout)
    if ok:
        logger.info(
            "channels.telegram.outbound.callback_answered tenant=%s cqid=%s",
            tenant.id,
            callback_query_id,
        )
    return ok


# ``json`` is imported only for tests / debug serialisation; quiet lint.
_ = json
