/**
 * Сторож-перепись: системные состояния мастерских экранов — только через
 * `SystemState` и его словарь (DRF-2157, М-6; макет DRF-1181 п.10).
 *
 * Строка-аналог («Данные могут быть неактуальны», «Не получилось загрузить»,
 * «Этот диалог не для вас», свой «Загружаем …» и т.п.) в исходнике экрана
 * `/master/*` или `/solo/*` — это второй словарь, и его быть не должно.
 *
 * # Baseline, который не переживёт свою причину
 *
 * Экраны, ещё не переписанные, перечислены явно и снимаются листом М-6b.
 * Тест краснеет в ОБЕ стороны: аналог появился вне baseline — красный;
 * экран из baseline уже чист, но из baseline не убран — тоже красный, чтобы
 * список сокращался вместе с работой, а не жил сам по себе.
 *
 * Исходники читаются через `import.meta.glob(..., { query: "?raw" })`, как в
 * `screens/backContract.test.ts`.
 */
import { describe, expect, it } from "vitest";

const SCREEN_SOURCES = import.meta.glob("../../screens/Master*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** Фразы второго словаря. Кнопка сама по себе («Проверить снова») — не состояние. */
const ANALOGS: readonly string[] = [
  "Данные могут быть неактуальны",
  "Не получилось загрузить",
  "Не удалось загрузить",
  "Этот диалог не для вас",
  "Нет сети",
  "Что-то у нас не получается",
  "Проверьте интернет",
  "Загружаем ",
  "Проверяем результат",
  "Недостаточно прав",
];

/**
 * Снимается листом М-6b: экраны вне раздела Сегодня · Расписание · Детали,
 * ещё живущие своими текстами. Убирай строку, как только экран переписан —
 * иначе тест напомнит.
 */
const BASELINE_M6B: ReadonlySet<string> = new Set([
  "MasterBillingScreen",
  "MasterConversationDetailScreen",
  "MasterConversationsScreen",
  "MasterCustomersScreen",
  "MasterDirectionsScreen",
  "MasterInternalChatListScreen",
  "MasterInternalChatThreadScreen",
  "MasterNotificationSettingsScreen",
  "MasterOnboardingScreen",
  "MasterPlaceScreen",
  "MasterProfileScreen",
  "MasterServiceSelectScreen",
  "MasterServicesScreen",
]);

function screenName(path: string): string {
  return path.replace(/^.*\//, "").replace(/\.tsx$/, "");
}

function analogsIn(src: string): string[] {
  // Комментарии не считаем: шапка файла может упоминать снятый текст.
  const code = src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  return ANALOGS.filter((a) => code.includes(a));
}

const screens = Object.entries(SCREEN_SOURCES)
  .filter(([path]) => !/\.test\.tsx$/.test(path))
  .map(([path, src]) => ({ name: screenName(path), src }));

describe("системные состояния мастерских экранов — один словарь (DRF-2157)", () => {
  it("исходники экранов найдены", () => {
    expect(screens.length).toBeGreaterThan(10);
  });

  it("Сегодня · Расписание · Детали записи — без строк-аналогов", () => {
    const rewritten = ["MasterDashboardScreen", "MasterScheduleScreen", "MasterBookingDetailScreen"];
    for (const name of rewritten) {
      const s = screens.find((x) => x.name === name);
      expect(s, `${name}.tsx не найден`).toBeDefined();
      expect(analogsIn(s!.src), `${name}: строки вне словаря SystemState`).toEqual([]);
    }
  });

  it("вне baseline — аналогов 0", () => {
    const offenders = screens
      .filter((s) => !BASELINE_M6B.has(s.name))
      .map((s) => ({ name: s.name, hits: analogsIn(s.src) }))
      .filter((s) => s.hits.length > 0);
    expect(offenders, "экран со своим словарём состояний — перепиши через SystemState").toEqual([]);
  });

  it("baseline не переживает свою причину: чистый экран из списка убран", () => {
    const stale = [...BASELINE_M6B].filter((name) => {
      const s = screens.find((x) => x.name === name);
      return s !== undefined && analogsIn(s.src).length === 0;
    });
    expect(stale, "экран переписан — убери его из BASELINE_M6B").toEqual([]);
    const missing = [...BASELINE_M6B].filter((name) => !screens.some((x) => x.name === name));
    expect(missing, "экрана больше нет — убери его из BASELINE_M6B").toEqual([]);
  });
});
