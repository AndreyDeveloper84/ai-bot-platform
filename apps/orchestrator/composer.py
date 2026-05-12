"""Response composer (DRF-539 / Sprint 6 / O5).

Step 13 of the orchestrator pipeline. Takes the skill's SkillResult +
brand voice config + post-check verdict and produces the final outbound
payload (text + UI keyboard) that the channel adapter ships.

### Why a separate step

Skills produce raw text + action metadata. The channel needs:
1. Final reply text — may differ from skill text if post-check vetoed
2. UI keyboard / quick replies — derived from action_data
3. brand-voice signature / signoff applied

Mixing these into every skill would duplicate the safety + voice
plumbing across N skills. Composer is the single funnel.

### Phase 0 → Phase 1 evolution

Phase 0: template-based. If post-check verdict is BLOCK, swap text
for a canned safety disclaimer. If REVISE, soften via prefix; otherwise
pass-through. Brand-voice signoff appended when configured.

Phase 1 may add an LLM polish layer (rephrase with target tone) — the
signature stays stable; only the body changes.

### Latency budget

PHASE0_DESIGN §5.2 budget: <50ms. Pure-Python template render; well
under budget. Test: 100 calls in <50ms.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from apps.orchestrator.safety.post_check import PostCheckResult, PostCheckVerdict

logger = logging.getLogger(__name__)


# Canned safety replies per verdict. Phase 0 ships fixed text; Phase 1
# moves these to PromptRegistry so operators can tune per-tenant.
_BLOCK_TEMPLATE = (
    "Извините, я не могу ответить на этот запрос. "
    "Свяжитесь с менеджером: напишите «оператор», и я переадресую вас."
)

_REVISE_PREFIX = "Пожалуйста, обратите внимание, что я не врач. "


@dataclass(frozen=True)
class ComposedReply:
    """Final outbound payload — what the channel adapter sends.

    Fields:
      text: final reply body (may differ from skill_result.reply_text).
      ui_keyboard: list of UI button dicts. Phase 0 = direct passthrough
        of skill_result.action_data when shape-compatible; channel
        adapter renders per its capabilities.
      action_type: forwarded from skill_result for telemetry.
      action_data: forwarded from skill_result for forensic.
      final_send: True if channel should ship; False when post-check
        BLOCKed AND we have no canned fallback (rare — Phase 0 always
        ships SOMETHING per the no-ghosting principle).
      safety_revised: True if post-check vetoed the original text.
    """

    text: str
    ui_keyboard: list[dict[str, Any]] = field(default_factory=list)
    action_type: str = ""
    action_data: dict[str, Any] = field(default_factory=dict)
    final_send: bool = True
    safety_revised: bool = False


def compose(
    skill_result: Any,
    *,
    post_check: PostCheckResult | None = None,
    brand_voice: dict[str, Any] | None = None,
) -> ComposedReply:
    """Compose the final outbound payload.

    Args:
      skill_result: a duck-typed object with at least ``reply_text``;
        optional ``action_type``, ``action_data``, ``should_send``.
      post_check: optional :class:`PostCheckResult` (O4 step 12). If
        verdict=BLOCK, text replaced with safety disclaimer.
      brand_voice: optional dict; ``signoff`` (str) appended to text.

    Returns:
      :class:`ComposedReply`.
    """

    skill_text = str(getattr(skill_result, "reply_text", "") or "")
    should_send = bool(getattr(skill_result, "should_send", True))
    action_type = str(getattr(skill_result, "action_type", "") or "")
    action_data = dict(getattr(skill_result, "action_data", None) or {})

    # Skill explicitly said "don't send" (silenced by handoff guard, etc.) —
    # respect it. No safety verdict applies; no signoff.
    if not should_send:
        return ComposedReply(
            text="",
            action_type=action_type,
            action_data=action_data,
            final_send=False,
        )

    safety_revised = False
    final_text = skill_text

    # Post-check verdict overrides skill text.
    if post_check is not None:
        if post_check.verdict == PostCheckVerdict.BLOCK:
            final_text = _BLOCK_TEMPLATE
            safety_revised = True
            logger.warning(
                "composer.post_check_block matched=%s",
                post_check.matched_patterns[:3],
            )
        elif post_check.verdict == PostCheckVerdict.REVISE:
            # Phase 0 prefix-soft; Phase 1 may rephrase via LLM.
            final_text = _REVISE_PREFIX + skill_text
            safety_revised = True
            logger.info(
                "composer.post_check_revise matched=%s",
                post_check.matched_patterns[:3],
            )

    # Apply brand-voice signoff when configured AND when we haven't
    # already swapped to the safety template (don't append marketing
    # voice to a safety disclaimer).
    if not safety_revised and brand_voice:
        signoff = str(brand_voice.get("signoff") or "").strip()
        if signoff and signoff not in final_text:
            final_text = f"{final_text}\n\n{signoff}"

    ui_keyboard = _render_keyboard(action_data)

    return ComposedReply(
        text=final_text,
        ui_keyboard=ui_keyboard,
        action_type=action_type,
        action_data=action_data,
        final_send=True,
        safety_revised=safety_revised,
    )


def _render_keyboard(action_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract UI buttons from action_data. Channel-agnostic shape.

    Phase 0 contract: action_data may have a "buttons" key holding a
    list of {label, callback} dicts. Channel adapter (Sprint 2 MAX)
    renders them per its native widget. Other shapes are ignored —
    composer is the funnel, not a schema enforcer.
    """

    buttons = action_data.get("buttons")
    if not isinstance(buttons, list):
        return []
    out: list[dict[str, Any]] = []
    for b in buttons:
        if isinstance(b, dict) and "label" in b:
            out.append(
                {
                    "label": str(b["label"]),
                    "callback": str(b.get("callback") or ""),
                }
            )
    return out
