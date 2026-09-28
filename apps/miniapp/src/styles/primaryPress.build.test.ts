/**
 * §6-йота (решение владельца 28.09, п.4): «главная кнопка должна иметь такой
 * же визуальный отклик на нажатие, как второстепенная».
 *
 * Род отклика — как у `.btn-secondary:active`: смена фона при нажатии.
 * Значение — то, каким в словаре уже отзывались главные кнопки:
 * `--c-accent-pressed`. До правки `.btn-primary` (84 места, 33 файла) не
 * отзывался вовсе, ещё четыре главные кнопки экранов — тоже, а
 * `.records-card__action--primary` при нажатии получал второстепенный почти
 * белый фон под белым текстом.
 *
 * Перепись заморожена: всякий селектор с фоном акцента без `:active` обязан
 * стоять в списке «не кнопок» ниже — новая главная кнопка без отклика
 * краснеет по имени.
 *
 * jsdom стили не считает — правила читаются с диска.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "globals.css"), "utf-8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

type Rule = { selectors: string[]; body: string; index: number };

const RULES: Rule[] = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({
  selectors: (m[1] ?? "").split(",").map((s) => s.replace(/\s+/g, " ").trim()),
  body: m[2] ?? "",
  index: m.index ?? 0,
}));

function activeRules(selector: string): Rule[] {
  return RULES.filter((r) => r.selectors.includes(`${selector}:active`));
}

/** Главные кнопки словаря — фон `--c-accent`, это действие, а не состояние. */
const PRIMARIES = [
  ".btn-primary",
  ".cta-bar__button",
  ".ayla-btn--primary",
  ".wellness-dash__cta",
  ".salon-today__cta",
  ".m6-btn-primary",
  ".m6-compose__send",
  ".records-card__action--primary",
  ".records-empty__cta-primary",
  ".schedule-sheet__primary",
  ".catalog-empty__cta-primary",
  ".ayla-compose__send",
];

/** Фон акцента без отклика по праву: это не кнопки, а отметки и состояния. */
const NOT_BUTTONS = new Set([
  ".date-strip__day--active",
  ".slot-grid__cell--active",
  ".accepting-bookings__switch--on",
  ".booking-detail__dot",
  ".avatar-sheet__dot",
  ".schedule-month-dot",
  ".m6-bubble--right",
  ".internal-chat-list__filter--active",
  ".customer-slots__suggestion--active",
  ".customer-slots__cell--active",
  ".customer-slots__day-chip--active",
  ".wellness-dash__progress-fill",
  ".records-detail__primary-cta",
  ".records-detail__sticky-cta-msg",
  ".records-msg-modal__send",
  ".ayla-bubble--mine",
  ".setup-landing__bar-fill",
  ".system-state__dot",
  // DRF-2527: точка «несохранено» матрицы услуг вынесена из инлайна в класс —
  // та же отметка, что `.booking-detail__dot`, не кнопка.
  ".services-matrix__dirty-dot",
]);

describe("главная кнопка отзывается на нажатие, как второстепенная (§6-йота)", () => {
  it("положительная пара: у второстепенной отклик — смена фона", () => {
    const [secondary] = activeRules(".btn-secondary");
    expect(secondary?.body).toMatch(/background:\s*var\(--c-accent-subtle\)/);
  });

  it.each(PRIMARIES)("%s: при нажатии фон --c-accent-pressed", (selector) => {
    const rules = activeRules(selector);
    expect(rules, `${selector}:active не найден`).not.toHaveLength(0);
    expect(rules.at(-1)?.body).toMatch(/background:\s*var\(--c-accent-pressed\)/);
  });

  it("главное действие на карточке записи не наследует второстепенный отклик", () => {
    const [secondary] = activeRules(".btn-secondary");
    const [primary] = activeRules(".records-card__action--primary");
    // Специфичность равная (0,2,0): побеждает правило, стоящее позже.
    expect(primary?.index ?? -1).toBeGreaterThan(secondary?.index ?? Infinity);
  });

  it("перепись: всякий фон акцента без отклика — не кнопка из списка", () => {
    const withAccent = RULES.filter((r) =>
      /background:\s*var\(--c-accent\)\s*;/.test(r.body),
    ).flatMap((r) => r.selectors.filter((s) => !/:[a-z]|\[/.test(s)));
    const silent = withAccent
      .filter((s) => activeRules(s).length === 0 && !NOT_BUTTONS.has(s))
      .sort();
    expect(silent, "главная кнопка без отклика на нажатие").toEqual([]);
    // Охват непустой: иначе «ноль молчащих» совпадает с «ничего не прочитано».
    expect(withAccent.length).toBeGreaterThan(20);
  });
});
