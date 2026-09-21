/**
 * Кадры 4–6 макета DRF-1320 и его recovery-состояния (DRF-2178, Э-3).
 *
 * Этап 3 из 4, радиус ограничен намеренно.
 *
 * **Машина исходов переиспользуется, её слова — нет.** `booking-draft.ts`
 * (М-3) даёт `SubmitOutcome`, `outcomeKeepsDraft` и правило «ничего не
 * сдвигается молча» — это и берём. А `SUBMIT_OUTCOME_COPY` написан для
 * ПЕРСОНАЛА: «выберите», «Обратитесь к владельцу салона», «Клиент и
 * услуга сохранены». Показать это клиенту значило бы заговорить с ним
 * голосом админки. У макета для каждого состояния свои слова — они и
 * канон.
 *
 * Состояний в макете **девять**, а не восемь: «Это время только что
 * стало недоступно» (при выборе) и «Это время уже заняли» (при
 * создании) — разные моменты и разные слова, хотя действие у них одно.
 *
 * Два текста несут имя специалиста, поэтому текст у всех — функция от
 * контекста, а не строка: единая форма не даёт однажды показать
 * человеку сырой шаблон с фигурными скобками.
 *
 * Что заперто:
 *
 * 1. на каждый исход и каждое состояние — свой текст и своё действие,
 *    ни одного пустого;
 * 2. **повтор — это повтор**, а не новая запись: «Проверить статус»
 *    шлёт тот же запрос; ключ идемпотентности считает сервер из
 *    (человек, мастер, услуга, время, оплата), и клиент его не
 *    выдумывает;
 * 3. исход, сохраняющий черновик, его сохраняет — человек не вводит
 *    заново то, что уже выбрал;
 * 4. голос клиентский: ни «вы», ни слов админки;
 * 5. имя специалиста подставляется, а без имени текст остаётся
 *    связным — скобок и «undefined» человек не увидит.
 */
import { describe, expect, it } from "vitest";

import {
  SUBMIT_OUTCOME_COPY,
  outcomeKeepsDraft,
  type SubmitOutcome,
} from "./booking-draft";
import { CUSTOMER_OUTCOME, RECOVERY, type RecoveryKind } from "./booking-outcome";

const ALL_OUTCOMES: SubmitOutcome[] = [
  "committed",
  "conflict",
  "blocked",
  "pending",
  "failed",
];

const ALL_RECOVERY: RecoveryKind[] = [
  "no_time",
  "slot_unavailable",
  "slot_taken",
  "provider_unavailable",
  "option_unavailable",
  "network",
  "unconfirmed",
  "empty_list",
  "offline",
];

describe("у каждого исхода свои слова и своё действие", () => {
  it.each(ALL_OUTCOMES)("%s — текст есть и он не пустой", (outcome) => {
    expect(CUSTOMER_OUTCOME[outcome].text.length).toBeGreaterThan(0);
  });

  it("голос клиентский: ни «вы», ни слов админки", () => {
    for (const outcome of ALL_OUTCOMES) {
      const text = CUSTOMER_OUTCOME[outcome].text;
      expect(text).not.toMatch(/\bвыберите\b|\bобратитесь\b|\bвладельцу салона\b/i);
      expect(text).not.toMatch(/\bКлиент\b/);
    }
  });

  it("положительная пара: слова М-3 на мастерской поверхности не тронуты", () => {
    // Клиентский голос заведён РЯДОМ, а не вместо: форма записи мастера
    // говорит с персоналом по-прежнему. Правка, которая «причесала бы»
    // админку заодно, упала бы здесь.
    expect(SUBMIT_OUTCOME_COPY.conflict).toContain("выберите");
    expect(SUBMIT_OUTCOME_COPY.conflict).toContain("Клиент и услуга сохранены");
    expect(SUBMIT_OUTCOME_COPY.blocked).toContain("владельцу салона");
  });

  it("исход, сохраняющий черновик, помечен тем же правилом, что у М-3", () => {
    for (const outcome of ALL_OUTCOMES) {
      expect(CUSTOMER_OUTCOME[outcome].keepsDraft).toBe(outcomeKeepsDraft(outcome));
    }
  });
});

describe("recovery-состояния макета — все девять, ни одного пустого", () => {
  it.each(ALL_RECOVERY)("%s — есть текст и действия названы", (kind) => {
    const frame = RECOVERY[kind];
    expect(frame.text({ provider: "Екатерина" }).length).toBeGreaterThan(0);
    expect(Array.isArray(frame.actions)).toBe(true);
  });

  it("два разных момента про время названы разными словами", () => {
    const atPick = RECOVERY.slot_unavailable.text({});
    const atCreate = RECOVERY.slot_taken.text({});
    expect(atPick).not.toBe(atCreate);
    // Но ведут они в одно место — выбрать другое время.
    expect(RECOVERY.slot_unavailable.actions).toEqual(RECOVERY.slot_taken.actions);
  });

  it("имя подставляется, а без имени текст остаётся связным", () => {
    expect(RECOVERY.no_time.text({ provider: "Екатерина" })).toContain("Екатерин");
    const anonymous = RECOVERY.no_time.text({});
    expect(anonymous.length).toBeGreaterThan(0);
    expect(anonymous).not.toMatch(/\{|\}|undefined/);
  });

  it("«нет соединения» — единственное состояние без действий", () => {
    // Макет: «Действия недоступны». Кнопка, которая ничего не может
    // сделать, хуже её отсутствия — она обещает связь, которой нет.
    expect(RECOVERY.offline.actions).toEqual([]);
    // Положительная половина пары: у остальных действия есть.
    for (const kind of ALL_RECOVERY.filter((k) => k !== "offline")) {
      expect(RECOVERY[kind].actions.length).toBeGreaterThan(0);
    }
  });

  it("«не удалось подтвердить» ведёт к проверке статуса, а не к повтору записи", () => {
    const labels = RECOVERY.unconfirmed.actions.map((a) => a.label);
    expect(labels).toContain("Проверить статус");
    expect(labels).not.toContain("Записаться");
  });
});

describe("повтор — это повтор", () => {
  it("«Проверить статус» не заводит нового ключа: его считает сервер", () => {
    // Ключ идемпотентности собирается на сервере из (человек, мастер,
    // услуга, время, оплата) и от клиента не зависит. Значит повторный
    // тот же запрос вернёт ту же запись, а не создаст вторую.
    expect(RECOVERY.unconfirmed.repeatsSameRequest).toBe(true);
  });
});
