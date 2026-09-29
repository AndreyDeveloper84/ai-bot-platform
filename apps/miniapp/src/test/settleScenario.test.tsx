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
import { act, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { settleScenario } from "./settleScenario";

type Via = "microtasks" | "nestedTimers" | "effectAfterLoad" | "longTimer";

function LateCaller({ call, via }: { call: () => void; via: Via }) {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    if (via === "microtasks") {
      void Promise.resolve()
        .then(() => Promise.resolve())
        .then(call);
    } else if (via === "nestedTimers") {
      setTimeout(() => setTimeout(() => setTimeout(call, 0), 0), 0);
    } else if (via === "longTimer") {
      // 60 с — за жизнь теста не наступит ни при какой нагрузке; снимается при
      // размонтировании. Прежние 500 мс держались на допущении «помощник
      // успевает быстрее» и под нагрузкой краснели без дефекта (DRF-2597).
      const id = setTimeout(call, 60_000);
      return () => clearTimeout(id);
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
    render(<LateCaller call={call} via="longTimer" />);
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

describe("DRF-2609 — почему 18 локальных «дождаться» сведены сюда", () => {
  // Замер 29.09 (20 повторов на клетку). Прежняя форма `settle` — N×
  // `act(async () => {})`: к гонке DRF-2596 не уязвима (внутри `act` React
  // сливает эффекты своей очередью), но таймер 0 мс видит СЛУЧАЙНО — N=1:
  // 0/20, N=2: 3/20, N=4: 8/20, N=6: 6/20 — а вложенный таймер не видит
  // никогда. Отсюда «раунды на глаз»: больше раундов — больше шанс позеленеть.
  // Прежние `flush`/`flushSweep` — `act` + ОДИН оборот: глубина 1 — 20/20,
  // 2 — 5/20, 3 — 0/20. Но и там, где замер дал 0/20, прежняя форма изредка
  // видит лишний оборот (узел на глубине 2 падал ~1 раз из 25). Поэтому узлы
  // берут цепочку глубины 8 — прежним формам нужно 8 оборотов, а у них их
  // от одного до шести, — и зовут помощник с `min: 10`: предел глубины
  // `settleScenario` тем самым назван и снят узлом явно.
  const DEPTH = 8;
  const nested = (depth: number, call: () => void): (() => void) =>
    depth === 0 ? call : () => setTimeout(nested(depth - 1, call), 0);

  it("прежняя форма N×act не видит цепочку таймеров, settleScenario — видит", async () => {
    const call = vi.fn();
    function Chain() {
      useEffect(() => {
        nested(DEPTH, call)();
      }, []);
      return <h1>Экран</h1>;
    }
    render(<Chain />);
    for (let i = 0; i < 6; i += 1) {
      await act(async () => {});
    }
    expect(call).not.toHaveBeenCalled(); // empty-assert-ok: прежняя форма — пара к строке ниже

    await settleScenario({ min: 10 });

    expect(call).toHaveBeenCalledTimes(1);
  });

  it("прежние flush/flushSweep (один оборот) не видят цепочку таймеров, settleScenario — видит", async () => {
    const call = vi.fn();
    function Chain() {
      useEffect(() => {
        nested(DEPTH, call)();
      }, []);
      return <h1>Экран</h1>;
    }
    render(<Chain />);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(call).not.toHaveBeenCalled(); // empty-assert-ok: прежняя форма — пара к строке ниже

    await settleScenario({ min: 10 });

    expect(call).toHaveBeenCalledTimes(1);
  });
});
