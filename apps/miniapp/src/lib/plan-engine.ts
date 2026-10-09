/**
 * Сохранённый план нового механизма — клиент Mini App (DRF-2876).
 *
 * Решение владельца (лист 07.10, п.9–10): раздел «Мой план» один; сохранённый
 * новый план — основной, прежний показывается, пока нового нет. Подписи шагов
 * — утверждённый клиентский текст каталога; ключей способностей в ответе нет.
 *
 * Источник — каталог через прокси бота (`customer/plan/current`). Экран —
 * не второй источник истины: он показывает то, что сохранено на сервере.
 */
import { ApiError, request } from "./api";

export interface SavedPlanStep {
  step_id: string;
  /** Подпись каталога — показывается как есть. */
  label: string;
  /**
   * «Почему этот шаг?» — курируемый «ожидаемый эффект» способности из
   * каталога, показывается как есть. `null` или ключа нет — текста нет:
   * ссылка не рисуется, текст не сочиняется.
   */
  why?: string | null;
}

export interface SavedPlan {
  plan_id: string;
  steps: SavedPlanStep[];
}

/**
 * Сохранённый план или `null`.
 *
 * `null` в двух случаях, и оба значат «показывай прежний план»: нового плана
 * нет, либо новый механизм выключен на сервере (`plan_engine_disabled`).
 * Любой другой отказ пробрасывается: подставить прежний план вместо
 * сохранённого нового, который не удалось прочитать, было бы неправдой.
 */
export async function getSavedPlan(): Promise<SavedPlan | null> {
  try {
    const res = await request<{ plan: SavedPlan | null }>("/plan/current");
    return res.plan ?? null;
  } catch (e) {
    if (e instanceof ApiError && e.slug === "plan_engine_disabled") return null;
    throw e;
  }
}
