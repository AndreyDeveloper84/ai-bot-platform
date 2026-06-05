/**
 * Status badge — icon + Russian label paired (WCAG 1.4.1 colour-only fix).
 *
 * Spec: `docs/screens/customer-records-flow.md` §6 (R4 Status vocabulary)
 * + memory `project_records_voice_principles` (Q-R-2 caveat — emoji
 * rendering inconsistent across Android/iOS/MAX webview, so the icon
 * glyphs MUST be SVG, not emoji).
 *
 * The icon SVGs are inlined here (small + framework-agnostic) rather
 * than pulled from an icon library — the entire badge surface is 8
 * glyphs, and the alternative (an icon dep) is ~30 KB raw for what
 * costs us ~2 KB inline.
 *
 * Tints map to existing token CSS vars:
 *   sage    → --c-success (positive lifecycle outcome)
 *   muted   → --c-text-secondary (neutral end-state)
 *   warning → --c-warning (provider-cancelled — NOT sage, per founder
 *                          review 2026-05-26)
 */

import type { StatusIcon, StatusRendering, StatusTint } from "../lib/booking-status";

interface Props {
  rendering: StatusRendering;
  /** Optional override — wraps the badge in a non-default container. */
  className?: string;
}

/**
 * Per Tau §13 (WCAG inline): icon is `aria-hidden`, label carries the
 * accessible name via plain text. Tint maps to a CSS class suffix so
 * styling lives in one place (globals.css).
 */
export function StatusBadge({ rendering, className }: Props) {
  const { icon, label, tint } = rendering;
  return (
    <span
      className={`records-status-badge records-status-badge--${tint}${
        className ? ` ${className}` : ""
      }`}
    >
      <StatusGlyph icon={icon} />
      <span className="records-status-badge__label">{label}</span>
    </span>
  );
}

// ---------------------------------------------------------------------------
// Inline SVG glyph dispatcher. 16×16 viewBox; strokeWidth=2.
// ---------------------------------------------------------------------------

function StatusGlyph({ icon }: { icon: StatusIcon }) {
  const common = {
    width: 16,
    height: 16,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
    focusable: false,
  };
  switch (icon) {
    case "check":
      return (
        <svg {...common}>
          <path d="M3 8l3 3 7-7" />
        </svg>
      );
    case "calendar-arrow":
      return (
        <svg {...common}>
          <rect x="2" y="3" width="12" height="11" rx="1.5" />
          <path d="M2 7h12" />
          <path d="M6 1v3M10 1v3" />
          <path d="M9 11l2-1.5L9 8" />
        </svg>
      );
    case "x":
      return (
        <svg {...common}>
          <path d="M4 4l8 8M12 4l-8 8" />
        </svg>
      );
    case "alert":
      return (
        <svg {...common}>
          <path d="M8 1.5L14.5 13h-13z" />
          <path d="M8 6v3.5" />
          <circle cx="8" cy="11.5" r="0.6" fill="currentColor" stroke="none" />
        </svg>
      );
    case "check-double":
      return (
        <svg {...common}>
          <path d="M1.5 8l3 3 6-6" />
          <path d="M6.5 11l1 1 7-7" />
        </svg>
      );
    case "minus":
      return (
        <svg {...common}>
          <path d="M3 8h10" />
        </svg>
      );
    case "clock":
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.5" />
          <path d="M8 4.5V8l2.5 1.5" />
        </svg>
      );
    case "money-check":
      return (
        <svg {...common}>
          <rect x="2" y="3.5" width="12" height="9" rx="1.5" />
          <path d="M5 11.5l1.5 1.5 3-3" />
          <path d="M11 6h1.5" />
        </svg>
      );
    case "info":
    default:
      return (
        <svg {...common}>
          <circle cx="8" cy="8" r="6.5" />
          <path d="M8 7v4" />
          <circle cx="8" cy="5" r="0.6" fill="currentColor" stroke="none" />
        </svg>
      );
  }
}

/**
 * Convenience: subscript helper that maps a `StatusTint` to a colour
 * variable name. Used in `BookingCard` to colour the left rail / chip
 * accent — keeps the tint→colour mapping co-located with the badge.
 */
export function tintColourVar(tint: StatusTint): string {
  switch (tint) {
    case "sage":
      return "var(--c-success)";
    case "warning":
      return "var(--c-warning)";
    case "muted":
    default:
      return "var(--c-text-secondary)";
  }
}
