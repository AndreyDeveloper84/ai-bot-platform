/**
 * Читатель происхождения порции — узлы на каждое значение (DRF-2371).
 *
 * Словарь провода: `provider` | `typical` | `unknown`. Узлы держат ещё два
 * случая, которых в словаре нет и которые страшнее всего: **поля нет** и
 * **значение незнакомо**. Оба обязаны читаться осторожно, иначе завтра
 * добавленное четвёртое значение поедет на экран как подтверждённое —
 * молча.
 */
import { describe, expect, it } from "vitest";

import {
  portionNeedsConfirmation,
  portionNumbersAreNamed,
  portionProvenanceOf,
  type PortionProvenance,
} from "./portion-provenance";

describe("portionProvenanceOf — все значения провода", () => {
  it("«provider» — вес назвал наблюдавший (распознаватель или сам человек)", () => {
    expect(portionProvenanceOf("provider")).toBe<PortionProvenance>("named");
  });

  it("«typical» — вес подставлен типовой величиной справочника", () => {
    expect(portionProvenanceOf("typical")).toBe<PortionProvenance>("typical");
  });

  it("«unknown» — вес не назвал никто", () => {
    expect(portionProvenanceOf("unknown")).toBe<PortionProvenance>("unnamed");
  });

  it("поля нет — «absent», и это НЕ «названо»", () => {
    expect(portionProvenanceOf(undefined)).toBe<PortionProvenance>("absent");
    expect(portionProvenanceOf(null)).toBe<PortionProvenance>("absent");
  });

  it("незнакомое значение — «unnamed», а не «названо» (fail-closed)", () => {
    // Ровно тот случай, из-за которого узел существует: в словарь добавили
    // четвёртое значение, а клиент старый.
    expect(portionProvenanceOf("confirmed")).toBe<PortionProvenance>("unnamed");
    expect(portionProvenanceOf("")).toBe<PortionProvenance>("unnamed");
    expect(portionProvenanceOf(42)).toBe<PortionProvenance>("unnamed");
    expect(portionProvenanceOf({ portion_source: "provider" })).toBe<PortionProvenance>(
      "unnamed",
    );
  });
});

describe("что признак разрешает показать", () => {
  it("названным числом считается только «named» (и переходный «absent»)", () => {
    expect(portionNumbersAreNamed("named")).toBe(true);
    // До половины B (DRF-2444) число в ответе без признака существует
    // только при названном весе — старый ответ показывается как прежде.
    expect(portionNumbersAreNamed("absent")).toBe(true);
    expect(portionNumbersAreNamed("typical")).toBe(false);
    expect(portionNumbersAreNamed("unnamed")).toBe(false);
  });

  it("ход «назови вес» нужен при «typical» и «unnamed»", () => {
    expect(portionNeedsConfirmation("typical")).toBe(true);
    expect(portionNeedsConfirmation("unnamed")).toBe(true);
    expect(portionNeedsConfirmation("named")).toBe(false);
    expect(portionNeedsConfirmation("absent")).toBe(false);
  });

  it("каждое значение разобрано: новое придётся описать здесь", () => {
    // Сторож полноты. Литеральный массив здесь ничего не доказывал бы:
    // подмножество union — законный `PortionProvenance[]`, и пятое
    // значение прошло бы молча. Карта же обязана покрыть union целиком —
    // добавление значения ломает `satisfies` ещё на типах.
    const EVERY = {
      named: true,
      typical: true,
      unnamed: true,
      absent: true,
    } satisfies Record<PortionProvenance, true>;
    const all = Object.keys(EVERY) as PortionProvenance[];
    const decided = all.filter(
      (p) => portionNumbersAreNamed(p) || portionNeedsConfirmation(p),
    );
    expect(decided).toEqual(all);
  });
});
