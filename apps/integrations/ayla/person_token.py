"""The salon administrator's OWN token for a salon write (DRF-2607).

Owner ruling 29.09: «(а) — подпись MAX, служебный ключ в записи не
участвует». Ayla (catalog #593) verifies the Mini App ``initData`` with the
salon-master bot's key and issues a short token bound to one person and one
salon: ``POST /api/v1/auth/max/salon-admin/token/``.

This module is the bot's half of that exchange and nothing else:

* it sends the person's ``initData`` as it arrived — the bot never signs,
  mints or substitutes anything;
* it sends **no** ``Authorization`` header: the service credential takes no
  part in obtaining the person's token;
* any failure is a :class:`PersonTokenRefused` with a ``reason`` for the
  journal. There is no fallback — no path in this repository turns «no token»
  into «then the service key».

Three levels of the refusal, kept apart on purpose (main window, 29.09):

1. **the response code** is the same for «no rights» and «no token» — an
   outsider probing the surface learns nothing from the difference;
2. **the journal** names the reason, because the cure differs: the person may
   not (``NO_SALON_ROLE``), the link is unproven (``LINK_NOT_PROVEN``), the
   signature is too old (``INIT_DATA_STALE``) — or WE are broken
   (``NOT_CONFIGURED``, a 5xx);
3. **the words to the person** are the owner's, not invented here. This
   module produces none.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

import httpx
from django.conf import settings

from apps.integrations.ayla.request_id import with_request_id
from apps.integrations.ayla.url_builder import AylaUrlBuilder

logger = logging.getLogger(__name__)

EXCHANGE_PATH = "auth/max/salon-admin/token/"
DEFAULT_TIMEOUT_S = 5.0

#: Journal reason when the exchange never got an answer.
REASON_UNREACHABLE = "EXCHANGE_UNREACHABLE"
#: Journal reason for a non-JSON / malformed success.
REASON_BAD_ANSWER = "EXCHANGE_BAD_ANSWER"


#: The ONLY refusals whose cause is the person: they may not, their link is
#: unproven, their account is not eligible, their Mini App launch is too old
#: (they reopen it), or they opened it from another bot. An allow-list on
#: purpose: everything else — a 404 because the catalog lacks the route, a 429
#: throttle, a 400 the bot's own parse disagrees with, a bad signature on a
#: payload the bot verified — is ours to fix, and a rule «below 500 is theirs»
#: would hide exactly those.
PERSON_SIDE_REASONS = frozenset(
    {
        "NO_SALON_ROLE",
        "IDENTITY_NOT_LINKED",
        "LINK_NOT_PROVEN",
        "IDENTITY_NOT_ELIGIBLE",
        "INIT_DATA_STALE",
        "WRONG_BOT",
    }
)

_REASON_RE = re.compile(r"^[A-Z0-9_:]{1,64}$")


def _clean_reason(value: str) -> str:
    """A catalog code goes into our journal verbatim — only if it looks like one."""
    return value if _REASON_RE.match(value) else "UNRECOGNISED_CODE"


class PersonTokenRefused(Exception):
    """No person token — the write must be refused, never re-routed."""

    def __init__(self, reason: str, *, status: int | None = None) -> None:
        reason = _clean_reason(reason)
        super().__init__(reason)
        self.reason = reason
        self.status = status

    @property
    def ours_to_fix(self) -> bool:
        """Anything that is not a named person-side refusal is ours."""
        return self.reason not in PERSON_SIDE_REASONS


@dataclass(frozen=True)
class PersonToken:
    access_token: str
    tenant_slug: str
    expires_in: int


def obtain_person_token(
    *,
    init_data: str,
    tenant_slug: str,
    base_url: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> PersonToken:
    """Exchange the person's ``initData`` for their own salon token."""
    if not init_data:
        raise PersonTokenRefused("NO_INIT_DATA")
    if not tenant_slug:
        raise PersonTokenRefused("NO_TENANT")
    root = base_url if base_url is not None else getattr(settings, "AYLA_BASE_URL", "")
    if not root:
        raise PersonTokenRefused("NOT_CONFIGURED")

    url = AylaUrlBuilder(root).build(EXCHANGE_PATH)
    headers = with_request_id(
        {
            # No Authorization: the service credential is not part of this.
            "X-Tenant": tenant_slug,
            "X-App-Type": "pro",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    try:
        with httpx.Client(timeout=timeout_s, transport=transport) as http:
            resp = http.post(url, headers=headers, json={"init_data": init_data})
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise PersonTokenRefused(REASON_UNREACHABLE) from exc

    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        code = ""
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            code = str(body["error"].get("code") or "")
        raise PersonTokenRefused(code or f"HTTP_{resp.status_code}", status=resp.status_code)

    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise PersonTokenRefused(REASON_BAD_ANSWER, status=resp.status_code)
    token = data.get("access_token")
    if not isinstance(token, str) or not token:
        raise PersonTokenRefused(REASON_BAD_ANSWER, status=resp.status_code)
    return PersonToken(
        access_token=token,
        tenant_slug=tenant_slug,
        expires_in=int(data.get("expires_in") or 0),
    )


# ── One exchange per person per launch (main window's decision, 29.09) ───────
#
# The catalog throttles the exchange at 10/min per IP — a guard of an
# AUTHENTICATION endpoint against guessing, and every exchange comes from the
# bot's one address. Raising it to fit a chatty client would weaken the guard
# to pay for our waste; the client is made less chatty instead: one exchange
# per (person, salon, launch). ``auth_date`` changes when the Mini App is
# reopened, so an entry expires together with the proof it was made from.
#
# Limits, named:
# * per process — the bot runs several workers, each with its own cache, so
#   the saving is N times smaller than ideal;
# * not durable — a restart (every deploy) empties it, and each administrator's
#   next write exchanges again.
# If writes still hit 429 with the cache, the next step is the catalog's rate —
# with a measured number, not a guess.

#: Kept this long before the token's own expiry, so a write never leaves with
#: a token that dies on the way.
EXPIRY_MARGIN_S = 60
MAX_ENTRIES = 512

CacheKey = tuple[str, str, str]  # (person, tenant slug, auth_date)


class _TokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: OrderedDict[CacheKey, tuple[PersonToken, float]] = OrderedDict()

    def get(self, key: CacheKey) -> PersonToken | None:
        with self._lock:
            row = self._rows.get(key)
            if row is None:
                return None
            token, until = row
            if time.monotonic() >= until:
                del self._rows[key]
                return None
            return token

    def put(self, key: CacheKey, token: PersonToken) -> None:
        life = token.expires_in - EXPIRY_MARGIN_S
        if life <= 0:
            return  # too short-lived to be worth keeping
        with self._lock:
            self._rows[key] = (token, time.monotonic() + life)
            self._rows.move_to_end(key)
            while len(self._rows) > MAX_ENTRIES:
                self._rows.popitem(last=False)

    def forget(self, key: CacheKey) -> None:
        with self._lock:
            self._rows.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._rows.clear()


_CACHE = _TokenCache()


def cached_person_token(
    *,
    key: CacheKey,
    init_data: str,
    tenant_slug: str,
    obtain: Callable[..., PersonToken] = obtain_person_token,
) -> PersonToken:
    """The person's token for this launch — exchanged at most once per key.

    Only a token is kept; a refusal never is, so the next write asks again.
    """
    token = _CACHE.get(key)
    if token is not None:
        return token
    token = obtain(init_data=init_data, tenant_slug=tenant_slug)
    _CACHE.put(key, token)
    return token


def forget_person_token(key: CacheKey) -> None:
    """Drop a token the catalog stopped accepting — the next write exchanges anew."""
    _CACHE.forget(key)


__all__ = [
    "PersonToken",
    "PersonTokenRefused",
    "cached_person_token",
    "forget_person_token",
    "obtain_person_token",
]
