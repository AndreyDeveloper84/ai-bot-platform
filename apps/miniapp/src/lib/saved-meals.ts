/**
 * Избранные блюда — серверный источник (DRF-2092, дневник F12).
 *
 * Строки живут в каталоге под личностью человека (beautygo_backend#505) и
 * доезжают сюда через прокси бота (`customer/saved-meals`, #1835). На
 * устройстве ничего не хранится: избранное переживает переустановку по
 * построению, а не по удачному кешу — здесь когда-то стоял `localStorage`,
 * и карта 12.09 (D12) назвала это «источника нет».
 *
 * «Сохранить в избранное» — из записи дневника: шлётся `food_log_id`, снимок
 * (порция, калории, БЖУ) делает каталог из своей записи. Сервер отвечает
 * 201 на новую строку и 200 на уже сохранённую (то же блюдо с той же
 * порцией — не дубль): экран говорит это разными словами.
 */
import { request, requestWithStatus } from "./api";

export interface SavedMeal {
  id: string;
  dish_name: string;
  /** Порция в граммах — как её видит человек, не множитель базовых 100 г. */
  portion_g: number;
  calories: number;
  protein_g: number | null;
  fat_g: number | null;
  carbs_g: number | null;
  source_food_log_id: string | null;
  created_at: string | null;
}

export async function listSavedMeals(): Promise<SavedMeal[]> {
  const res = await request<{ items: SavedMeal[] }>("/saved-meals");
  return res.items;
}

export interface SaveMealOutcome {
  /** true — строка новая (201); false — уже была (200). */
  created: boolean;
  meal: SavedMeal;
}

export async function saveMealFromEntry(foodLogId: string): Promise<SaveMealOutcome> {
  const { status, data } = await requestWithStatus<SavedMeal>("/saved-meals", {
    method: "POST",
    body: JSON.stringify({ food_log_id: foodLogId }),
  });
  return { created: status === 201, meal: data };
}

export async function deleteSavedMeal(mealId: string): Promise<void> {
  await request<{ id: string; deleted: boolean }>(
    `/saved-meals/${encodeURIComponent(mealId)}`,
    { method: "DELETE" },
  );
}
