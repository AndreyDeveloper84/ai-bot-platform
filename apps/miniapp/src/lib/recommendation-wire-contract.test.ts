/**
 * Третье звено golden-цепочки: потребитель принимает свой же образец.
 *
 * Форму диктует `__fixtures__/recommendation-wire-v1.json` — он лежит
 * здесь, на стороне потребителя, намеренно. Серверная половина читает
 * ТОТ ЖЕ файл (`tests/contracts/test_recommendation_wire_contract.py`),
 * поэтому «мы договорились» перестаёт быть словами: расхождение половин
 * краснит билд, а не доезжает до человека пустой полкой.
 *
 * Дефект, ради которого это заведено (DRF-1626), был не в форме, а в
 * проводе: транзит ходил на легаси-ручку `layer_1/2/3`, полка написана
 * против границы §9.4, валидатор отвергал ответ целиком и `picks`
 * оставался пустым — при том, что 55 живых вызовов из 56 отвечали 200.
 *
 * Учить полку принимать обе формы запрещено владельцем. Здесь это
 * запрещено устройством: образец один.
 */

import { describe, expect, it } from "vitest";

import { decisionContractViolation } from "./api";
import golden from "./__fixtures__/recommendation-wire-v1.json";

/** Свежая копия — мутации ниже не должны течь между проверками. */
const sample = (): Record<string, unknown> =>
  JSON.parse(JSON.stringify(golden)) as Record<string, unknown>;

describe("golden-цепочка: потребитель и сервер читают один образец", () => {
  it("образец непуст — иначе «нарушений нет» ничего не значит", () => {
    const data = sample().data as { ordered: unknown[] };
    expect(data.ordered.length).toBeGreaterThan(0);
  });

  it("потребитель принимает образец целиком", () => {
    expect(decisionContractViolation(sample())).toBeNull();
  });

  it("легаси-форма отвергается — и это ровно тот случай, что был на проде", () => {
    const legacy = {
      data: {
        layer_1_your_places: [],
        layer_2_ayla_picks: [{ master_id: "mst-1", reasoning_text: "20 минут от тебя" }],
        layer_3_explore: [],
      },
    };

    const violation = decisionContractViolation(legacy);

    expect(violation).not.toBeNull();
    // Сообщение обязано назвать сломанное, а не сказать «что-то не так».
    // Названо `resolver_spec_version`, а не `ordered`, потому что версия
    // проверяется ПЕРВОЙ — и это правильный порядок: у ответа неизвестной
    // мажорной версии разбирать `ordered` бессмысленно.
    expect(violation).toContain("resolver_spec_version");
  });

  it.each([
    ["конверт", (d: Record<string, unknown>) => d.data],
    [
      "неизвестная мажорная версия",
      (d: Record<string, unknown>) => {
        const data = d.data as Record<string, unknown>;
        data.resolver_spec_version = "2.0.0";
        return d;
      },
    ],
    [
      "kind вне закрытого множества",
      (d: Record<string, unknown>) => {
        const data = d.data as { ordered: { candidate: { kind: string } }[] };
        data.ordered[0]!.candidate.kind = "TENANT";
        return d;
      },
    ],
    [
      "фраза для человека в ответе границы",
      (d: Record<string, unknown>) => {
        const data = d.data as { ordered: Record<string, unknown>[] };
        data.ordered[0]!.reasoning_text = "20 минут от тебя";
        return d;
      },
    ],
  ])("сторож срабатывает: %s", (_name, mutate) => {
    // Проверка на срабатывание, а не на веру: валидатор, которого не
    // видели красным, — это `expect(true)` с длинным именем.
    expect(decisionContractViolation(mutate(sample()))).not.toBeNull();
  });
});
