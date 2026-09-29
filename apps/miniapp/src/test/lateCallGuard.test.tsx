/**
 * DRF-2597 — положительный контроль сторожа запоздавшего вызова.
 *
 * Сторож с нулём срабатываний неотличим от неработающего: первый зонд
 * DRF-2597 молчал вместе со своим контролем, потому что его вывод глушился.
 * Эти узлы держат обе стороны на ТОМ ЖЕ механизме, что и `afterEach`
 * сторожа: запоздавший вызов назван с адресом, удержавшийся и законно
 * позванный самим тестом — нет.
 */
import { render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { drainLateCalls } from "./lateCallGuard";

function LoadsThenCalls({ call }: { call: () => void }) {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    // Эффект после загрузки — дальше одного оборота очереди (случай DRF-2596).
    setTimeout(() => setTimeout(() => setLoaded(true), 0), 0);
  }, []);
  return (
    <>
      <h1>Экран</h1>
      {loaded ? <Child onMount={call} /> : null}
    </>
  );
}

function Child({ onMount }: { onMount: () => void }) {
  useEffect(onMount, [onMount]);
  return null;
}

describe("сторож запоздавшего вызова — положительный контроль", () => {
  it("запоздавший запрещённый вызов назван с адресом строки", async () => {
    const call = vi.fn();
    render(<LoadsThenCalls call={call} />);
    expect(await screen.findByRole("heading", { name: "Экран" })).toBeInTheDocument();
    expect(call).not.toHaveBeenCalled(); // empty-assert-ok: предмет узла — запоздание

    const late = await drainLateCalls();

    expect(late).toEqual([expect.stringMatching(/lateCallGuard\.test\.tsx:\d+ \.not\.toHaveBeenCalled$/)]);
    expect(call).toHaveBeenCalledTimes(1);
  });

  it("то же для `.not.toHaveBeenCalledWith` — по аргументам", async () => {
    const call = vi.fn();
    render(<LoadsThenCalls call={() => call("чужой салон")} />);
    expect(await screen.findByRole("heading", { name: "Экран" })).toBeInTheDocument();
    expect(call).not.toHaveBeenCalledWith("чужой салон"); // empty-assert-ok: предмет узла — запоздание

    const late = await drainLateCalls();

    expect(late).toEqual([expect.stringMatching(/\.not\.toHaveBeenCalledWith$/)]);
  });

  it("удержавшийся запрет не назван", async () => {
    const call = vi.fn();
    render(<h1>Тихо</h1>);
    expect(screen.getByRole("heading", { name: "Тихо" })).toBeInTheDocument();
    expect(call).not.toHaveBeenCalled(); // empty-assert-ok: пара к первому узлу

    expect(await drainLateCalls()).toEqual([]);
  });

  it("вызов, который тест делает сам дальше по сценарию, — не нарушение", async () => {
    const send = vi.fn();
    render(<button onClick={() => send()}>Подтвердить</button>);
    expect(send).not.toHaveBeenCalled(); // empty-assert-ok: «до подтверждения»
    screen.getByRole("button", { name: "Подтвердить" }).click();
    expect(send).toHaveBeenCalledTimes(1);

    expect(await drainLateCalls()).toEqual([]);
  });
});
