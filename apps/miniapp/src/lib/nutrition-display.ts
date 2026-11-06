/**
 * «Без чисел» (DRF-2766) — добровольный выбор человека не видеть калории,
 * БЖУ и числовые цели.
 *
 * Решение владельца 04.10: режим отображения на всех экранах, записи дневника
 * он не трогает. Он заменил прежнее автоматическое скрытие по флагу
 * расстройства пищевого поведения: по признаку анкеты числа больше не
 * прячутся, только по выбору самого человека.
 *
 * Хранится на сервере бота (`me/nutrition-display/`), а не в браузере:
 * экраны дневника читают тот же выбор из `nutrition_numbers_hidden`
 * своих ответов, и выбор не теряется при смене устройства.
 */
import { request } from "./api";

const PATH = "/me/nutrition-display/";

export interface NutritionDisplay {
  numbers_hidden: boolean;
}

export async function fetchNutritionDisplay(): Promise<NutritionDisplay> {
  return request<NutritionDisplay>(PATH, { method: "GET" });
}

export async function setNumbersHidden(next: boolean): Promise<NutritionDisplay> {
  return request<NutritionDisplay>(PATH, {
    method: "POST",
    body: JSON.stringify({ numbers_hidden: next }),
  });
}
