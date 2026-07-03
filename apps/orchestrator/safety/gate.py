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

### ⚠️ CRISIS COPY IS A DRAFT

:data:`CRISIS_REPLY_TEXT` and the hotline in :data:`CRISIS_HOTLINE` are
placeholders pending **founder sign-off** (self-harm response wording is not an
engineering default). Do not treat as final. The gate mechanism is production-
ready; the words are not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from apps.orchestrator.safety.pre_check import SafetyResult, SafetyVerdict, pre_check

# ── DRAFT crisis copy — FOUNDER SIGN-OFF REQUIRED before merge ──────────────
# The hotline below is a placeholder. 8-800-2000-122 is the all-Russia free 24/7
# psychological helpline, included so the flow is functional in review, but the
# founder must confirm the correct line(s) + final wording. Do NOT treat as final.
CRISIS_HOTLINE = "8-800-2000-122"  # DRAFT placeholder — founder to confirm.

CRISIS_REPLY_TEXT = (
    "Мне очень жаль, что тебе сейчас так тяжело. Ты не один(одна), и о таком "
    "важно поговорить с человеком, который может поддержать.\n\n"
    f"☎️ Телефон доверия: {CRISIS_HOTLINE} — бесплатно, круглосуточно, анонимно.\n"
    "Если есть угроза жизни — позвони 112.\n\n"
    "Я рядом и помогу с записью к специалисту, когда будешь готов(а)."
)  # DRAFT — founder sign-off required.

# ── BLOCK copy — canned refusal for out-of-domain risk (drugs / diagnosis /
# legal). Neutral, redirects to a professional. Lower sensitivity than crisis;
# still worth a founder glance but not the gating concern.
BLOCK_REPLY_TEXT = (
    "Это лучше обсудить с профильным специалистом — я не могу давать медицинские "
    "или юридические советы. Помогу с подбором мастера и записью, если нужно."
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
