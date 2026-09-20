/**
 * Универсальные системные состояния мастерских экранов — макет DRF-1181 п.10
 * (DRF-2157, М-6). Один компонент, один словарь, тексты дословно:
 *
 *   loading      — скелет (без слов)
 *   refreshing   — «Обновляем…»
 *   empty        — текст экрана (у «Сегодня» — «На сегодня записей нет»)
 *   offline      — «Нет подключения» · «Показаны последние данные»
 *   stale        — «Расписание могло измениться»
 *   load_error   — с данными: «Не удалось обновить» · «Показаны последние данные»;
 *                  первичная: «Не удалось загрузить» + [Попробовать снова]
 *   forbidden    — «Недостаточно прав» · «Это действие недоступно»
 *   pending      — «Проверяем результат» · «Не удалось подтвердить, сохранилось
 *                  ли изменение.» + [Проверить снова] (троттл 10 с, блок в полёте)
 *   conflict     — «Это время занято» + [Выбрать другое время]
 *
 * Словарь экспортируется как константы — сторож на дословность здесь же.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import {
  RECHECK_MIN_INTERVAL_MS,
  SYSTEM_STATE_COPY,
  SystemState,
} from "./SystemState";

afterEach(() => {
  vi.useRealTimers();
});

describe("словарь — дословно по DRF-1181 п.10", () => {
  it("тексты макета зафиксированы константами", () => {
    expect(SYSTEM_STATE_COPY.refreshing).toBe("Обновляем…");
    expect(SYSTEM_STATE_COPY.offline).toEqual({
      title: "Нет подключения",
      body: "Показаны последние данные",
    });
    expect(SYSTEM_STATE_COPY.stale).toEqual({ title: "Расписание могло измениться" });
    expect(SYSTEM_STATE_COPY.load_error).toEqual({
      withData: { title: "Не удалось обновить", body: "Показаны последние данные" },
      initial: { title: "Не удалось загрузить", retry: "Попробовать снова" },
      booking: { title: "Не удалось загрузить запись", retry: "Проверить снова" },
    });
    expect(SYSTEM_STATE_COPY.forbidden).toEqual({
      title: "Недостаточно прав",
      body: "Это действие недоступно",
    });
    expect(SYSTEM_STATE_COPY.pending).toEqual({
      title: "Проверяем результат",
      body: "Не удалось подтвердить, сохранилось ли изменение.",
      recheck: "Проверить снова",
    });
    expect(SYSTEM_STATE_COPY.conflict).toEqual({
      title: "Это время занято",
      cta: "Выбрать другое время",
    });
    expect(RECHECK_MIN_INTERVAL_MS).toBe(10_000);
  });
});

describe("loading / refreshing / empty", () => {
  it("loading — скелет без слов, aria-busy", () => {
    render(<SystemState kind="loading" />);
    const el = screen.getByRole("status");
    expect(el).toHaveAttribute("aria-busy", "true");
    expect(el.textContent?.trim()).toBe("");
  });

  it("refreshing — «Обновляем…»", () => {
    render(<SystemState kind="refreshing" />);
    expect(screen.getByRole("status")).toHaveTextContent("Обновляем…");
  });

  it("empty — текст экрана, без кнопок (кнопка «Создать запись» — с М-3)", () => {
    render(<SystemState kind="empty" text="На сегодня записей нет" />);
    expect(screen.getByRole("status")).toHaveTextContent("На сегодня записей нет");
    expect(screen.queryByRole("button")).toBeNull();
  });
});

describe("offline / stale / load_error", () => {
  it("offline — «Нет подключения» · «Показаны последние данные», role=status", () => {
    render(<SystemState kind="offline" />);
    const el = screen.getByRole("status");
    expect(el).toHaveTextContent("Нет подключения");
    expect(el).toHaveTextContent("Показаны последние данные");
  });

  it("stale — «Расписание могло измениться»", () => {
    render(<SystemState kind="stale" />);
    expect(screen.getByRole("status")).toHaveTextContent("Расписание могло измениться");
  });

  it("load_error с данными — «Не удалось обновить» · «Показаны последние данные» + повтор", async () => {
    const onRetry = vi.fn();
    render(<SystemState kind="load_error" err={new Error("x")} hasData onRetry={onRetry} />);
    const el = screen.getByRole("status");
    expect(el).toHaveTextContent("Не удалось обновить");
    expect(el).toHaveTextContent("Показаны последние данные");
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("load_error первичная — «Не удалось загрузить» + [Попробовать снова], role=alert", async () => {
    const onRetry = vi.fn();
    render(<SystemState kind="load_error" err={new Error("x")} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить");
    await userEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
    // Старых текстов нет.
    expect(screen.queryByText(/Не получилось загрузить/)).toBeNull();
    expect(screen.queryByText(/Что-то у нас не получается/)).toBeNull();
  });

  it("load_error для записи (§61 п.3) — «Не удалось загрузить запись» + [Проверить снова]", async () => {
    const onRetry = vi.fn();
    render(<SystemState kind="load_error" what="booking" err={new Error("x")} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить запись");
    await userEvent.click(screen.getByRole("button", { name: "Проверить снова" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("повтор заблокирован, пока busy", () => {
    render(<SystemState kind="load_error" err={new Error("x")} onRetry={() => {}} busy />);
    expect(screen.getByRole("button", { name: "Попробовать снова" })).toBeDisabled();
  });

  it("403 внутри load_error → «Недостаточно прав», без кнопки повтора", () => {
    render(
      <SystemState kind="load_error" err={new ApiError(403, "forbidden", "nope")} onRetry={() => {}} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Недостаточно прав");
    expect(screen.getByRole("alert")).toHaveTextContent("Это действие недоступно");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("отказ транспорта (DRF-1893) — возврат в MAX, а не повтор", () => {
    render(
      <SystemState
        kind="load_error"
        err={new ApiError(401, "no_init_data", "missing")}
        onRetry={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "Попробовать снова" })).toBeNull();
    expect(screen.queryByText("Не удалось загрузить")).toBeNull();
  });
});

describe("forbidden / conflict", () => {
  it("forbidden — «Недостаточно прав» · «Это действие недоступно»", () => {
    render(<SystemState kind="forbidden" />);
    const el = screen.getByRole("alert");
    expect(el).toHaveTextContent("Недостаточно прав");
    expect(el).toHaveTextContent("Это действие недоступно");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("conflict — «Это время занято» + [Выбрать другое время]", async () => {
    const onPickAnother = vi.fn();
    render(<SystemState kind="conflict" onPickAnother={onPickAnother} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Это время занято");
    await userEvent.click(screen.getByRole("button", { name: "Выбрать другое время" }));
    expect(onPickAnother).toHaveBeenCalledTimes(1);
  });
});

describe("pending — «Проверяем результат» + «Проверить снова», троттл 10 с", () => {
  it("тексты словаря; тело можно заменить текстом другого макета (DRF-1185)", () => {
    render(<SystemState kind="pending" onRecheck={() => {}} />);
    const el = screen.getByRole("status");
    expect(el).toHaveTextContent("Проверяем результат");
    expect(el).toHaveTextContent("Не удалось подтвердить, сохранилось ли изменение.");
    expect(screen.getByRole("button", { name: "Проверить снова" })).toBeEnabled();
  });

  it("тело по DRF-1185 через body", () => {
    render(
      <SystemState
        kind="pending"
        body="Не удалось получить актуальное состояние записи."
        onRecheck={() => {}}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Не удалось получить актуальное состояние записи.",
    );
    expect(screen.queryByText("Не удалось подтвердить, сохранилось ли изменение.")).toBeNull();
  });

  it("клик → onRecheck; повтор не чаще раза в 10 с; в полёте (busy) — заблокирована", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const onRecheck = vi.fn();
    const { rerender } = render(<SystemState kind="pending" onRecheck={onRecheck} />);
    const btn = screen.getByRole("button", { name: "Проверить снова" });
    await user.click(btn);
    expect(onRecheck).toHaveBeenCalledTimes(1);
    expect(btn).toBeDisabled();
    await user.click(btn);
    expect(onRecheck).toHaveBeenCalledTimes(1);

    await act(async () => {
      vi.advanceTimersByTime(RECHECK_MIN_INTERVAL_MS + 50);
    });
    expect(screen.getByRole("button", { name: "Проверить снова" })).toBeEnabled();

    // Пока запрос в полёте — заблокирована независимо от троттла.
    rerender(<SystemState kind="pending" onRecheck={onRecheck} busy />);
    expect(screen.getByRole("button", { name: "Проверить снова" })).toBeDisabled();
  });
});
