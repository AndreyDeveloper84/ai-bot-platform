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

/**
 * Фразы второго словаря — как они стоят в исходнике, с открывающей кавычкой
 * литерала, чтобы не ловить доменные тексты («Не получилось загрузить фото…»
 * при загрузке файла — не состояние экрана). Кнопка сама по себе
 * («Проверить снова») — не состояние.
 */
const ANALOGS: readonly string[] = [
  '"Данные могут быть неактуальны',
  '"Не получилось загрузить',
  '"Не удалось загрузить ',
  '"Этот диалог не для вас',
  '"Нет сети',
  '"Что-то у нас не получается',
  '"Проверьте интернет',
  '"Загружаем ',
  '"Проверяем результат',
  '"Недостаточно прав',
  // Ruling §61 М-6 е: кнопка повтора загрузки — «Попробовать снова»; своя
  // подпись у экрана — второй словарь.
  '"Повторить"',
  // DRF-2194: клиентский StateError на мастерском экране — его словарь
  // («Не получилось загрузить…») сторож по тексту экрана не видит, ловим импорт.
  'from "../components/StateError"',
];

/**
 * Файлы Master*.tsx, которые не мастерская поверхность: клиентский выбор
 * мастера (/book/master). Их словарь — клиентский, здесь не судим.
 */
const CUSTOMER_SURFACE: ReadonlySet<string> = new Set(["MasterPickerScreen"]);

/**
 * Остаток baseline после М-6b: переписки мастера с клиентом и со студией —
 * НЕ переписываются, они под снятие (DRF-1255); строки уйдут вместе с
 * файлами, тест напомнит («экрана больше нет»). Всё остальное — через
 * SystemState.
 */
const BASELINE_M6B: ReadonlySet<string> = new Set([
  "MasterConversationDetailScreen", // под снятие DRF-1255
  "MasterConversationsScreen", // под снятие DRF-1255
  "MasterInternalChatListScreen", // под снятие DRF-1255
  "MasterInternalChatThreadScreen", // под снятие DRF-1255
]);

/**
 * Доменные фразы, похожие на состояние, но не состояние экрана: результат
 * действия (загрузка файла), не загрузка экрана. Ключ — имя экрана.
 */
const DOMAIN_ALLOW: Readonly<Record<string, readonly string[]>> = {
  MasterProfileScreen: ['"Не получилось загрузить фото'],
  // «Повторить» отправку профиля после отказа каталога — действие экрана,
  // не повтор загрузки (ошибку загрузки экран рисует общим SystemState).
  MasterPublicationScreen: ['"Повторить"'],
};

function screenName(path: string): string {
  return path.replace(/^.*\//, "").replace(/\.tsx$/, "");
}

function analogsIn(src: string, screen = ""): string[] {
  // Комментарии не считаем: шапка файла может упоминать снятый текст. Сначала
  // глушим «/*» внутри строковых литералов (accept="image/*"), иначе он открыл
  // бы ложный блочный комментарий и съел код до ближайшего «*/».
  const masked = src.replace(/"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'/g, (m) =>
    m.replace(/\/\*/g, "/ *"),
  );
  let code = masked.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  for (const allowed of DOMAIN_ALLOW[screen] ?? []) code = code.split(allowed).join("");
  return ANALOGS.filter((a) => code.includes(a));
}

const screens = Object.entries(SCREEN_SOURCES)
  .filter(([path]) => !/\.test\.tsx$/.test(path))
  .map(([path, src]) => ({ name: screenName(path), src }))
  .filter((s) => !CUSTOMER_SURFACE.has(s.name));

describe("сторож не слепнет от «/*» в строке", () => {
  it('accept="image/*" не открывает ложный комментарий', () => {
    const src = 'const a = <input accept="image/*" />;\nconst t = "Не получилось загрузить";\n{/* x */}';
    expect(analogsIn(src)).toEqual(['"Не получилось загрузить']);
  });
  it("настоящие комментарии не считаются", () => {
    const src = '/* "Не получилось загрузить" */\n// "Нет сети"\nconst t = 1;';
    expect(analogsIn(src)).toEqual([]);
  });
});

describe("системные состояния мастерских экранов — один словарь (DRF-2157)", () => {
  it("исходники экранов найдены", () => {
    expect(screens.length).toBeGreaterThan(10);
  });

  it("Сегодня · Расписание · Детали записи — без строк-аналогов", () => {
    const rewritten = ["MasterDashboardScreen", "MasterScheduleScreen", "MasterBookingDetailScreen"];
    for (const name of rewritten) {
      const s = screens.find((x) => x.name === name);
      expect(s, `${name}.tsx не найден`).toBeDefined();
      expect(analogsIn(s!.src, name), `${name}: строки вне словаря SystemState`).toEqual([]);
    }
  });

  it("вне baseline — аналогов 0", () => {
    const offenders = screens
      .filter((s) => !BASELINE_M6B.has(s.name))
      .map((s) => ({ name: s.name, hits: analogsIn(s.src, s.name) }))
      .filter((s) => s.hits.length > 0);
    expect(offenders, "экран со своим словарём состояний — перепиши через SystemState").toEqual([]);
  });

  it("baseline не переживает свою причину: чистый экран из списка убран", () => {
    const stale = [...BASELINE_M6B].filter((name) => {
      const s = screens.find((x) => x.name === name);
      return s !== undefined && analogsIn(s.src, s.name).length === 0;
    });
    expect(stale, "экран переписан — убери его из BASELINE_M6B").toEqual([]);
    const missing = [...BASELINE_M6B].filter((name) => !screens.some((x) => x.name === name));
    expect(missing, "экрана больше нет — убери его из BASELINE_M6B").toEqual([]);
  });
});
