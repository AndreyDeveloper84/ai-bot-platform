/**
 * Карточка записи дневника питания — доска владельца «Дневник» (DRF-2954,
 * кадры 1, 10, 13). Общая для дневника, дня и избранного.
 *
 * # Слева — всегда место под картинку
 *
 * Решение владельца 12.11 п.1: запись — карточкой, у записи без снимка —
 * значок вместо миниатюры. Это решение снимает заморозку вида строки от 28.09
 * (п.10 «рефакторинг ≠ редизайн») для экранов дневника.
 *
 * Место под картинку занято всегда (56×56, `cr-thumb--tile` раздела клиента):
 * пока снимок грузится, его нет (404 — удалён по сроку) или у записи его не
 * было — там значок. Прежний предел «строка прыгает, когда снимок догрузился»
 * этим снят.
 *
 * Правила снимка прежние (DRF-2455, решения 28.09 п.14 и главного окна):
 * картинка — только когда адрес ПОЛУЧЕН (`useDiaryEntryPhoto`), а не по
 * `has_photo`; без `has_photo === true` к прокси не ходим вовсе. Картинка
 * декоративная (`alt=""`): блюдо названо текстом рядом.
 *
 * Хук нельзя звать внутри `map` экрана, поэтому карточка — компонент.
 */
import type { ReactNode } from "react";

import { useDiaryEntryPhoto } from "../../hooks/useDiaryEntryPhoto";
import type { FoodDiaryEntry } from "../../lib/customer-wellness";
import { RecordIcon } from "../records/RecordIcons";

export const DIARY_ENTRY_CLASS = "diary-entry";
export const DIARY_ENTRY_PHOTO_CLASS = "diary-entry__photo";
export const DIARY_ENTRY_ICON_CLASS = "diary-entry__icon";

interface Props {
  /** Запись дневника — снимок ищется по ней; у избранного снимка нет (`null`). */
  entry: Pick<FoodDiaryEntry, "id" | "has_photo"> | null;
  dish: string;
  /** Вторая строка шапки карточки: время записи, порция избранного. */
  meta?: string;
  /** Строки данных под названием: калории, «Оценка ИИ», БЖУ. */
  children?: ReactNode;
  /** Действия — рядом, на всю ширину карточки. */
  actions?: ReactNode;
  /** То, что раскрывается под действиями (форма граммов). */
  after?: ReactNode;
}

export function DiaryEntryCard({ entry, dish, meta, children, actions, after }: Props) {
  return (
    <li className={DIARY_ENTRY_CLASS}>
      {entry ? <EntryMedia entry={entry} /> : <MediaIcon />}
      <div className="diary-entry__body">
        <span className="diary-entry__dish">{dish}</span>
        {meta ? <span className="diary-entry__meta">{meta}</span> : null}
        {children}
      </div>
      {actions ? <div className="diary-entry__actions">{actions}</div> : null}
      {after}
    </li>
  );
}

function EntryMedia({ entry }: { entry: Pick<FoodDiaryEntry, "id" | "has_photo"> }) {
  const src = useDiaryEntryPhoto(entry);
  if (!src) return <MediaIcon />;
  return (
    <span className="cr-thumb cr-thumb--tile diary-entry__media">
      <img className={`cr-thumb__img ${DIARY_ENTRY_PHOTO_CLASS}`} src={src} alt="" />
    </span>
  );
}

function MediaIcon() {
  return (
    <span className={`cr-thumb cr-thumb--tile diary-entry__media ${DIARY_ENTRY_ICON_CLASS}`}>
      <RecordIcon name="bowl" size={26} />
    </span>
  );
}
