import type { FoodDiaryEntry } from "../lib/customer-wellness";
import { useDiaryEntryPhoto } from "../hooks/useDiaryEntryPhoto";

/**
 * DRF-2455 — миниатюра снимка слева в строке дневника (решение владельца
 * 28.09, п.14: «небольшой миниатюрой слева, данные — справа… дневник
 * остаётся компактным списком»).
 *
 * Класс `food-scanner-diary__entry-thumb` — по макету «Ближайшей записи» (экран 2
 * принятой презентации); правило заводит окно `mini` (хозяин `styles/*`). Строка
 * уже `display: flex` — картинка первым ребёнком встаёт слева, данные справа;
 * строка без снимка не получает ни элемента, ни объявления.
 *
 * Состояния строки:
 *  * снимок есть → картинка, источник — только прокси бота (`blob:`-адрес
 *    от `useDiaryEntryPhoto`, заголовок initData; DRF-2549);
 *  * снимка нет (`has_photo` не `true`, или прокси ответил 404 — удалён по
 *    сроку 30 суток) → НИЧЕГО: строка выглядит как обычная текстовая
 *    запись, без заглушки и без текста (решение главного окна 26.09, Б(а));
 *    к прокси при этом не ходим вовсе.
 */
export const DIARY_ENTRY_PHOTO_CLASS = "food-scanner-diary__entry-thumb";

export function DiaryEntryPhoto({ entry }: { entry: Pick<FoodDiaryEntry, "id" | "has_photo"> }) {
  const src = useDiaryEntryPhoto(entry);
  if (!src) return null;
  return <img className={DIARY_ENTRY_PHOTO_CLASS} src={src} alt="" />;
}
