/**
 * DRF-2266 — «вернуться в чат» не бывает тишиной.
 *
 * web.max.ru (скрины владельца 21.09): Mini App открыт прямо на Главной, у
 * моста может не быть `close()`, истории нет — `closeApp()` молча ничего не
 * делал. Порядок: мост `close()` → ссылка на диалог бота через `openLink` →
 * иначе «застрял», и экран показывает подсказку.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { returnToChat } from "./max-sdk";

afterEach(() => {
  delete (window as { WebApp?: unknown }).WebApp;
});

describe("returnToChat", () => {
  it("мост умеет закрыть — закрывает, ссылку не трогает", () => {
    const close = vi.fn();
    const openLink = vi.fn();
    (window as { WebApp?: unknown }).WebApp = { close, openLink };
    expect(returnToChat("https://max.ru/bot")).toBe("closed");
    expect(close).toHaveBeenCalledTimes(1);
    expect(openLink).not.toHaveBeenCalled();
  });

  it("закрыть нечем, есть openLink и ссылка — открывает диалог бота", () => {
    const openLink = vi.fn();
    (window as { WebApp?: unknown }).WebApp = { openLink };
    expect(returnToChat("https://max.ru/bot")).toBe("opened_chat");
    expect(openLink).toHaveBeenCalledWith("https://max.ru/bot");
  });

  it("нет ни close, ни ссылки — «застрял», решает экран", () => {
    const openLink = vi.fn();
    (window as { WebApp?: unknown }).WebApp = { openLink };
    expect(returnToChat(null)).toBe("stuck");
    expect(openLink).not.toHaveBeenCalled();
  });

  it("моста нет вовсе — «застрял»", () => {
    expect(returnToChat("https://max.ru/bot")).toBe("stuck");
  });

  it("close() бросил — пробует ссылку", () => {
    const openLink = vi.fn();
    const close = vi.fn(() => {
      throw new Error("nope");
    });
    (window as { WebApp?: unknown }).WebApp = { close, openLink };
    expect(returnToChat("https://max.ru/bot")).toBe("opened_chat");
    expect(openLink).toHaveBeenCalledTimes(1);
  });
});
