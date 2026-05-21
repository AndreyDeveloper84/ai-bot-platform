"""Master mobile §M5 conversations list service layer (PR Tier1.3).

Pure-function helpers powering ``GET /api/v1/master/conversations`` —
the third Tier-1 master end-to-end gap. The master sees only their own
conversations with strict PII gating per :doc:`conversation-ownership-
policy` §4 (capability column «own only» for the *Master* role).

Spec quote (master-mobile §M5, lines 602-613 of
``docs/design/handoffs/2026-05-18-master-mobile-handoff.md``):

    «Master's card is **stripped down** vs admin's:
     ❌ No LTV / financial signal
     ❌ No reveal-phone hint
     ❌ No assignee badge (master only sees own)
     ❌ No tier banner (HUMAN_LOCKED shown differently — see M6)
     ✅ Customer first name only
     ✅ SLA color
     ✅ Last message preview (max 2 lines)
     ✅ «постоянный клиент» chip if returning (no LTV value)
     ✅ Reason chip («жалоба» NOT shown unless master is direct
     subject; «вопрос», «запись» yes)»

### Scope cuts (per PR Tier1.3 spec)

* M5 frontend (layout, tabs, search bar) — separate PR.
* M6 conversation detail backend — separate PR.
* AI draft reply generation — separate PR. ``ai_drafted_reply_available``
  is hard-False here; the ``ai_drafted`` section is therefore empty in
  practice until that work lands.
* Direct-master-complaint detection — heuristic too noisy for this
  PR; reason_chip is null for every conversation for now (so we
  never leak a «жалоба» on a master). Refined later.
* WebSocket / SSE live updates — separate PR.
* Master sending reply — that's M6 territory.

### "Involves master" definition

A conversation involves *master* IFF:

1. The conversation's ``bot_user`` has at least one
   :class:`apps.booking.models.BookingRequest` row where
   ``master_id == current_master`` (past, present, or future) in the
   same tenant.

No explicit ``Conversation.assigned_master`` field exists yet — when
that lands the OR clause is a single ``| Q(assigned_master=master)``.

The filter is implemented with a single
:class:`django.db.models.Exists` annotation against
:class:`BookingRequest` — no N+1, one SQL roundtrip for the list page.

### Section classification (5 sections)

* ``awaiting_master`` — last :class:`~apps.conversations.models.Message`
  is from ``role='user'`` (customer) AND no AI draft pending. Master
  needs to reply.
* ``ai_drafted`` — AI prepared a draft reply. No backing field exists
  yet, so this section is permanently empty until the AI-draft model
  lands. Kept in the response for forward compatibility.
* ``ai_handling`` — last reply is from ``role='assistant'``. Bot is
  speaking; master doesn't need to act.
* ``resolved`` — ``is_active=False`` OR ``outcome != ''``. The
  ``resolved`` filter additionally narrows this to closed within the
  last 30 days (``last_message_at >= now-30d``).
* ``other`` — catch-all (rare).

### PII gating per spec §M5 lines 608-617

ALWAYS excluded from response (no JSON key whatsoever):

* Customer phone
* Customer LTV / financial data
* Customer email
* Customer FULL last name (only single-letter initial)
* Assignee details
* HUMAN_LOCKED reason

Master can see:

* Customer FIRST name only
* Last name as 1-letter initial
* SLA tier color
* Last message excerpt (max 100 chars)
* Returning-customer chip (boolean)

Search NEVER finds by phone/email/full last name — it's restricted to
:attr:`BotUser.client_name` / :attr:`BotUser.display_name`
case-insensitive substring on the first whitespace-delimited token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import Conversation, Message

logger = logging.getLogger(__name__)


# --- constants ---------------------------------------------------------

DEFAULT_LIMIT = 25
MAX_LIMIT = 50

EXCERPT_MAX_LEN = 100
"""Per spec §M5 line 611 — last-message preview capped at 2 lines /
~100 chars."""

SLA_RED_HOURS = 4
SLA_YELLOW_MINUTES = 30
"""Same thresholds as :mod:`apps.master_api.services.dashboard`."""

RESOLVED_FILTER_WINDOW_DAYS = 30
"""``filter=resolved`` narrows the resolved section to the last 30
days. Older resolved rows are out of the M5 feed."""

RESOLVED_OUTCOME_BOOKING_LOOKBACK_HOURS = 24
"""``_resolved_outcome_summary`` looks at CONFIRMED bookings created
within this window before resolution to find the «записалась на …»
hint."""

CURSOR_SALT = "master_api.conversations.cursor"
"""Per-token salt distinguishing this signer from other Django signed
payloads. Mirrors :data:`apps.master_api.auth.SESSION_TOKEN_SALT`
pattern."""


# Section ordering for the response sort (lower = earlier). Mirrors the
# spec's vertical section order: ЖДУТ ВАШЕГО ОТВЕТА → ПРЕДЛОЖЕН ОТВЕТ →
# ИДЁТ ДИАЛОГ → РЕШЕНО → other.
_SECTION_ORDER: dict[str, int] = {
    "awaiting_master": 0,
    "ai_drafted": 1,
    "ai_handling": 2,
    "resolved": 3,
    "other": 4,
}

_SLA_TIER_RANK: dict[str, int] = {"red": 0, "yellow": 1, "white": 2}


Section = Literal["awaiting_master", "ai_drafted", "ai_handling", "resolved", "other"]
SlaTier = Literal["red", "yellow", "white"]
FilterValue = Literal["active", "all", "resolved"]


# --- response dataclasses ----------------------------------------------


@dataclass(frozen=True)
class ConversationItem:
    conversation_id: str
    client_first_name: str
    client_last_initial: str
    is_returning_customer: bool
    section: str
    last_message_excerpt: str
    last_message_at: str | None
    sla_tier: str
    ai_drafted_reply_available: bool
    reason_chip: str | None
    resolved_outcome: str | None

    def to_dict(self) -> dict[str, Any]:
        # Explicit dict to make the PII gate auditable — adding a new
        # field to the dataclass without adding it here is a no-op.
        return {
            "conversation_id": self.conversation_id,
            "client_first_name": self.client_first_name,
            "client_last_initial": self.client_last_initial,
            "is_returning_customer": self.is_returning_customer,
            "section": self.section,
            "last_message_excerpt": self.last_message_excerpt,
            "last_message_at": self.last_message_at,
            "sla_tier": self.sla_tier,
            "ai_drafted_reply_available": self.ai_drafted_reply_available,
            "reason_chip": self.reason_chip,
            "resolved_outcome": self.resolved_outcome,
        }


@dataclass(frozen=True)
class SectionCounts:
    awaiting_master: int
    ai_drafted: int
    ai_handling: int
    resolved_today: int

    def to_dict(self) -> dict[str, int]:
        return {
            "awaiting_master": self.awaiting_master,
            "ai_drafted": self.ai_drafted,
            "ai_handling": self.ai_handling,
            "resolved_today": self.resolved_today,
        }


@dataclass(frozen=True)
class ConversationsListResponse:
    items: list[ConversationItem] = field(default_factory=list)
    section_counts: SectionCounts = field(default_factory=lambda: SectionCounts(0, 0, 0, 0))
    next_cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [i.to_dict() for i in self.items],
            "section_counts": self.section_counts.to_dict(),
            "next_cursor": self.next_cursor,
        }


# --- errors ------------------------------------------------------------


class ConversationsListError(Exception):
    """Validation failure surfaced by the service to the view layer."""

    slug: str = "bad_request"

    def __init__(self, slug: str, detail: str) -> None:
        super().__init__(detail)
        self.slug = slug
        self.detail = detail


# --- cursor signing ----------------------------------------------------


def _cursor_secret() -> bytes:
    """Pull a stable secret for HMAC-signing cursors.

    Falls back to ``SECRET_KEY`` — same convention as
    :mod:`apps.master_api.auth`.
    """

    key = getattr(settings, "MASTER_SESSION_SECRET", "") or settings.SECRET_KEY
    if isinstance(key, str):
        return key.encode()
    return bytes(key)


def _sign_cursor(payload: dict[str, Any]) -> str:
    """Encode + sign a cursor payload as base64(payload):hex(hmac)."""

    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    body = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    sig = hmac.new(
        _cursor_secret() + CURSOR_SALT.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    return f"{body}:{sig}"


def _verify_cursor(token: str) -> dict[str, Any]:
    """Inverse of :func:`_sign_cursor`. Raises :class:`ConversationsListError`
    on tampering / malformed input.
    """

    if ":" not in token:
        raise ConversationsListError("bad_cursor", "cursor is malformed")
    body, sig = token.rsplit(":", 1)
    expected = hmac.new(
        _cursor_secret() + CURSOR_SALT.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ConversationsListError("bad_cursor", "cursor signature mismatch")
    # Pad to multiple-of-4 for b64.
    padded = body + "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode())
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ConversationsListError("bad_cursor", "cursor payload not decodable") from exc
    if not isinstance(payload, dict):
        raise ConversationsListError("bad_cursor", "cursor payload not an object")
    return payload


# --- name helpers ------------------------------------------------------


def _client_name_for(conversation: Conversation) -> str:
    """Return the customer's preferred display name from the linked BotUser.

    Falls back through ``client_name`` (what they typed) → ``display_name``
    (channel-reported) → channel_user_id. The channel id is never PII
    (it's a numeric MAX user id) so it's safe as a final fallback in the
    «no name set» case.
    """

    bot_user = conversation.bot_user
    if bot_user is None:
        return ""
    return (
        (bot_user.client_name or "").strip()
        or (bot_user.display_name or "").strip()
        or (bot_user.channel_user_id or "").strip()
    )


def _split_name(client_name: str) -> tuple[str, str]:
    """Split «Ксения Леонова» → («Ксения», «Л.»). See dashboard helper.

    Defensive: empty input → ("", ""); single-token input → (token, "").
    Per spec §M5 line 608: «Customer first name only» + «Last name as
    1-letter initial».
    """

    parts = (client_name or "").strip().split()
    if not parts:
        return "", ""
    first = parts[0]
    if len(parts) == 1:
        return first, ""
    last_initial = parts[1][:1].upper() + "."
    return first, last_initial


def _truncate(text: str, limit: int = EXCERPT_MAX_LEN) -> str:
    """Trim ``text`` to ``limit`` chars with a trailing ellipsis."""

    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "…"


# --- SLA / returning customer ------------------------------------------


def _sla_tier(last_message_at: datetime | None, now: datetime) -> SlaTier:
    """Best-effort SLA tiering.

    * ``red`` ≥ 4h since last customer message (master is overdue)
    * ``yellow`` ≥ 30min
    * ``white`` otherwise OR master already replied (last_message_at
      None signals «no waiting customer message»).
    """

    if last_message_at is None:
        return "white"
    delta = now - last_message_at
    if delta >= timedelta(hours=SLA_RED_HOURS):
        return "red"
    if delta >= timedelta(minutes=SLA_YELLOW_MINUTES):
        return "yellow"
    return "white"


def _build_returning_set(master: CatalogMaster, bot_user_ids: list[Any]) -> set[Any]:
    """Single-scan returning-customer index for the page.

    Returning = >1 CONFIRMED + COMPLETED bookings with this master.
    Same definition as
    :func:`apps.master_api.services.dashboard._is_returning_customer`,
    optimised to one DB scan per page (avoids N+1 across items).

    «Completed» is encoded as ``completed_at IS NOT NULL`` — the
    BookingRequest model has no COMPLETED status value; the post-visit
    Celery beat stamps ``completed_at`` instead.
    """

    if not bot_user_ids:
        return set()
    rows = (
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            bot_user_id__in=list(bot_user_ids),
        )
        .filter(Q(status=BookingRequest.Status.CONFIRMED) | Q(completed_at__isnull=False))
        .values_list("bot_user_id", flat=True)
    )
    counts = Counter(rows)
    return {bid for bid, n in counts.items() if n > 1}


def _is_returning(bot_user: Any, master: CatalogMaster) -> bool:
    """Single-conversation flavour of :func:`_build_returning_set`.

    Kept as a public helper for unit testing per the PR Tier1.3 spec
    sub-helpers list. The list endpoint uses the batched flavour above.
    """

    if bot_user is None:
        return False
    n = (
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            bot_user_id=bot_user.id,
        )
        .filter(Q(status=BookingRequest.Status.CONFIRMED) | Q(completed_at__isnull=False))
        .count()
    )
    return n > 1


# --- section classification --------------------------------------------


def _section_for(conversation: Conversation, last_message: Message | None) -> Section:
    """Classify a conversation into one of the 5 master-inbox sections.

    Pure function — caller passes the pre-fetched last :class:`Message`
    so this helper does no DB access. Spec §M5 categories:

    * resolved      → is_active=False OR outcome non-empty
    * awaiting_master → last message is role='user'
    * ai_handling   → last message is role='assistant' (bot speaking)
    * ai_drafted    → AI draft pending; no model field yet, so always
                      False here (placeholder kept for forward-
                      compatibility — refined when draft model lands)
    * other         → catch-all (e.g. last message is role='tool')
    """

    # Resolved is computed first — a closed conversation's last
    # message role is irrelevant to the master.
    if not conversation.is_active or (conversation.outcome or ""):
        return "resolved"

    if last_message is None:
        return "other"

    role = last_message.role
    if role == Message.Role.USER:
        return "awaiting_master"
    if role == Message.Role.ASSISTANT:
        return "ai_handling"
    # tool / system messages end up here — bot internals, not a
    # customer-visible state.
    return "other"


def _redact_for_master(
    *,
    conversation: Conversation,
    last_message: Message | None,
    last_customer_message_at: datetime | None,
    section: str,
    is_returning: bool,
    resolved_outcome: str | None,
    now: datetime,
) -> ConversationItem:
    """Assemble the master-facing :class:`ConversationItem`.

    PII-gating happens HERE — by construction the dataclass holds only
    the fields the master may see. No phone, no full last name, no
    LTV, no assignee, no HUMAN_LOCKED reason. The function signature
    intentionally pulls only redacted inputs from the caller.
    """

    client_name = _client_name_for(conversation)
    first, last_initial = _split_name(client_name)

    if last_message is not None:
        body = _truncate(last_message.rendered_text or last_message.content)
        last_msg_iso = last_message.created_at.isoformat() if last_message.created_at else None
    else:
        body = ""
        last_msg_iso = (
            conversation.last_message_at.isoformat() if conversation.last_message_at else None
        )

    # SLA tier only meaningful when there's an unanswered customer
    # message (awaiting_master). For other sections the master isn't
    # «late», so we return white.
    if section == "awaiting_master":
        tier = _sla_tier(last_customer_message_at, now)
    else:
        tier = "white"

    return ConversationItem(
        conversation_id=str(conversation.id),
        client_first_name=first,
        client_last_initial=last_initial,
        is_returning_customer=is_returning,
        section=section,
        last_message_excerpt=body,
        last_message_at=last_msg_iso,
        sla_tier=tier,
        # AI draft model not yet present; never True until that ships.
        ai_drafted_reply_available=False,
        # «Direct master complaint» detection is too noisy a heuristic
        # for this PR; we suppress reason chips entirely so we never
        # leak a «жалоба» on a master. Refined when the policy lands.
        reason_chip=None,
        resolved_outcome=resolved_outcome,
    )


def _resolved_outcome_summary(conversation: Conversation, master: CatalogMaster) -> str | None:
    """Best-effort «записалась на 22 мая» summary for resolved rows.

    Per PR Tier1.3 spec: if a CONFIRMED booking exists for this
    conversation's bot_user and master, created within
    :data:`RESOLVED_OUTCOME_BOOKING_LOOKBACK_HOURS` before resolution,
    use its ``visit_at`` in the master's tenant TZ. Else None.

    «Resolution time» is approximated by ``last_message_at`` for the
    conversation since :class:`Conversation` has no dedicated
    ``resolved_at`` field — the close path stamps ``last_message_at``
    anyway. Good-enough for a UI hint.
    """

    if conversation.bot_user_id is None:
        return None
    resolved_anchor = conversation.last_message_at
    if resolved_anchor is None:
        return None
    booking = (
        BookingRequest.all_tenants.filter(
            tenant_id=master.tenant_id,
            master_id=master.id,
            bot_user_id=conversation.bot_user_id,
            status=BookingRequest.Status.CONFIRMED,
            visit_at__isnull=False,
            created_at__gte=resolved_anchor
            - timedelta(hours=RESOLVED_OUTCOME_BOOKING_LOOKBACK_HOURS),
            created_at__lte=resolved_anchor
            + timedelta(hours=RESOLVED_OUTCOME_BOOKING_LOOKBACK_HOURS),
        )
        .order_by("-visit_at")
        .first()
    )
    if booking is None or booking.visit_at is None:
        return None

    # Format «записалась на 22 мая» in tenant-local TZ for human
    # readability. We deliberately don't import the
    # :func:`get_tenant_tz` helper from dashboard.py to keep this module
    # free of an apps-loading cycle; reuse via duplication is fine for
    # a 3-line function.
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    tz_name = getattr(master.tenant, "timezone", "") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
    visit_local = booking.visit_at.astimezone(tz)
    month_ru = [
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ][visit_local.month - 1]
    return f"записалась на {visit_local.day} {month_ru}"


# --- search normalisation ---------------------------------------------


def _normalize_search(raw: str) -> str:
    """Return the first whitespace-delimited token (case-insensitive).

    Per spec §M5 line 624: «Search by customer name (no phone — master
    doesn't see phones)». Restricting to the first token discourages
    «Иванова +79161234567» style search-by-phone attempts and matches
    only against ``BotUser.client_name`` / ``display_name`` — fields
    that hold names, not phones.
    """

    return (raw or "").strip().split()[0].lower() if (raw or "").strip() else ""


# --- main entrypoint --------------------------------------------------


def list_master_conversations(
    master: CatalogMaster,
    *,
    filter: FilterValue = "active",  # noqa: A002 — public API name fixed by spec
    search: str = "",
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    now: datetime | None = None,
) -> ConversationsListResponse:
    """Single GET endpoint backend for ``/api/v1/master/conversations``.

    Spec quote (master-mobile §M5 line 623):

        «Filter: «Активные» (default, requires attention) / «Все»
        (all conversations involving master) / «Решённые» (resolved
        last 30 days)»

    Spec quote (master-mobile §M5 line 622):

        «Default sort: SLA tier desc → recency desc»

    Sort within :class:`ConversationsListResponse.items`:
    ``section_order → SLA tier → last_message_at desc``.

    The «involves master» predicate is one
    :class:`django.db.models.Exists` annotation against
    :class:`BookingRequest` — no N+1 across the page.

    PII gating is implemented at :func:`_redact_for_master`: the
    response dataclass exposes only first name + initial, SLA color,
    last-message excerpt, and a returning-customer chip. Phone, email,
    LTV, full last name, assignee, and HUMAN_LOCKED reason are never
    serialised.
    """

    if now is None:
        now = dj_timezone.now()

    if filter not in ("active", "all", "resolved"):
        raise ConversationsListError("bad_request", "filter must be active|all|resolved")
    if limit < 1 or limit > MAX_LIMIT:
        raise ConversationsListError("bad_request", f"limit must be in 1..{MAX_LIMIT}")

    cursor_payload: dict[str, Any] | None = None
    if cursor:
        cursor_payload = _verify_cursor(cursor)

    # Base "involves this master" predicate: BookingRequest row exists
    # joining the conversation's bot_user to current master.
    involves_master = BookingRequest.all_tenants.filter(
        tenant_id=master.tenant_id,
        master_id=master.id,
        bot_user_id=OuterRef("bot_user_id"),
    )

    qs = (
        Conversation.all_tenants.filter(
            tenant_id=master.tenant_id,
            deleted_at__isnull=True,
            is_shadow=False,
            bot_user__isnull=False,
        )
        .annotate(_involves=Exists(involves_master))
        .filter(_involves=True)
        .select_related("bot_user")
    )

    # Search filter applied at the Python layer after the SQL pre-filter.
    # SQLite's LOWER() and LIKE are NOT Unicode-aware — they're a no-op
    # for Cyrillic / non-ASCII text. Postgres' ILIKE is locale-aware,
    # but the contract here is «case-insensitive across both backends».
    # The candidate list is bounded by ``candidate_cap`` so the Python
    # pass is O(limit*10) per request — cheap.
    #
    # Phone / email are NEVER part of the filter; the spec explicitly
    # forbids master-side phone search (§M5 line 624). The Python check
    # only inspects :attr:`BotUser.client_name` / ``display_name``.
    needle = _normalize_search(search)

    if filter == "resolved":
        cutoff = now - timedelta(days=RESOLVED_FILTER_WINDOW_DAYS)
        qs = qs.filter((Q(is_active=False) | ~Q(outcome="")) & Q(last_message_at__gte=cutoff))
    elif filter == "active":
        # Active = needs attention. We can't classify section in SQL
        # without joining Message — so we narrow the SQL to «open
        # conversations» and filter at the Python layer. The result is
        # bounded by the «involves master» join (at most O(bookings)
        # rows per master) so a Python pass is fine.
        qs = qs.filter(is_active=True, outcome="")
    # filter == "all": no extra status filter; everything involving
    # master is fair game.

    # Pull a generous candidate window for in-Python classification +
    # sort + slice. Capped to limit*10 to bound the pass — for realistic
    # master inboxes (≤ a few hundred clients) this fits comfortably.
    candidate_cap = max(limit * 10, 100)
    candidates = list(qs.order_by("-last_message_at", "-created_at")[:candidate_cap])

    if needle:
        # Unicode-aware case-insensitive filter applied at the Python
        # layer (see needle assignment above for rationale).
        filtered = []
        for c in candidates:
            if c.bot_user is None:
                continue
            haystack = (
                (c.bot_user.client_name or "").lower()
                + "\n"
                + (c.bot_user.display_name or "").lower()
            )
            if needle in haystack:
                filtered.append(c)
        candidates = filtered

    if not candidates:
        return ConversationsListResponse(
            items=[],
            section_counts=SectionCounts(0, 0, 0, 0),
            next_cursor=None,
        )

    # Pre-fetch last message per conversation in one query.
    conv_ids = [c.id for c in candidates]
    last_msgs_by_conv: dict[Any, Message] = {}
    last_user_at_by_conv: dict[Any, datetime] = {}

    # Last message overall (any role) — drives section classification.
    for msg in Message.all_tenants.filter(conversation_id__in=conv_ids).order_by(
        "conversation_id", "-created_at"
    ):
        if msg.conversation_id not in last_msgs_by_conv:
            last_msgs_by_conv[msg.conversation_id] = msg

    # Last USER message — drives SLA tier for awaiting_master.
    for msg in Message.all_tenants.filter(
        conversation_id__in=conv_ids, role=Message.Role.USER
    ).order_by("conversation_id", "-created_at"):
        if msg.conversation_id not in last_user_at_by_conv:
            last_user_at_by_conv[msg.conversation_id] = msg.created_at

    # Returning-customer set: one DB scan for the whole page.
    bot_user_ids = [c.bot_user_id for c in candidates if c.bot_user_id is not None]
    returning_set = _build_returning_set(master, bot_user_ids)

    # Classify + redact each row.
    items: list[ConversationItem] = []
    section_count: Counter[str] = Counter()
    resolved_today_cutoff = now - timedelta(days=1)

    for conv in candidates:
        last_msg = last_msgs_by_conv.get(conv.id)
        last_user_at = last_user_at_by_conv.get(conv.id)
        section = _section_for(conv, last_msg)
        section_count[section] += 1
        if section == "resolved" and (
            conv.last_message_at is None or conv.last_message_at >= resolved_today_cutoff
        ):
            section_count["resolved_today"] += 1

        # Apply the filter selection after classification (filter=active
        # narrows further: drops ai_handling / other from the visible
        # list even though they passed the SQL pre-filter).
        if filter == "active" and section not in ("awaiting_master", "ai_drafted"):
            continue
        if filter == "resolved" and section != "resolved":
            continue
        # filter=all: keep everything.

        resolved_outcome = None
        if section == "resolved":
            resolved_outcome = _resolved_outcome_summary(conv, master)

        item = _redact_for_master(
            conversation=conv,
            last_message=last_msg,
            last_customer_message_at=last_user_at,
            section=section,
            is_returning=conv.bot_user_id in returning_set,
            resolved_outcome=resolved_outcome,
            now=now,
        )
        items.append(item)

    items.sort(key=_sort_key)

    # Apply cursor offset (post-sort) — opaque (section_order, sla_rank,
    # last_msg_iso, conversation_id) tuple.
    start_idx = 0
    if cursor_payload is not None:
        anchor = (
            int(cursor_payload.get("so", -1)),
            int(cursor_payload.get("sr", -1)),
            cursor_payload.get("lm") or "",
            cursor_payload.get("id") or "",
        )
        for idx, it in enumerate(items):
            if _item_tuple(it) > anchor:
                # Sort is ascending on the composite key, but
                # last_message_at recency wants descending. We invert
                # last_message_at when building the tuple, so simple
                # > comparison works for «strictly after» pagination.
                start_idx = idx
                break
        else:
            start_idx = len(items)
    page = items[start_idx : start_idx + limit]

    next_cursor: str | None = None
    if start_idx + limit < len(items):
        anchor_item = page[-1] if page else None
        if anchor_item is not None:
            so, sr, lm, cid = _item_tuple(anchor_item)
            next_cursor = _sign_cursor({"so": so, "sr": sr, "lm": lm, "id": cid})

    counts = SectionCounts(
        awaiting_master=section_count.get("awaiting_master", 0),
        ai_drafted=section_count.get("ai_drafted", 0),
        ai_handling=section_count.get("ai_handling", 0),
        resolved_today=section_count.get("resolved_today", 0),
    )

    return ConversationsListResponse(items=page, section_counts=counts, next_cursor=next_cursor)


# --- sort key helpers --------------------------------------------------


def _item_tuple(item: ConversationItem) -> tuple[int, int, str, str]:
    """Composite sort key for a :class:`ConversationItem`.

    Composite: (section_order, sla_rank, -timestamp_iso, conversation_id).

    Negating timestamps numerically across ISO strings is awkward, so
    we use the trick of storing the *negative* unix-epoch float as an
    inverse-sort fragment. Cursor encoding mirrors this exactly so a
    cursor decodes into the same comparable shape.
    """

    so = _SECTION_ORDER.get(item.section, 99)
    sr = _SLA_TIER_RANK.get(item.sla_tier, 99)
    # Use the ISO string DESC: invert by string comparison reversal
    # (Z-A becomes A-Z) by mapping the ISO timestamp to its
    # «complement». Simpler: store negative epoch as a fixed-width
    # padded int string so lexicographic order == numeric order.
    if item.last_message_at:
        try:
            ts = datetime.fromisoformat(item.last_message_at.replace("Z", "+00:00"))
            inv = int((datetime.max.replace(tzinfo=ts.tzinfo) - ts).total_seconds())
        except (TypeError, ValueError):
            inv = 0
    else:
        inv = 2**31
    # Pad to 12 digits so lexicographic comparison matches numeric.
    inv_str = f"{inv:012d}"
    return (so, sr, inv_str, item.conversation_id)


def _sort_key(item: ConversationItem) -> tuple[int, int, str, str]:
    return _item_tuple(item)


__all__ = [
    "ConversationItem",
    "ConversationsListError",
    "ConversationsListResponse",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "SectionCounts",
    "_build_returning_set",
    "_is_returning",
    "_redact_for_master",
    "_resolved_outcome_summary",
    "_section_for",
    "_sla_tier",
    "_sign_cursor",
    "_verify_cursor",
    "list_master_conversations",
]
