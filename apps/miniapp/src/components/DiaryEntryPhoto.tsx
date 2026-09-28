import type { ReactNode } from "react";

import type { FoodDiaryEntry } from "../lib/customer-wellness";
import { useDiaryEntryPhoto } from "../hooks/useDiaryEntryPhoto";

/**
 * DRF-2455 — строка дневника с миниатюрой снимка слева (решение владельца
 * 28.09, п.14: «небольшой миниатюрой слева, данные — справа… дневник
 * остаётся компактным списком»).
 *
 * Классы — от окна `mini` (хозяин `styles/*`, по макету «Ближайшей записи»):
 *  * `food-scanner-diary__entry-thumb` — сама миниатюра, прямым ребёнком `<li>`;
 *  * `food-scanner-diary__entry--with-thumb` — модификатор строки. Без него
 *    строка `flex-wrap: wrap`, и данные уезжают на вторую линию ПОД снимок —
 *    при длинном названии и при коротком (калории, БЖУ, кнопки —
 *    `flex: 0 0 auto`). Замер `mini` в Chrome на 360: без модификатора данные
 *    `left` 0, с ним — 98.
 *
 * Модификатор ставится по ФАКТУ картинки (адрес получен), а не по
 * `has_photo` (решение главного окна 28.09): `has_photo` — утверждение
 * сводки, адрес — факт; они расходятся при 404 (снимок удалён по сроку между
 * сводкой и загрузкой), и раскладка «со снимком» без снимка была бы видимой
 * ложью — пустое место слева. Плата — сдвиг строки, когда снимок догрузился;
 * это названный предел, а не дефект.
 *
 * Снимка нет (`has_photo` не `true`, 404, ещё грузится) → обычная текстовая
 * строка: ни `<img>`, ни модификатора; к прокси при `has_photo` не `true`
 * не ходим вовсе.
 *
 * Хук нельзя звать внутри `map` экрана, поэтому строка — компонент.
 */
export const DIARY_ENTRY_CLASS = "food-scanner-diary__entry";
export const DIARY_ENTRY_WITH_THUMB_CLASS = "food-scanner-diary__entry--with-thumb";
export const DIARY_ENTRY_PHOTO_CLASS = "food-scanner-diary__entry-thumb";

export function DiaryEntryItem({
  entry,
  children,
}: {
  entry: Pick<FoodDiaryEntry, "id" | "has_photo">;
  children: ReactNode;
}) {
  const src = useDiaryEntryPhoto(entry);
  return (
    <li className={src ? `${DIARY_ENTRY_CLASS} ${DIARY_ENTRY_WITH_THUMB_CLASS}` : DIARY_ENTRY_CLASS}>
      {src && <img className={DIARY_ENTRY_PHOTO_CLASS} src={src} alt="" />}
      {children}
    </li>
  );
}
