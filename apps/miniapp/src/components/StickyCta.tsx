/**
 * Sticky bottom CTA — replaces MAX `MainButton` (not available per
 * memory + skill-ref Part 6).
 *
 * Disabled state shows the button greyed, not hidden (UX rule).
 *
 * DRF-1469 — панель разобрана на две части. Большинству экранов
 * по-прежнему нужен ровно один `StickyCta`, и для них ничего не
 * изменилось. Поверхности цели нужно держать в той же панели ещё и
 * выход с поверхности («Сменить режим»), поэтому контейнер `StickyBar`
 * и сама кнопка `StickyCtaButton` вынесены наружу: экран, которому мало
 * одного действия, складывает панель сам, не переобъявляя ни разметку
 * бара, ни разметку кнопки.
 */

import type { ReactNode } from "react";

interface Props {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
}

/**
 * Сама закреплённая панель. Область помечена `role="region"` с
 * постоянным именем «Действие»: тесты и скринридер находят панель по
 * ней независимо от того, что внутри.
 */
export function StickyBar({ children }: { children: ReactNode }) {
  return (
    <div className="cta-bar" role="region" aria-label="Действие">
      {children}
    </div>
  );
}

/** Главное действие панели — кнопка без бара вокруг неё. */
export function StickyCtaButton({ onClick, disabled, children }: Props) {
  return (
    <button
      type="button"
      className="cta-bar__button"
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}

export function StickyCta({ onClick, disabled, children }: Props) {
  return (
    <StickyBar>
      <StickyCtaButton onClick={onClick} disabled={disabled}>
        {children}
      </StickyCtaButton>
    </StickyBar>
  );
}
