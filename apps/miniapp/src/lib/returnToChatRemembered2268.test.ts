/**
 * DRF-2268 — ссылка на диалог, полученная Главной, доступна остальным экранам.
 *
 * `chat_link` приходит с `last-topic` (#1961) только на Главной. Экраны после
 * неё (запись, каталог, цель, рекомендация) зовут `returnToChat()` без
 * аргумента — и должны получить ту же ссылку, а не «застрять» без причины.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { rememberChatLink, returnToChat } from "./max-sdk";

afterEach(() => {
  delete (window as { WebApp?: unknown }).WebApp;
  rememberChatLink(null);
});

describe("запомненная ссылка на чат", () => {
  it("без аргумента returnToChat берёт запомненную ссылку", () => {
    const openLink = vi.fn();
    (window as { WebApp?: unknown }).WebApp = { openLink };
    rememberChatLink("https://max.ru/ayla_client_bot");
    expect(returnToChat()).toBe("opened_chat");
    expect(openLink).toHaveBeenCalledWith("https://max.ru/ayla_client_bot");
  });

  it("явный аргумент сильнее запомненного", () => {
    const openLink = vi.fn();
    (window as { WebApp?: unknown }).WebApp = { openLink };
    rememberChatLink("https://max.ru/old");
    expect(returnToChat("https://max.ru/new")).toBe("opened_chat");
    expect(openLink).toHaveBeenCalledWith("https://max.ru/new");
  });

  it("ничего не запомнено — «застрял» (положительная пара к первому узлу)", () => {
    const openLink = vi.fn();
    (window as { WebApp?: unknown }).WebApp = { openLink };
    expect(returnToChat()).toBe("stuck");
    expect(openLink).not.toHaveBeenCalled();
  });
});
