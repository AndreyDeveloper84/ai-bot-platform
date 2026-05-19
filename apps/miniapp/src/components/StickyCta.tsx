/**
 * Sticky bottom CTA — replaces MAX `MainButton` (not available per
 * memory + skill-ref Part 6).
 *
 * Disabled state shows the button greyed, not hidden (UX rule).
 */

import type { ReactNode } from "react";

interface Props {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}

export function StickyCta({ onClick, disabled, children }: Props) {
  return (
    <div className="cta-bar" role="region" aria-label="Действие">
      <button
        type="button"
        className="cta-bar__button"
        onClick={onClick}
        disabled={disabled}
      >
        {children}
      </button>
    </div>
  );
}
