/**
 * iOS-style toggle — Tier 1 Priority 6 R2 / R4 (Profile tab, deferred
 * Variant 3).
 *
 * Spec: `docs/screens/customer-profile-flow.md` §11.3 (toggle variant
 * picked over checkbox + radio) + §13 WCAG inline (2.5.8 target size
 * + 4.1.2 role + 1.4.3 contrast + 2.3.3 reduced motion).
 *
 * # Why an inline SVG-free toggle
 *
 * `StatusBadge` precedent — emoji + 3rd-party icon libs are 30+ KB
 * raw for trivial glyphs; a CSS-only toggle is ~50 LOC + 0 KB icon
 * cost. Browsers + MAX webview render `prefers-reduced-motion` from
 * CSS reliably, so the handle slide is gated there.
 */

interface Props {
  /** Current toggle state. */
  checked: boolean;
  /** Called when user taps. */
  onChange: (next: boolean) => void;
  /** Accessible label — REQUIRED (WCAG 4.1.2). */
  ariaLabel: string;
  /** Disable interaction (e.g. while a write is in flight). */
  disabled?: boolean;
  /** Optional id for `aria-describedby` linkage from a sibling label. */
  id?: string;
}

export function ToggleSwitch({
  checked,
  onChange,
  ariaLabel,
  disabled,
  id,
}: Props) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel}
      aria-disabled={disabled ? "true" : undefined}
      id={id}
      className={`profile-toggle${checked ? " profile-toggle--on" : ""}${
        disabled ? " profile-toggle--disabled" : ""
      }`}
      onClick={() => {
        if (disabled) return;
        onChange(!checked);
      }}
    >
      <span className="profile-toggle__track" aria-hidden="true" />
      <span className="profile-toggle__thumb" aria-hidden="true" />
    </button>
  );
}
