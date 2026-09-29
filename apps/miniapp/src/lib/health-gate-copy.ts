/**
 * Copy of the health-safety entry on the goal anketa (DRF-1763, C02-M7).
 *
 * The server sends the acknowledgement and the questions in the `safety`
 * envelope of `POST /customer/goals/select`; the screen renders what it
 * receives. These constants exist for the parity test with the Python
 * source (`apps/miniapp_api/health_gate.py`) and for the offline preview —
 * the wire value wins whenever both are present.
 */

/** GAP_MAP_C02 Q12 mockup line — word for word the Python constant. */
export const HEALTH_ACKNOWLEDGEMENT_COPY =
  "Я поняла, что здесь есть вопрос самочувствия. " +
  "Сначала уточню несколько вещей, чтобы не предложить неподходящий вариант.";

/** Body key that carries the person's answer next to the original body. */
export const SAFETY_ANSWER_FIELD = "safety_answer";

/** `safety.kind` values the server may send. */
export const SAFETY_KIND_CLARIFY = "health_clarify";
export const SAFETY_KIND_RED_FLAG = "health_red_flag";
export const SAFETY_KIND_CRISIS = "crisis";
export const SAFETY_KIND_BLOCK = "block";
