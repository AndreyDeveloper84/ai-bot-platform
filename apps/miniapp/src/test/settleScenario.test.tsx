/**
 * DRF-2597 — `settleScenario` дожидается запоздавшего вызова, а проверка
 * «сразу после» — нет. Каждый узел — пара: в прежний момент замера вызова
 * ещё нет (там `.not.toHaveBeenCalled()` прошёл бы), после помощника — есть.
 *
 * Что считается «запоздавшим», зависит от момента. `findBy*` сам отпускает
 * тест через `setTimeout(0)` (asyncWrapper RTL), поэтому к нему микрозадачи и
 * один таймер 0 мс уже слиты — опаздывает то, что дальше одного оборота
 * очереди. Сразу после синхронного `render`/`getBy` опаздывают уже микрозадачи.
 */
import { render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { settleScenario } from "./settleScenario";

type Via = "microtasks" | "nestedTimers" | "effectAfterLoad" | "timer500";

function LateCaller({ call, via }: { call: () => void; via: Via }) {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (via === "microtasks") {
      void Promise.resolve()
        .then(() => Promise.resolve())
        .then(call);
    } else if (via === "nestedTimers") {
      setTimeout(() => setTimeout(() => setTimeout(call, 0), 0), 0);
    } else if (via === "timer500") {
      setTimeout(call, 500);
    } else {
      setTimeout(() => setTimeout(() => setLoaded(true), 0), 0);
    }
  }, [call, via]);
  return (
    <>
      <h1>Экран</h1>
      {loaded ? <Child onMount={call} /> : null}
    </>
  );
}

function Child({ onMount }: { onMount: () => void }) {
  useEffect(onMount, [onMount]);
  return <p>загружено</p>;
}

describe("settleScenario — запоздавший запрещённый вызов становится виден", () => {
  it("после синхронного момента: цепочка микрозадач", async () => {
    const call = vi.fn();
    render(<LateCaller call={call} via="microtasks" />);
    expect(screen.getByRole("heading", { name: "Экран" })).toBeInTheDocument();
    expect(call).not.toHaveBeenCalled(); // empty-assert-ok: прежний момент замера — пара к строке ниже

    await settleScenario();

    expect(call).toHaveBeenCalledTimes(1);
  });

  it.each(["nestedTimers", "effectAfterLoad"] as const)(
    "после findBy: %s — дальше одного оборота очереди",
    async (via) => {
      const call = vi.fn();
      render(<LateCaller call={call} via={via} />);
      expect(await screen.findByRole("heading", { name: "Экран" })).toBeInTheDocument();
      expect(call).not.toHaveBeenCalled(); // empty-assert-ok: прежний момент замера — пара к строке ниже

      await settleScenario();

      expect(call).toHaveBeenCalledTimes(1);
    },
  );

  it("предел: вызов с настоящей задержкой по часам помощник не дожидается", async () => {
    const call = vi.fn();
    render(<LateCaller call={call} via="timer500" />);
    expect(screen.getByRole("heading", { name: "Экран" })).toBeInTheDocument();

    await settleScenario();

    // Предел назван, а не спрятан: дебаунс и задержки — фейковые таймеры узла.
    expect(call).not.toHaveBeenCalled(); // empty-assert-ok: узел предела помощника
  });

  it("тихий экран отпускает быстро", async () => {
    render(<h1>Тихо</h1>);
    const started = performance.now();
    await settleScenario();
    expect(screen.getByRole("heading", { name: "Тихо" })).toBeInTheDocument();
    expect(performance.now() - started).toBeLessThan(1000);
  });
});
