/**
 * Сторож запоздавшего запрещённого вызова (DRF-2597) — setup dom-набора.
 *
 * `expect(x).not.toHaveBeenCalled()` утверждает отсутствие вызова лишь К
 * МОМЕНТУ проверки. Регрессия, которая делает запрещённый вызов на оборот
 * очереди позже (обработчик после `await`, эффект после загрузки, таймер
 * 0 мс), проходит такую проверку зелёной. Сторож запоминает каждый ПРОШЕДШИЙ
 * `.not` и в `afterEach`, пока компонент ещё смонтирован, даёт сценарию
 * улечься (`settleScenario`) и повторяет ту же проверку. Провалилась — тест
 * падает с адресом строки.
 *
 * Вызов, который тест делает сам дальше по сценарию («без подтверждения не
 * отправлено → нажать → отправлено»), законен: такая запись к концу теста уже
 * не держится и сторожем пропускается. Падает только вызов, случившийся
 * ПОСЛЕ конца тела теста.
 *
 * Решение: обёртка `chai.util.overwriteMethod`, а не подмена матчеров через
 * `expect.extend`. Подмена заменила бы штатные сообщения об ошибке ВСЕМУ
 * набору — сторож против слабых проверок ослабил бы диагностику остальных.
 * Здесь матчеры прежние, сторож лишь наблюдает.
 *
 * Пределы (замер 29.09 на `b34e495e`):
 * * вызов с НАСТОЯЩЕЙ задержкой (дебаунс, `setTimeout(…, 500)`) не виден —
 *   см. предел `settleScenario`;
 * * наблюдаются `toHaveBeenCalled`/`toBeCalled` и `toHaveBeenCalledWith`/
 *   `toBeCalledWith`; `.not.toHaveBeenCalledTimes` и прочие — нет;
 * * не видит вызовы, чью историю съели раньше него: `mockRestore`/`mockClear`
 *   того же мока в теле теста после `.not` (2 места из 143) и
 *   `vi.useRealTimers()` в `afterEach` файла (2 файла из 65 с `.not`) —
 *   хуки файла идут раньше хуков setup;
 * * только dom-набор: у node-набора нет DOM, и успокоение не определено.
 *   Node-набор (48 файлов) НЕ охвачен; `.not.toHaveBeenCalled` там 12 мест,
 *   все в `lib/customer-booking.test.ts` — «разбор не сорит в консоль» после
 *   `await getCatalogBrowse()`. Фоновой работы после `return` в этом пути нет
 *   — проверено по трём телам (getCatalogBrowse → resolveCatalogPicks →
 *   loadRecommendations), `fetch*` в тесте заменены моками.
 *
 * Положительный контроль — `lateCallGuard.test.tsx`: без него сторож с нулём
 * срабатываний неотличим от неработающего (первый зонд DRF-2597 был именно
 * таким).
 */
import { afterEach, chai, expect } from "vitest";

import { settleScenario } from "./settleScenario";

const WATCHED = ["toHaveBeenCalled", "toBeCalled", "toHaveBeenCalledWith", "toBeCalledWith"] as const;

type Held = { method: string; subject: unknown; args: unknown[]; site: string };

let held: Held[] = [];
let replaying = false;

function siteOf(): string {
  const frames = (new Error().stack ?? "").split("\n");
  const frame = frames.find((l) => /\.test\.tsx?:\d+/.test(l) && !l.includes("lateCallGuard.ts"));
  const m = frame?.match(/(src[\\/][^\s():]+\.test\.tsx?):(\d+)/);
  return m ? `${m[1]!.replace(/\\/g, "/")}:${m[2]}` : "(строка не определена)";
}

for (const method of WATCHED) {
  chai.util.overwriteMethod(
    chai.Assertion.prototype,
    method,
    (_super: (...a: unknown[]) => unknown) =>
      function (this: Chai.AssertionStatic, ...args: unknown[]) {
        const result = _super.apply(this, args);
        if (!replaying && chai.util.flag(this, "negate")) {
          held.push({ method, subject: chai.util.flag(this, "object"), args, site: siteOf() });
        }
        return result;
      },
  );
}

function stillHolds(h: Held): boolean {
  replaying = true;
  try {
    (expect(h.subject).not as unknown as Record<string, (...a: unknown[]) => void>)[h.method]!(...h.args);
    return true;
  } catch {
    return false;
  } finally {
    replaying = false;
  }
}

/** Проверить запомненные `.not` после успокоения; вернуть адреса нарушений. */
export async function drainLateCalls(): Promise<string[]> {
  const pending = held;
  held = [];
  // Законно позванное тестом позже — не нарушение: к концу теста запись не держится.
  const atEnd = pending.filter(stillHolds);
  if (atEnd.length === 0) return [];
  await settleScenario();
  return atEnd.filter((h) => !stillHolds(h)).map((h) => `${h.site} .not.${h.method}`);
}

afterEach(async () => {
  const late = await drainLateCalls();
  if (late.length > 0) {
    throw new Error(
      "DRF-2597: запрещённый вызов случился ПОСЛЕ проверки `.not` — она прошла рано и " +
        "не могла провалиться. Поставьте `await settleScenario()` перед ней или " +
        "назовите момент в строке.\n  " +
        late.join("\n  "),
    );
  }
});
