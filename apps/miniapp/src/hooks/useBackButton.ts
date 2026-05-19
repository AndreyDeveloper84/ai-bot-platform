/**
 * Wire MAX BackButton to the router's `navigate(-1)` for every screen
 * except the root.
 *
 * Per skill-ref Part 6 Pattern 2:
 *   - Show on every screen except root
 *   - Wire to client-side router back, never close the app silently
 *
 * Usage:
 *   useBackButton();  // root — hides
 *   useBackButton({ onBack: () => navigate(-1) });  // any other screen
 */

import { useEffect } from "react";
import { onBackButton, setBackButton } from "../lib/max-sdk";

interface Options {
  /** If omitted, BackButton is hidden. */
  onBack?: () => void;
}

export function useBackButton(opts: Options = {}): void {
  const { onBack } = opts;
  useEffect(() => {
    if (!onBack) {
      setBackButton(false);
      return;
    }
    setBackButton(true);
    const unsubscribe = onBackButton(onBack);
    return () => {
      unsubscribe();
      setBackButton(false);
    };
  }, [onBack]);
}
