import { useEffect, useState } from "react";

import { acquireMasterPhoto, masterPhotoPath } from "../lib/master-photo";

/**
 * DRF-2539 — `blob:`-адрес фото мастера (или работы портфолио), или `null`.
 *
 * `null` — и «фото нет», и «ещё грузится», и «прокси ответил отказом»: для
 * карточки это одно состояние — инициалы (или пустая плитка портфолио).
 *
 * Адрес привязан к пути, для которого он пришёл: карточка, получившая
 * другого мастера, не покажет ни кадра чужого лица, пока грузится своё.
 */
export function useMasterPhoto(value: string | null | undefined): string | null {
  const path = masterPhotoPath(value);
  const [loaded, setLoaded] = useState<{ path: string; src: string | null } | null>(null);

  useEffect(() => {
    if (path === null) return;
    let live = true;
    const lease = acquireMasterPhoto(path);
    lease.promise.then(
      (src) => {
        if (live) setLoaded({ path, src });
      },
      () => {
        if (live) setLoaded({ path, src: null });
      },
    );
    return () => {
      live = false;
      lease.release();
    };
  }, [path]);

  return path !== null && loaded?.path === path ? loaded.src : null;
}
