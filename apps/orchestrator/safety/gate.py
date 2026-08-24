"""Safety gate for the live MAX handlers (#1053).

``pre_check`` (self-harm / suicide / emergency / drugs / legal) previously ran
ONLY inside :func:`apps.orchestrator.pipeline.turn`, which is on NEITHER
production MAX path — so a red-flag phrase («я думаю о суициде») reached echo /
FAQ / discovery instead of a safe response. This module wires the SAME
``pre_check`` into a single helper both live handlers call BEFORE skill dispatch
/ discovery.

### Scope (S1-B, #1053) — detection + canned reply only

* ``HANDOFF`` verdict (suicide / self-harm / acute emergency / abuse) → a canned
  **crisis** reply (crisis resources).
* ``BLOCK`` verdict (specific drugs / definitive diagnosis / legal advice) → a
  canned **block** reply.
* ``CLARIFY`` and ``ALLOW`` → the turn proceeds to normal handling. We
  deliberately do NOT short-circuit CLARIFY: on a beauty marketplace an ambiguous
  «почему болит спина» is more likely a legitimate massage query than a safety
  event, and the regex CLARIFY bucket is broad. Short-circuiting only the two
  unambiguous-risk verdicts keeps the happy-path regression tight.

### Explicitly NOT in S1-B

* No ``AdminTask`` / HUMAN_HANDOFF flip. On the per-tenant path that is S1-C
  (#1047). On the tenant-less global path an AdminTask is never created (founder
  decision 2026-07-03, Variant A — a global support queue is post-pilot, #1076).
* No new safety patterns — this consumes the existing
  :mod:`apps.orchestrator.safety.pre_check` verdicts unchanged.

### Crisis copy

:data:`CRISIS_REPLY_TEXT` and :data:`BLOCK_REPLY_TEXT` are the **founder-approved**
user-facing responses (sign-off on PR #1084, 2026-07-04). This is the sole live
crisis reply, so the text is authoritative — change it only via a new founder
sign-off. :data:`CRISIS_HOTLINE` (8-800-2000-122, the all-Russia free 24/7
psychological helpline) is referenced by the crisis reply.

### The other direction (DRF-1210)

:func:`evaluate_inbound` reads what the PERSON said. Nothing on the client
path read what the BOT was about to say. ``evaluate_outbound`` (DRF-1061)
was built for exactly that and wired to four surfaces — the master
assistant, the two proactive senders and master deactivation — but not to
the one where a client is sitting, which is this one.

:func:`guard_outbound` is the channel-facing half that closes it, and it
lives here rather than in a module of its own for the same reason
``evaluate_inbound`` does: the two live handlers already import this
module, and «change the safety policy in ONE place» has to stay true in
both directions. It does not re-implement or relax
:func:`apps.orchestrator.safety.outbound.evaluate_outbound` — it calls it
unchanged and adds the one thing a shared channel helper owes: a single
PII-safe emit, so a block is visible in the bus with the category that
fired and never the sentence that fired it.

Why the inbound gate does not already cover this: it short-circuits only
``HANDOFF`` and ``BLOCK`` and deliberately lets ``CLARIFY`` through (see
the Scope note above). A confident medical claim in the ANSWER is
precisely the class that arrives as an innocuous ``CLARIFY`` question, so
the inbound gate cannot be the thing that catches it — and tightening it
so it could would drown legitimate beauty queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import logging

from apps.orchestrator.safety.outbound import evaluate_outbound
from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict, pre_check

logger = logging.getLogger(__name__)

# All-Russia free 24/7 psychological helpline, referenced by the crisis reply.
CRISIS_HOTLINE = "8-800-2000-122"

# Founder-approved crisis reply (sign-off PR #1084, 2026-07-04). Sole live
# self-harm response — change only via a new founder sign-off.
CRISIS_REPLY_TEXT = (
    "Спасибо, что написал(а) мне это. Мне не всё равно, и я хочу, "
    "чтобы ты был(а) в безопасности.\n\n"
    "Я — AI и не заменю живого человека, но рядом есть те, кто может "
    "поддержать прямо сейчас — бесплатно, круглосуточно и анонимно:\n\n"
    f"📞 {CRISIS_HOTLINE} — телефон доверия, психологическая помощь.\n"
    "🚑 112 — если жизни угрожает опасность прямо сейчас.\n\n"
    "Пожалуйста, позвони. Ты не один(одна)."
)

# Founder-approved block reply (sign-off PR #1084, 2026-07-04) — out-of-domain
# risk (drugs / diagnosis / legal), redirects to a professional.
BLOCK_REPLY_TEXT = (
    "Здесь я не помощник — это вопрос к специалисту, и я не хочу навредить советом.\n\n"
    "С красотой и записью помогу с радостью 💛"
)


@dataclass(frozen=True)
class SafetyGateOutcome:
    """Result of the inbound safety gate.

    ``allowed`` False means the handler must short-circuit with ``reply_text`` and
    NOT proceed to skill dispatch / discovery. ``verdict`` / ``matched_patterns``
    are carried for observability (event / audit), never user-facing.
    """

    allowed: bool
    verdict: str
    reply_text: str = ""
    reason: str = ""
    matched_patterns: list[str] = field(default_factory=list)


def evaluate_inbound(text: str) -> SafetyGateOutcome:
    """Run the shared safety pre-check over inbound ``text`` for the MAX handlers.

    Returns an :class:`SafetyGateOutcome`. Short-circuits (``allowed=False``) only
    on ``HANDOFF`` (crisis reply) and ``BLOCK`` (block reply); ``CLARIFY`` and
    ``ALLOW`` return ``allowed=True`` so the turn proceeds as before.

    ``intent_decision`` is intentionally not passed — neither live MAX path runs
    the LLM intent classifier (that is the pipeline's step 6, dead in prod). This
    is the pure-regex guard, which is exactly what catches the red-flag stems.
    """
    result: SafetyResult = pre_check(text)
    verdict = result.verdict

    if verdict == SafetyVerdict.HANDOFF:
        return SafetyGateOutcome(
            allowed=False,
            verdict=verdict.value,
            reply_text=CRISIS_REPLY_TEXT,
            reason=result.reason,
            matched_patterns=list(result.matched_patterns),
        )
    if verdict == SafetyVerdict.BLOCK:
        return SafetyGateOutcome(
            allowed=False,
            verdict=verdict.value,
            reply_text=BLOCK_REPLY_TEXT,
            reason=result.reason,
            matched_patterns=list(result.matched_patterns),
        )

    # CLARIFY + ALLOW → proceed to normal handling.
    return SafetyGateOutcome(allowed=True, verdict=verdict.value, reason=result.reason)


# --------------------------------------------------------------------------- #
# Outbound half (DRF-1210)                                                     #
# --------------------------------------------------------------------------- #
#: ``action_type`` for a turn the outbound guard replaced. Mirrors
#: ``"safety_pre_check"``: a blocked answer must be findable in the Message
#: table without joining anything, on every surface, the same way.
OUTBOUND_ACTION_TYPE = "safety_outbound"


@dataclass(frozen=True)
class OutboundGuardOutcome:
    """What the person may be shown, after the drafted reply was checked.

    ``text`` is the drafted text when clean and the replacement line when
    not — never a partially edited draft (see ``outbound.py``: cutting the
    offending sentence can invert what is left).

    ``categories`` is carried for observability only. It names the SHAPE
    that fired (``medical`` / ``promise`` / ``contact``), never the words.
    """

    allowed: bool
    text: str
    categories: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return not self.allowed


def guard_outbound(
    text: str,
    *,
    surface: str,
    bot_user: object | None = None,
    trace_id: object | None = None,
) -> OutboundGuardOutcome:
    """Check a drafted reply on its way to a person; emit once if it is blocked.

    ``surface`` is the free-form name of the place the reply was about to
    leave from (``"max"``, ``"telegram"``, ``"concierge"``). It rides in the
    payload rather than in the event name so one dashboard answers «how
    often does the assistant have to be stopped, and where» without a union
    over per-channel names.

    Never raises. ``evaluate_outbound`` already fails open on a broken
    pattern, and the emit is wrapped: a telemetry failure must not be the
    thing that costs someone their answer.
    """

    verdict = evaluate_outbound(text)
    if verdict.allowed:
        return OutboundGuardOutcome(allowed=True, text=verdict.text)

    logger.warning(
        "safety.outbound.blocked surface=%s categories=%s trace=%s",
        surface,
        ",".join(verdict.categories),
        trace_id,
    )
    try:
        from apps.events.services import emit

        emit(
            "safety.outbound_blocked",
            payload={
                # PII-safe by construction: the categories name the shape,
                # the length says how much was dropped, and neither is the
                # sentence we just decided nobody should read.
                "surface": surface,
                "categories": list(verdict.categories),
                "text_len": len(text or ""),
                "bot_user_id": str(getattr(bot_user, "id", "")) if bot_user is not None else "",
                "trace_id": str(trace_id) if trace_id else "",
            },
        )
    except Exception:  # noqa: BLE001 — telemetry must never cost the reply
        logger.exception("safety.outbound.emit_failed surface=%s", surface)

    return OutboundGuardOutcome(
        allowed=False,
        text=verdict.text,
        categories=tuple(verdict.categories),
    )


__all__ = [
    "BLOCK_REPLY_TEXT",
    "CRISIS_HOTLINE",
    "CRISIS_REPLY_TEXT",
    "OUTBOUND_ACTION_TYPE",
    "OutboundGuardOutcome",
    "SafetyGateOutcome",
    "evaluate_inbound",
    "guard_outbound",
]
