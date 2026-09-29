/**
 * Учёт ожидающих коротких таймеров — чтобы `settleScenario` мог отличить
 * «сценарий улёгся» от «таймеры ещё идут» (DRF-2617). Setup dom-набора,
 * ставится ПЕРВЫМ — раньше, чем кто-либо запомнит свой `setTimeout`.
 *
 * У реальных таймеров нет вопроса «сколько ждут в очереди», поэтому
 * `globalThis.setTimeout`/`clearTimeout` обёрнуты счётчиком. На каждый таймер —
 * одна запись в `Map`: задержка и ссылка на колбэк. Стек НЕ снимается: захват
 * стека дороже самого таймера и сдвигал бы тот самый порядок, который помощник
 * ждёт. Источник называется только при отказе — по колбэку, лениво.
 *
 * Считаются только КОРОТКИЕ таймеры — задержка не больше `SHORT_TIMER_MS`.
 * Длинный таймер (дебаунс, `setTimeout(…, 500)`) — законное ожидание ВРЕМЕНИ;
 * его ждут фейковые часы узла, а не помощник.
 *
 * `setInterval` не считается: вечный интервал дал бы вечный отказ. Отказ
 * `settleScenario` называет число активных интервалов прямо, чтобы молчание
 * про исключение не заменило самого исключения.
 *
 * Под фейковыми таймерами vitest подменяет `setTimeout` своим — обёртка на это
 * время выключена сама собой, учёт не ведётся.
 */
import { beforeEach } from "vitest";

/**
 * Порог «короткого» таймера, мс. Причина числа: код приложения и библиотеки
 * тестов ставят «на следующий тик» задержки 0–1 мс (отложенная уборка
 * дневника, задержка `user-event` между действиями), а ближайшее законное
 * ожидание времени — опрос `waitFor` (50 мс) и дебаунсы (300 мс). 10 мс
 * отделяет первое от второго с запасом в обе стороны.
 */
export const SHORT_TIMER_MS = 10;

type Pending = { delay: number; callback: unknown };

const pending = new Map<unknown, Pending>();
const intervals = new Set<unknown>();

const realSetTimeout = globalThis.setTimeout;
const realClearTimeout = globalThis.clearTimeout;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;

function install(): void {
  const wrappedSetTimeout = function (callback: unknown, delay?: number, ...args: unknown[]) {
    const ms = Number(delay) || 0;
    let id: unknown;
    const run =
      typeof callback === "function"
        ? (...a: unknown[]) => {
            pending.delete(id);
            return (callback as (...x: unknown[]) => unknown)(...a);
          }
        : callback;
    id = (realSetTimeout as (...x: unknown[]) => unknown)(run, delay, ...args);
    if (ms <= SHORT_TIMER_MS) pending.set(id, { delay: ms, callback });
    return id;
  };
  const wrappedClearTimeout = function (id: unknown) {
    pending.delete(id);
    return (realClearTimeout as (x: unknown) => void)(id);
  };
  const wrappedSetInterval = function (...a: unknown[]) {
    const id = (realSetInterval as (...x: unknown[]) => unknown)(...a);
    intervals.add(id);
    return id;
  };
  const wrappedClearInterval = function (id: unknown) {
    intervals.delete(id);
    return (realClearInterval as (x: unknown) => void)(id);
  };
  Object.assign(globalThis, {
    setTimeout: wrappedSetTimeout,
    clearTimeout: wrappedClearTimeout,
    setInterval: wrappedSetInterval,
    clearInterval: wrappedClearInterval,
  });
}

install();

// Таймеры размонтированного прошлого теста — не предмет нынешнего.
beforeEach(resetTimerLedger);

/** Сколько коротких таймеров ещё ждут. */
export function pendingShortTimers(): number {
  return pending.size;
}

/** Сколько интервалов активно — их помощник не считает, но называет. */
export function activeIntervals(): number {
  return intervals.size;
}

/** Колбэки ожидающих коротких таймеров — словами, для отказа. Лениво. */
export function describePendingShortTimers(limit = 3): string[] {
  return [...pending.values()].slice(0, limit).map(({ delay, callback }) => {
    const fn = callback as { name?: string; toString?: () => string };
    const name = fn?.name && fn.name !== "run" ? fn.name : "";
    const text = (fn?.toString?.() ?? String(callback)).replace(/\s+/g, " ").slice(0, 80);
    return `${delay} мс: ${name || text}`;
  });
}

/** Сбросить учёт между тестами: таймеры размонтированного прошлого не в счёт. */
export function resetTimerLedger(): void {
  pending.clear();
  intervals.clear();
}
