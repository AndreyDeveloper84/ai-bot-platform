/**
 * DRF-2654 — переменные, без которых production-сборка не собирается.
 *
 * `VITE_SUPPORT_DEEPLINK` читалась через `??`, а сборка выкладки не задавала
 * её вовсе: на пилоте кнопка «поддержка» вела на умолчание из кода
 * (`https://max.me/aylasupport`) — адрес-заглушку. Шаги с переменной написаны
 * ещё 12.07 (PR #1115, задача #949), но не слиты: в `dev` о ней не было ни
 * слова. Отсутствие такой переменной в production — отказ сборки с её именем,
 * а не молчаливое умолчание.
 *
 * Пусто и одни пробелы — то же, что не задано. Dev-сервер и тесты не
 * затрагиваются: там пустое значение законно (`.env.local.example`).
 */
export const REQUIRED_IN_PRODUCTION = ["VITE_SUPPORT_DEEPLINK"] as const;

export function missingInProduction(
  mode: string,
  env: Record<string, string | undefined>,
): string[] {
  if (mode !== "production") return [];
  return REQUIRED_IN_PRODUCTION.filter((name) => !(env[name] ?? "").trim());
}
