"""Global (tenant-less) MAX onboarding — welcome + 152-ФЗ consent (#1046).

The nationwide marketplace path (``_handle_global_max_event_inner``) previously
dropped a brand-new user straight into discovery — no greeting, no consent
capture. That was both a UX miss and a 152-ФЗ gap (the conversation is persisted,
and long-term memory will be, without a recorded consent). This module adds a
**Variant A «soft gate»** (founder verdict 2026-07-02): we greet + offer consent,
but we do NOT block discovery / one-off booking on it. Only long-term memory (G2)
and proactive messaging are gated — and that enforcement lives in the memory
writer (S1.7 / #1054, `apps/identity`), NOT here. This module is presentation +
consent capture only.

### Design — reuse WelcomeSkill directly

The per-tenant welcome/consent state machine already exists in
:class:`apps.skills.welcome.skill.WelcomeSkill` (``/start`` → menu → S2 consent →
stamp ``consent_at`` → S5). We reuse it **directly**, NOT through the per-tenant
skill dispatcher — the global path must stay tenant-less and cannot pull
per-tenant skills. Its :class:`SkillResult` is wrapped into a
:class:`DiscoveryReply` whose ``action_data`` intentionally mirrors
``SkillResult.action_data``, so ``handler._build_attachments`` renders the
keyboard identically with zero rendering changes.

Two text surfaces are swapped for marketplace framing, because WelcomeSkill
hardcodes the pilot salon «Формула тела» + a wellness first-action grid
(«сфотографировать еду / вода / цель»), which is the wrong entry for a nationwide
discovery bot:

* the initial welcome → :data:`GLOBAL_WELCOME_TEXT` + a single «Начать» button
  that routes into the shared S2 consent flow;
* the S5 first-action prompt → :data:`GLOBAL_S5_TEXT` («напиши услугу и город»),
  the marketplace call-to-action, with the wellness grid dropped.

The S2 consent texts themselves are marketplace-neutral («Я буду помнить о тебе
только то, что поможет рекомендовать точнее…») and pass through unchanged.

### Tenant-safety

Everything here runs at ``current_tenant() is None`` (asserted in
:func:`run_onboarding_turn`). Consent is journaled server-side via
:func:`apps.consent.services.record_global_consent` (sentinel-scoped
``ConsentRecord`` + audit) without ever entering a tenant scope — the server
proof-of-consent the regulator needs, extending the food-scanner journal (#956).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from apps.orchestrator.discovery import DiscoveryReply
from apps.tenancy.context import current_tenant

logger = logging.getLogger(__name__)

# Any ``cb:discover:*`` callback (today: the ``cb:discover:book:{tenant}:{master}``
# booking handoff, #1020) is NEVER onboarding — it must reach the booking flow
# even for a user whose ``welcomed_at`` is still NULL. Every global BotUser that
# predates this feature has ``welcomed_at IS NULL`` (the global path never stamped
# it), so without this guard the first post-flag-flip booking tap of an existing
# user would be swallowed by the welcome greeting.
_DISCOVER_CALLBACK_PREFIX = "cb:discover:"


# Marketplace-framed welcome. WelcomeSkill.WELCOME_TEXT names the «Формула тела»
# salon and a wellness menu — wrong for the nationwide discovery bot. Neutral
# «подобрать мастера по всей стране».
GLOBAL_WELCOME_TEXT = (
    "Привет! Я Ayla 👋\n\n"
    "Помогу подобрать мастера по всей стране и записаться — "
    "маникюр, массаж, стрижка и не только.\n\n"
    "Начнём?"
)

# Marketplace-framed S5 first action. WelcomeSkill's S5 is a wellness grid
# («сфотографировать еду / вода / цель») — after consent a discovery-bot user
# must land on «кого ищешь», not a food diary.
GLOBAL_S5_TEXT = (
    "Отлично! С чего начнём?\n"
    "Напиши услугу и город — например, «маникюр в Пензе» или «массаж завтра»."
)

# Single «Начать» button on the marketplace welcome — routes into the SHARED S2
# consent flow (WelcomeSkill handles ``cb:welcome:start_s2``). We drop the
# salon/wellness buttons WelcomeSkill would otherwise attach.
_START_BUTTON: list[dict[str, str]] = [{"label": "▶️ Начать", "callback": "cb:welcome:start_s2"}]

# reply_kind values (WelcomeSkill.meta["reply_kind"]) whose TEXT we replace with a
# marketplace surface. Everything else (S2 consent prompt, S2a details, refusal,
# ask/food/water prompts) passes through verbatim.
_WELCOME_KINDS = frozenset({"welcome", "welcome_s1_multitenant"})
_S5_KIND = "welcome_s5_first_action"

# Source slug stamped on the server consent journal row for the global welcome.
_CONSENT_SOURCE = "global_onboarding:welcome_s2"

# Disclosure-version snapshot stamped on the consent journal (152-ФЗ informed
# consent: the row must prove WHICH text the user accepted, not just that they
# tapped «Да»). Tracks the WelcomeSkill S2 consent copy (S2_CONSENT_TEXT /
# S2A_DETAILS_TEXT); bump this slug whenever that copy changes materially.
CONSENT_DOCUMENT_VERSION = "welcome-s2-v1"


def needs_onboarding(bot_user: Any, text: str) -> bool:
    """Decide whether this global turn should run onboarding instead of discovery.

    True when any of (per #1046):

    * ``/start`` or ``/start <deeplink_payload>`` — explicit entry / deep link;
    * a ``cb:welcome:*`` callback tap — the user is mid-consent-flow (S2 prompt,
      consent yes/no, details fold);
    * first contact — the BotUser has ``welcomed_at IS NULL`` (never greeted).

    False otherwise — an already-welcomed user's plain message (or a
    ``cb:discover:book:*`` handoff tap) flows straight to discovery. This is what
    makes the gate «soft»: after a greeting (or a consent refusal) the user can
    keep searching without re-entering onboarding.

    A ``cb:discover:*`` callback (the booking handoff, #1020) is explicitly NOT
    onboarding even when ``welcomed_at IS NULL`` — a booking tap must reach the
    booking flow, never the welcome greeting. This matters at flag flip: every
    pre-existing global BotUser has ``welcomed_at IS NULL``, so without this guard
    their first booking tap after enabling the flag would be swallowed.
    """
    stripped = (text or "").strip()
    # Booking handoff wins over onboarding, unconditionally.
    if stripped.startswith(_DISCOVER_CALLBACK_PREFIX):
        return False
    if stripped == "/start" or stripped.startswith("/start "):
        return True
    if stripped.startswith("cb:welcome:"):
        return True
    return getattr(bot_user, "welcomed_at", None) is None


def run_onboarding_turn(
    conversation: Any,
    bot_user: Any,
    text: str,
    trace_id: str | uuid.UUID | None = None,
) -> DiscoveryReply:
    """Run one onboarding turn via WelcomeSkill and wrap it as a DiscoveryReply.

    Reuses :class:`WelcomeSkill` directly (see module docstring), swaps the two
    marketplace text surfaces, and — when this turn is the one that newly stamps
    ``consent_at`` — writes the server consent journal (#956 extension).

    Invariant: runs at ``current_tenant() is None`` (the global path never enters
    a tenant scope). The assertion is a loud guard, not decoration.
    """
    assert current_tenant() is None, (  # noqa: S101 — tenant-less invariant guard
        "global onboarding must run at current_tenant() is None"
    )

    # Lazy import — WelcomeSkill's module runs @register at import time (pulls
    # apps.skills.registry). Keep it off this module's load path, mirroring the
    # handler's lazy skill import.
    from apps.skills.base import SkillContext
    from apps.skills.welcome.skill import WelcomeSkill

    ctx = SkillContext(
        conversation=conversation,
        bot_user=bot_user,
        message_text=text,
        trace_id=str(trace_id) if trace_id else "",
    )
    result = WelcomeSkill().handle(ctx)

    # Journal consent on the grant turn. WelcomeSkill renders the S5 first-action
    # surface ONLY after stamping consent_at (both consent_yes and
    # consent_yes_via_s2a funnel through ``_render_consent_granted``), so the S5
    # reply_kind is the reliable «consent granted this turn» signal — more robust
    # than reading the in-memory ``consent_at`` delta (WelcomeSkill swallows a
    # failed ``consent_at`` DB save, which would desync that delta). The journal
    # write is idempotent (get_or_create), so re-tapping «Да» never duplicates and
    # a repeat tap backfills a row a prior transient failure dropped.
    if _is_consent_grant_turn(result):
        _record_consent_journal(bot_user)

    return _to_discovery_reply(result)


def _is_consent_grant_turn(result: Any) -> bool:
    """True when this WelcomeSkill turn is the one that grants consent (S5 render)."""
    return (getattr(result, "meta", None) or {}).get("reply_kind", "") == _S5_KIND


def _to_discovery_reply(result: Any) -> DiscoveryReply:
    """Wrap a WelcomeSkill :class:`SkillResult` into a :class:`DiscoveryReply`.

    Swaps the marketplace text surfaces; otherwise passes ``reply_text`` +
    ``action_data`` through unchanged so ``_build_attachments`` renders the same
    keyboard it would on the per-tenant path.
    """
    reply_kind = (getattr(result, "meta", None) or {}).get("reply_kind", "")

    if reply_kind in _WELCOME_KINDS:
        return DiscoveryReply(
            text=GLOBAL_WELCOME_TEXT,
            action_data={"buttons": _START_BUTTON, "button_columns": 1},
        )
    if reply_kind == _S5_KIND:
        # Marketplace CTA; drop the wellness first-action grid entirely.
        return DiscoveryReply(text=GLOBAL_S5_TEXT)

    # S2 consent prompt / S2a details / refusal / ask-food-water prompts →
    # verbatim (their texts are already marketplace-neutral).
    return DiscoveryReply(text=result.reply_text, action_data=result.action_data)


def _record_consent_journal(bot_user: Any) -> None:
    """Write the server-side proof-of-consent row (best-effort, loud on failure).

    ``consent_at`` on the BotUser is the primary record and is already stamped by
    the time we get here; this ConsentRecord row is the auditable journal. A
    failure must not break the user-facing reply (they DID consent this turn), but
    it is a compliance gap, so we log LOUD (``exception``) rather than swallow
    silently — matching WelcomeSkill's consent_at-save error handling.
    """
    try:
        from apps.consent.services import record_global_consent

        record_global_consent(
            bot_user,
            source=_CONSENT_SOURCE,
            document_version=CONSENT_DOCUMENT_VERSION,
        )
    except Exception:  # noqa: BLE001 — journal failure must not break the reply
        logger.exception(
            "global_onboarding.consent_journal_failed bot_user_id=%s",
            getattr(bot_user, "id", None),
        )
