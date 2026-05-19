/**
 * Stable callbacks for haptic feedback — `useCallback`-wrapped so they
 * can be passed to memoised children without re-rendering.
 */

import { useCallback } from "react";
import { hapticImpact, hapticNotify, hapticSelection } from "../lib/max-sdk";

export function useHaptics() {
  return {
    selection: useCallback(hapticSelection, []),
    notify: useCallback(hapticNotify, []),
    impact: useCallback(hapticImpact, []),
  };
}
