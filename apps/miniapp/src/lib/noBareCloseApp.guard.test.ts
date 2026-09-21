/**
 * DRF-2268 — вернуться в чат можно только через `returnToChat` (#1961).
 *
 * `closeApp()` при отсутствии моста `close()` и пустой истории молча ничего не
 * делал (web.max.ru, скрины владельца 21.09): «кнопка не работает». #1961
 * перевёл Главную на `returnToChat` — мост `close()` → ссылка на диалог →
 * «застрял», и экран сам решает, что показать. Этот сторож держит класс:
 * вне `lib/max-sdk.ts` нет ни `closeApp`, ни прямого `close()` моста.
 */
import { describe, expect, it } from "vitest";

const SOURCES = import.meta.glob(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

function code(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

const files = Object.entries(SOURCES).filter(([path]) => !path.endsWith("/lib/max-sdk.ts"));

describe("возврат в чат — только через returnToChat (DRF-2268)", () => {
  it("положительная пара: сторож видит исходники и сам returnToChat где-то зовут", () => {
    expect(files.length).toBeGreaterThan(50);
    expect(files.some(([, src]) => /\breturnToChat\(/.test(code(src)))).toBe(true);
  });

  it("нигде нет closeApp", () => {
    const offenders = files.filter(([, src]) => /\bcloseApp\b/.test(code(src))).map(([p]) => p);
    expect(offenders, "closeApp молчит там, где нет close(): зови returnToChat").toEqual([]);
  });

  it("нигде не зовут close() моста напрямую", () => {
    const direct = /maxBridge\(\)\s*\?\.\s*close\b|\bWebApp\s*\?*\.\s*close\b/;
    const offenders = files.filter(([, src]) => direct.test(code(src))).map(([p]) => p);
    expect(offenders, "прямой close() обходит лестницу returnToChat").toEqual([]);
  });
});
