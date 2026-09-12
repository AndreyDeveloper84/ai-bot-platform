/**
 * DRF-1319 D-1 — отказ входа называется одним именем на всех экранах.
 *
 * Инвентарь 1319-C: четыре экрана (каталог, услуга, мастер, окна) при
 * пустом `initData` показывали через `StateError` серверную строку
 * «missing Authorization header», а `HelloScreen` для того же 400 — «MAX
 * не передал данные для входа». Теперь копия одна (`lib/auth-error-copy.ts`).
 *
 * Положительная половина впереди: обычные ошибки (5xx, 403 без слага
 * входа, прочие 4xx с detail) рисуются как раньше — иначе тест зеленел бы
 * на компоненте, который на ЛЮБУЮ ошибку отвечает «не получилось войти».
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api";
import { AUTH_ERROR_COPY, AUTH_REFUSAL_SLUGS, authErrorCopy } from "../lib/auth-error-copy";
import { StateError } from "./StateError";

function renderErr(err: unknown) {
  render(<StateError err={err} onRetry={vi.fn()} />);
}

describe("StateError — обычные ошибки как раньше", () => {
  it("5xx → общая фраза, без detail", () => {
    renderErr(new ApiError(503, "http_error", "upstream down"));
    expect(screen.getByText("Что-то у нас не получается прямо сейчас.")).toBeTruthy();
    expect(screen.queryByText(/upstream down/)).toBeNull();
  });

  it("403 без слага входа → «раздел недоступен»", () => {
    renderErr(new ApiError(403, "forbidden", "no access"));
    expect(screen.getByText("Этот раздел сейчас недоступен.")).toBeTruthy();
  });

  it("прочие 4xx → detail сервера (это не отказ входа)", () => {
    renderErr(new ApiError(422, "validation_error", "Дата в прошлом"));
    expect(screen.getByText("Дата в прошлом")).toBeTruthy();
  });

  it("не ApiError → фраза про интернет", () => {
    renderErr(new TypeError("Failed to fetch"));
    expect(screen.getByText(/Проверьте интернет/)).toBeTruthy();
  });

  it("кнопка повтора есть всегда", () => {
    const onRetry = vi.fn();
    render(<StateError err={new ApiError(500, "x", "y")} onRetry={onRetry} />);
    screen.getByText("Попробовать снова").click();
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe("StateError — отказ входа тем же именем, что на HelloScreen", () => {
  it('400 malformed → «MAX не передал данные для входа», а не "missing Authorization header"', () => {
    renderErr(new ApiError(400, "malformed", "missing Authorization header"));
    expect(screen.getByText(authErrorCopy("malformed").title)).toBeTruthy();
    expect(screen.getByText(authErrorCopy("malformed").body)).toBeTruthy();
    expect(screen.queryByText(/missing Authorization header/)).toBeNull();
  });

  it("401 stale → «Сессия устарела»", () => {
    renderErr(new ApiError(401, "stale", "initData expired — reopen the Mini App"));
    expect(screen.getByText(authErrorCopy("stale").title)).toBeTruthy();
    expect(screen.queryByText(/reopen the Mini App/)).toBeNull();
  });

  it("500 server_misconfigured → точная копия, а не общая 5xx", () => {
    renderErr(new ApiError(500, "server_misconfigured", "MAX bot token not configured"));
    expect(screen.getByText(authErrorCopy("server_misconfigured").title)).toBeTruthy();
    expect(screen.queryByText("Что-то у нас не получается прямо сейчас.")).toBeNull();
  });

  it("403 user_deleted → «Аккаунт удалён», а не «раздел недоступен»", () => {
    renderErr(new ApiError(403, "user_deleted", "user was deleted"));
    expect(screen.getByText(authErrorCopy("user_deleted").title)).toBeTruthy();
    expect(screen.queryByText("Этот раздел сейчас недоступен.")).toBeNull();
  });

  it("каждый слаг отказа входа имеет копию в общей карте", () => {
    for (const slug of AUTH_REFUSAL_SLUGS) {
      expect(AUTH_ERROR_COPY[slug], slug).toBeDefined();
      expect(authErrorCopy(slug).body.length, slug).toBeGreaterThan(0);
    }
  });
});
