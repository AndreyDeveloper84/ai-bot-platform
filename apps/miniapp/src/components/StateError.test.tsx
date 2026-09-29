/**
 * DRF-1319 D-1 — отказ входа называется одним именем на всех экранах.
 *
 * Инвентарь 1319-C: четыре экрана (каталог, услуга, мастер, окна) при
 * пустом `initData` показывали через `StateError` серверную строку
 * «missing Authorization header», а `HelloScreen` для того же 400 — «MAX
 * не передал данные для входа». Теперь копия одна (`lib/auth-error-copy.ts`).
 *
 * Положительная половина впереди: обычные ошибки (5xx, 403 без слага
 * входа, прочие 4xx) рисуются как раньше — иначе тест зеленел бы на
 * компоненте, который на ЛЮБУЮ ошибку отвечает «не получилось войти».
 *
 * DRF-2446: прочие 4xx больше НЕ печатают серверный `detail` — он
 * написан для нас и по-английски; на экране согласованная фраза, а
 * `detail` уходит в журнал.
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

  it("прочие 4xx → согласованная фраза, серверный detail не показан (DRF-2446)", () => {
    // Решение изменилось: раньше сюда печатался `detail`, и владелец
    // увидел «booking not found». Узел не ослаблен — он стал строже:
    // проверяем И фразу, И отсутствие внутреннего текста.
    renderErr(new ApiError(404, "not_found", "booking not found"));
    expect(screen.getByText("Не получилось загрузить.")).toBeTruthy();
    expect(screen.queryByText(/booking not found/)).toBeNull();
  });

  it("никакой 4xx не выносит detail на экран", () => {
    // Охват, а не один случай: по замеру 24.09 в `miniapp_api` 176
    // английских строк `detail`, и общий хвост печатал любую.
    for (const [status, slug, detail] of [
      [400, "bad_request", "master_id and service_id are required"],
      [404, "not_found", "booking not found"],
      [409, "conflict", "price or duration changed since it was shown"],
      [422, "validation_error", "ml must be between 50 and 2000"],
    ] as const) {
      const { unmount } = render(<StateError err={new ApiError(status, slug, detail)} onRetry={vi.fn()} />);
      expect(screen.getByText("Не получилось загрузить.")).toBeTruthy();
      expect(screen.queryByText(new RegExp(detail.slice(0, 12)))).toBeNull();
      unmount();
    }
  });

  it("detail не потерян — он уходит в журнал", () => {
    // Иначе через неделю его вернут на экран, чтобы «было видно».
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderErr(new ApiError(404, "not_found", "booking not found"));
    expect(warn).toHaveBeenCalledTimes(1);
    const [line] = warn.mock.calls[0] ?? [];
    expect(String(line)).toContain("booking not found");
    expect(String(line)).toContain("not_found");
    warn.mockRestore();
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
