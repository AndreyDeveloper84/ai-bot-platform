/**
 * Payment status badge — C7.3 read model rendered via the shared
 * StatusBadge. Returns null for hidden/unknown states (ADR:
 * `waiting_for_capture` and anything unmapped stay invisible — a
 * missing badge is honest, a wrong one is not).
 */

import { mapPaymentStatus } from "../lib/payment-status";
import { StatusBadge } from "./StatusBadge";

interface Props {
  /** Raw capture_state from the backend (C7.3); absent pre-passthrough. */
  state: string | null | undefined;
}

export function PaymentStatusBadge({ state }: Props) {
  const mapped = mapPaymentStatus(state);
  if (!mapped.visible || !mapped.rendering) return null;
  return <StatusBadge rendering={mapped.rendering} />;
}
