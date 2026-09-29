/**
 * Окно возврата удалённой записи — с провода (DRF-2108).
 *
 * Каталог отдаёт `restore_window_expires_at` в ответе на удаление
 * (`food_log_edit_service.RESTORE_WINDOW_MINUTES` живёт там, не здесь).
 * Экран не держит своей константы: без поля, при нечитаемой дате и когда
 * окно уже закрылось — `null`, и обещания «вернуть можно» не звучит
 * (fail-closed).
 */

/** Минуты до конца окна, вверх; `null` — обещать нечего. */
export function restoreWindowMinutesLeft(
  expiresAt: string | null | undefined,
  now: Date = new Date(),
): number | null {
  if (!expiresAt) return null;
  const expires = new Date(expiresAt).getTime();
  if (Number.isNaN(expires)) return null;
  const seconds = (expires - now.getTime()) / 1000;
  if (seconds <= 0) return null;
  return Math.max(1, Math.ceil(seconds / 60));
}

/** «1 минуту» · «4 минуты» · «15 минут». */
export function minutesRu(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  let word: string;
  if (mod10 === 1 && mod100 !== 11) word = "минуту";
  else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) word = "минуты";
  else word = "минут";
  return `${n} ${word}`;
}
