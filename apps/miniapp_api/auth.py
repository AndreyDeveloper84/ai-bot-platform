"""MAX Mini App ``initData`` HMAC verification.

The Mini App passes the raw ``WebApp.initData`` string on every request
via the ``Authorization: MaxInitData <raw>`` header. This module:

1. Parses the URL-encoded ``initData`` into a key/value mapping.
2. Verifies the ``hash`` field against
   ``HMAC_SHA256(HMAC_SHA256("WebAppData", BOT_TOKEN), data_check_string)``
   per the MAX platform spec (mirrors Telegram WebApp).
3. Returns a :class:`VerifiedInitData` carrying the trusted ``user``
   payload + ``auth_date`` for staleness checks.

The verifier deliberately does NOT touch the database. Callers
(``views``) resolve the :class:`BotUser` + :class:`Tenant` from the
verified ``user.id`` separately.

### Trust model (per MAX skill ref Part 5)

After HMAC verification, the following fields are HMAC-bound:

* ``user.id`` — channel-side user UUID/integer
* ``user.first_name`` / ``user.last_name`` / ``user.username``
* ``chat.id`` / ``chat.type`` (when present)
* ``auth_date``

The wrapper rejects payloads where ``auth_date`` is older than
:attr:`AUTH_DATE_MAX_AGE_SECONDS` (default 60 min, matches MAX
recommendation).

### Why not a session token?

Phase 0b verifies on every request. This avoids a session table + JWT
rotation moving parts. The cost is one HMAC computation per request
(microseconds). A future phase can layer a session token on top
without changing the endpoint contract — the auth header would just
accept ``Bearer <token>`` as an alternative.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time as time_module
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from django.conf import settings

logger = logging.getLogger(__name__)


AUTH_DATE_MAX_AGE_SECONDS = 60 * 60  # 60 min — matches MAX recommendation.
HEADER_NAME = "Authorization"
HEADER_PREFIX = "MaxInitData "


class InitDataError(Exception):
    """Base error for initData verification failures."""


class InitDataMalformed(InitDataError):
    """The string can't be parsed at all (bad shape)."""


class InitDataBadSignature(InitDataError):
    """The HMAC doesn't match — the payload is forged or the bot token is wrong."""


class InitDataStale(InitDataError):
    """The ``auth_date`` is older than :attr:`AUTH_DATE_MAX_AGE_SECONDS`."""


class InitDataNotConfigured(InitDataError):
    """``MAX_BOT_TOKEN`` is unset — server can't verify anything."""


@dataclass(frozen=True)
class VerifiedInitData:
    """Successfully-verified initData — fields are HMAC-bound and safe to trust.

    ``raw`` is the original URL-encoded string for debugging / audit.
    ``params`` is the parsed key→string map (``hash`` already popped).
    ``user`` is the parsed user JSON dict (per MAX spec).
    ``auth_date`` is the unix-seconds timestamp when MAX signed.
    """

    raw: str
    params: dict[str, str]
    user: dict[str, Any]
    auth_date: int
    chat: dict[str, Any] | None = None

    @property
    def user_id(self) -> str:
        """Channel-side user id as string (MAX uses integers but we store str)."""

        return str(self.user.get("id", ""))


def extract_init_data(authorization_header: str | None) -> str:
    """Pull the raw initData string out of the ``Authorization`` header.

    Empty header or wrong prefix → :class:`InitDataMalformed`.
    """

    if not authorization_header:
        raise InitDataMalformed("missing Authorization header")
    if not authorization_header.startswith(HEADER_PREFIX):
        raise InitDataMalformed("expected 'MaxInitData <raw>' prefix")
    return authorization_header[len(HEADER_PREFIX) :]


def verify_init_data(
    raw: str,
    *,
    bot_token: str | None = None,
    now: int | None = None,
    max_age_seconds: int = AUTH_DATE_MAX_AGE_SECONDS,
) -> VerifiedInitData:
    """Parse + HMAC-verify the raw initData string.

    Parameters
    ----------
    raw
        The full URL-encoded query string from ``WebApp.initData``.
    bot_token
        Override for testing. Defaults to ``settings.MAX_BOT_TOKEN``.
    now
        Override for testing. Defaults to ``time.time()``.
    max_age_seconds
        Reject if ``now - auth_date > max_age_seconds``. Default 60 min.
    """

    token = bot_token if bot_token is not None else getattr(settings, "MAX_BOT_TOKEN", "")
    if not token:
        raise InitDataNotConfigured("MAX_BOT_TOKEN is not configured")

    if not raw:
        raise InitDataMalformed("empty initData")

    # parse_qsl preserves duplicates as list-of-tuples; the MAX spec
    # has no duplicate keys, so a dict is fine. keep_blank_values keeps
    # empty strings (e.g. last_name="") which MAX may emit.
    pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=False)
    params: dict[str, str] = {}
    for k, v in pairs:
        # Reject duplicate keys defensively — a forged payload could try
        # to slip in a second `hash` to fool naive parsers.
        if k in params:
            raise InitDataMalformed(f"duplicate key {k!r}")
        params[k] = v

    received_hash = params.pop("hash", "")
    if not received_hash:
        raise InitDataMalformed("missing hash field")

    # data_check_string: sorted by key, "key=value\n"-joined (newline
    # between entries, no trailing newline). The values are taken from
    # the URL-decoded payload (parse_qsl already decoded them).
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataBadSignature("HMAC mismatch")

    # auth_date freshness — required field after HMAC passed.
    try:
        auth_date = int(params.get("auth_date", "0"))
    except ValueError as exc:
        raise InitDataMalformed("auth_date not an integer") from exc
    if auth_date <= 0:
        raise InitDataMalformed("auth_date missing or zero")

    now_ts = now if now is not None else int(time_module.time())
    if now_ts - auth_date > max_age_seconds:
        raise InitDataStale(f"auth_date {auth_date} older than {max_age_seconds}s")

    # user payload — parse JSON. Required for our flows (we always need
    # to know who the customer is). chat payload is optional.
    try:
        user = json.loads(params["user"]) if "user" in params else {}
    except (KeyError, json.JSONDecodeError) as exc:
        raise InitDataMalformed("invalid user JSON") from exc
    if not user or "id" not in user:
        raise InitDataMalformed("user.id missing")

    chat: dict[str, Any] | None = None
    if "chat" in params:
        try:
            chat = json.loads(params["chat"])
        except json.JSONDecodeError as exc:
            raise InitDataMalformed("invalid chat JSON") from exc

    return VerifiedInitData(
        raw=raw,
        params=params,
        user=user,
        auth_date=auth_date,
        chat=chat,
    )


__all__ = [
    "AUTH_DATE_MAX_AGE_SECONDS",
    "HEADER_NAME",
    "HEADER_PREFIX",
    "InitDataBadSignature",
    "InitDataError",
    "InitDataMalformed",
    "InitDataNotConfigured",
    "InitDataStale",
    "VerifiedInitData",
    "extract_init_data",
    "verify_init_data",
]
