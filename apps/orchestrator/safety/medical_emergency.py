"""Canonical medical S1 emergency text — the single source for every surface.

Owner ruling 18.09.2026 — ``docs/OPEN_DECISIONS.md`` [OD-BOT §163], immutable
record ``docs/safety/reviews/OWNER_RULINGS_S1_AI_CLINICAL_PRE_REVIEW_2026-09-18.md``
(Решение 5). The text is reproduced **word for word**; it is not edited here
and not paraphrased by any consumer. v2 supersedes the 16.09 text ([§157])
as the runtime candidate.

What the ruling fixes about this text:

* it is the reply for a **medical** S1 red flag (G1–G7) on every live
  surface that shows a medical S1 verdict — the chat skill
  (:mod:`apps.skills.health_screening.skill`, per-tenant path and the global
  path through the tool) and the Mini App goal anketa
  (:mod:`apps.miniapp_api.health_gate`); the Mini App renders the server's
  ``safety.text`` as is;
* it is **separate** from the Psychological Crisis Policy: the founder-approved
  :data:`apps.orchestrator.safety.gate.CRISIS_REPLY_TEXT` and the ``HANDOFF``
  route are untouched (W1-06 = C, [§154]);
* it carries no diagnosis, no treatment, no medication, no home remedy —
  only the next step to emergency help (103 / 112);
* owner approval is **not** physician approval: the text awaits licensed
  physician confirmation and Legal / localization review (VQ1). Implementation
  of the registered owner policy does not constitute CLINICAL APPROVED,
  PHYSICIAN PASS or SAFE FOR PILOT.

Named limits (not fixed here): «сердечный приступ / скорая» still reach the
crisis ``HANDOFF`` route of ``pre_check`` (DRF-2000, S-2); G7 «резко стало
очень плохо» is still a plain red flag instead of the one-question CLARIFY of
[§160]; there is no persistence of the S1 state between turns (DRF-2040).
"""

from __future__ import annotations

#: [OD-BOT §163] — verbatim. Do not edit; a new owner ruling supersedes it.
MEDICAL_EMERGENCY_TEXT_V2 = (
    "По описанию это может требовать срочной медицинской помощи. "
    "Я не буду сейчас подбирать процедуру или оформлять запись. "
    "Если это происходит сейчас, произошло только что, повторяется, усиливается "
    "или тебе резко плохо — позвони 103 или 112. "
    "Не добирайся за рулём самостоятельно. "
    "Если можешь, попроси человека рядом помочь тебе вызвать помощь и остаться с тобой."
)

#: Emergency numbers the ruling names — the guard checks both are present.
EMERGENCY_NUMBERS: tuple[str, ...] = ("103", "112")

__all__ = ["EMERGENCY_NUMBERS", "MEDICAL_EMERGENCY_TEXT_V2"]
