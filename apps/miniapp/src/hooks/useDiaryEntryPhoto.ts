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
 * Адрес привязан к id записи, для которой он пришёл: карточка, получившая
 * другую запись, не покажет ни кадра чужого снимка, пока грузится свой.
 *
 * Уход карточки отдаёт аренду; когда уходит последняя, незавершённые
 * запросы отменяются, адреса освобождаются (`lib/diary-photo.ts`).
 */
export function useDiaryEntryPhoto(entry: Pick<FoodDiaryEntry, "id" | "has_photo">): string | null {
  const [loaded, setLoaded] = useState<{ id: string; src: string | null } | null>(null);
  const id = entry.id;
  const hasPhoto = entry.has_photo === true;

  useEffect(() => {
    if (!hasPhoto) return;
    let live = true;
    const lease = acquireDiaryEntryPhoto({ id, has_photo: true });
    lease.promise.then(
      (src) => {
        if (live) setLoaded({ id, src });
      },
      () => {
        if (live) setLoaded({ id, src: null });
      },
    );
    return () => {
      live = false;
      lease.release();
    };
  }, [id, hasPhoto]);

  return hasPhoto && loaded?.id === id ? loaded.src : null;
}
