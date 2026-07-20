/**
 * Shared sheet keyboard navigation (#953 — centralized Escape / focus
 * trap, previously duplicated per sheet component).
 *
 * - Escape closes the sheet — EXCEPT while `closeDisabled` (a request
 *   in flight must not strand its status view);
 * - Tab / Shift+Tab cycle across the currently-ENABLED buttons and
 *   links inside the dialog (focus trap, WCAG 2.1.2).
 *
 * Used by `PersonalDataSheets` chrome and the booking-detail cancel
 * modal.
 */
import { useEffect } from "react";

interface Options {
  onClose: () => void;
  /** True while a request is in flight — Escape is ignored. */
  closeDisabled?: boolean;
}

export function useSheetKeyNav(
  dialogRef: React.RefObject<HTMLElement | null>,
  { onClose, closeDisabled = false }: Options,
): void {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        if (!closeDisabled) onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusables = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          "button:not([disabled]), a[href]",
        ),
      );
      if (focusables.length === 0) return;
      const active = document.activeElement as HTMLElement | null;
      const idx = active ? focusables.indexOf(active) : -1;
      if (e.shiftKey) {
        if (idx <= 0) {
          e.preventDefault();
          focusables[focusables.length - 1]?.focus();
        }
      } else if (idx === focusables.length - 1) {
        e.preventDefault();
        focusables[0]?.focus();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [dialogRef, onClose, closeDisabled]);
}
