/**
 * Enable MAX's "Are you sure you want to close?" prompt while a screen
 * has unsaved data (skill-ref Part 6 Pattern 2).
 *
 * Pass `true` while a form is dirty, `false` after submit / discard.
 */

import { useEffect } from "react";
import { setClosingConfirmation } from "../lib/max-sdk";

export function useClosingConfirmation(enabled: boolean): void {
  useEffect(() => {
    setClosingConfirmation(enabled);
    return () => setClosingConfirmation(false);
  }, [enabled]);
}
