import { useEffect, useState } from "react";

import type { FoodDiaryEntry } from "../lib/customer-wellness";
import { acquireDiaryEntryPhoto } from "../lib/diary-photo";

/**
 * DRF-2455 — `blob:`-адрес снимка записи дневника, или `null`.
 *
 * `null` — и «снимка нет» (`has_photo` не `true`, запроса нет вовсе), и
 * «ещё грузится», и «прокси ответил 404» (удалён по сроку). Для карточки
 * это одно состояние: строка без картинки (решение главного окна, Б(а)).
 *
 * Уход карточки отдаёт аренду; когда уходит последняя, незавершённые
 * запросы отменяются, адреса освобождаются (`lib/diary-photo.ts`).
 */
export function useDiaryEntryPhoto(entry: Pick<FoodDiaryEntry, "id" | "has_photo">): string | null {
  const [src, setSrc] = useState<string | null>(null);
  const id = entry.id;
  const hasPhoto = entry.has_photo === true;

  useEffect(() => {
    setSrc(null);
    if (!hasPhoto) return;
    let live = true;
    const lease = acquireDiaryEntryPhoto({ id, has_photo: true });
    lease.promise.then(
      (value) => {
        if (live) setSrc(value);
      },
      () => {
        if (live) setSrc(null);
      },
    );
    return () => {
      live = false;
      lease.release();
    };
  }, [id, hasPhoto]);

  return hasPhoto ? src : null;
}
