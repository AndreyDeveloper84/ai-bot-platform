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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict, pre_check

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
