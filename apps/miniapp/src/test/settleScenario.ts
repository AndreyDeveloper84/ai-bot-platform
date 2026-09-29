/**
 * Дождаться, пока сценарий уляжется, — перед запретом «не позван» (DRF-2597).
 *
 * `expect(x).not.toHaveBeenCalled()` утверждает отсутствие вызова К ЭТОМУ
 * МОМЕНТУ. Сразу после `findBy*` / `waitFor` / клика момент — «появился
 * элемент», а запрещённый вызов, стоящий за следующим `await` (обработчик
 * после промиса, эффект после загрузки, таймер 0 мс), случится позже, и
 * проверка его не увидит: она не может провалиться. Запрет «за сценарий»
 * ставится после этого помощника.
 *
 * Как устроен: раунды `act` с одним поворотом очереди событий
 * (`setTimeout(…, 0)` — оборот цикла, а не ожидание по часам). Каждый раунд
 * сливает микрозадачи (цепочки `mockResolvedValue`), таймеры с нулевой
 * задержкой и отложенные эффекты React. Раунды идут, пока DOM не перестанет
 * меняться два раунда подряд, но не меньше `min` и не больше `max`.
 *
 * Предел по построению: вызов с НАСТОЯЩЕЙ задержкой (`setTimeout(…, 500)`,
 * дебаунс) помощник не дожидается — это ожидание по часам, и ему место в
 * фейковых таймерах конкретного узла, а не здесь.
 *
 * Второй предел — глубина чисто таймерной цепочки (DRF-2609, замер 29.09, 20
 * повторов): таймеры DOM не меняют, и тишина DOM отпускает помощника, пока
 * цепочка ещё идёт. Гарантированно — до глубины `min` (3): 20/20; глубина 4
 * — 13/20, 5 — 3/20, 8 — 0/20. Цепочку глубже `min` ждут `min` побольше
 * или фейковые таймеры узла.
 *
 * За пределом — ОТКАЗ, а не проход (DRF-2617). До DRF-2617 помощник молча
 * возвращал управление, и тест на глубокой таймерной цепочке зеленел, не
 * дождавшись работы. Теперь в момент выхода — по тишине DOM или по `max` —
 * он спрашивает `timerLedger`: ждут ли ещё короткие таймеры (не длиннее
 * `SHORT_TIMER_MS`). Ждут — падение с числом, колбэками и советом; про
 * `setInterval` (его не считаем) отказ говорит прямо. Условие ожидания НЕ
 * менялось — только громкость: замер 29.09 показал, что до предела не доходит
 * ни один настоящий тест, и платить перестройкой за молчащую дыру не стоит.
 */
import { act } from "@testing-library/react";

import {
  activeIntervals,
  describePendingShortTimers,
  pendingShortTimers,
  SHORT_TIMER_MS,
} from "./timerLedger";

export async function settleScenario({ min = 3, max = 12 } = {}): Promise<void> {
  let previous = document.body.innerHTML;
  let quiet = 0;
  for (let round = 0; round < max; round += 1) {
    await act(async () => {
      await new Promise<void>((resolve) => setTimeout(resolve, 0));
    });
    const current = document.body.innerHTML;
    quiet = current === previous ? quiet + 1 : 0;
    previous = current;
    if (round + 1 >= min && quiet >= 2) {
      refuseIfTimersPending(round + 1);
      return;
    }
  }
  refuseIfTimersPending(max);
}

function refuseIfTimersPending(rounds: number): void {
  const left = pendingShortTimers();
  if (left === 0) return;
  const intervals = activeIntervals();
  const lines = [
    `settleScenario: очередь коротких таймеров (≤ ${SHORT_TIMER_MS} мс) не пуста после ` +
      `${rounds} оборотов — ${left} шт. Сценарий не улёгся, и проверка после помощника ` +
      `прошла бы, не дождавшись работы. Передайте { min: … } больше глубины цепочки ` +
      `или фейковые часы узла.`,
    ...describePendingShortTimers().map((d) => `  ${d}`),
    `  setInterval помощник не считает; активных интервалов сейчас: ${intervals}.`,
  ];
  throw new Error(lines.join("\n"));
}
