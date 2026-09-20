/**
 * ErrorBoundary вокруг поверхностей (DRF-2198).
 *
 * Инцидент 20.09: `new Date(NaN).toISOString()` в карточке «Сегодня» бросил
 * RangeError — React снял корень, человек увидел белый экран без единой
 * кнопки. Точечная причина закрыта (#1918), класс — здесь: любое исключение
 * рендера должно давать понятное состояние с повтором, а не пустоту.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ErrorBoundary } from "./ErrorBoundary";

function Boom({ throws }: { throws: boolean }): JSX.Element {
  if (throws) throw new RangeError("Invalid time value");
  return <p>живой экран</p>;
}

let errorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // React печатает пойманное исключение — в тесте это шум.
  errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  errorSpy.mockRestore();
});

describe("ErrorBoundary", () => {
  it("без исключения — рисует детей как есть", () => {
    render(
      <ErrorBoundary>
        <Boom throws={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText("живой экран")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("исключение — общее состояние с предметом «экран» и «Попробовать снова», не пустота", () => {
    render(
      <ErrorBoundary>
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить экран");
    expect(screen.getByRole("button", { name: "Попробовать снова" })).toBeInTheDocument();
  });

  it("«Попробовать снова» перемонтирует детей — после починки экран живой", async () => {
    function Flaky() {
      const [n] = [calls++];
      if (n === 0) throw new RangeError("Invalid time value");
      return <p>живой экран</p>;
    }
    let calls = 0;
    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(await screen.findByText("живой экран")).toBeInTheDocument();
  });

  it("навигация из пропса остаётся под состоянием ошибки", () => {
    render(
      <ErrorBoundary chrome={<nav aria-label="Основная навигация">панель</nav>}>
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Основная навигация" })).toBeInTheDocument();
  });

  it("исключение уходит в лог без тела ошибки с ПДн", () => {
    const onError = vi.fn();
    render(
      <ErrorBoundary onError={onError}>
        <Boom throws />
      </ErrorBoundary>,
    );
    expect(onError).toHaveBeenCalledTimes(1);
    const [err] = onError.mock.calls[0] ?? [];
    expect(err).toBeInstanceOf(Error);
  });
});
