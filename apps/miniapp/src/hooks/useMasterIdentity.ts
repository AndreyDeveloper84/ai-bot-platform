/**
 * Имя и фото мастера для аватара на разделах, у которых своих данных о
 * мастере нет (Расписание, Ayla) — DRF-2121, §28 п.3.
 *
 * Один запрос `GET master/me` на сессию приложения: ответ кэшируется на
 * уровне модуля, повторные экраны его не дёргают. Сбой — аватар без имени
 * («•»), лист всё равно открывается: пункты не зависят от ответа.
 */
import { useEffect, useState } from "react";

import { getMasterMe } from "../lib/master-api";

export interface MasterIdentity {
  name: string;
  photoUrl: string;
}

let cached: MasterIdentity | null = null;
let inflight: Promise<MasterIdentity | null> | null = null;

async function load(): Promise<MasterIdentity | null> {
  if (cached) return cached;
  if (!inflight) {
    inflight = getMasterMe()
      .then((res) => {
        cached = { name: res.master.name, photoUrl: res.master.photo_url };
        return cached;
      })
      .catch(() => null)
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

/** Только для тестов: забыть закэшированный ответ. */
export function resetMasterIdentityCache(): void {
  cached = null;
  inflight = null;
}

export function useMasterIdentity(): MasterIdentity | null {
  const [identity, setIdentity] = useState<MasterIdentity | null>(cached);
  useEffect(() => {
    let alive = true;
    void load().then((res) => {
      if (alive && res) setIdentity(res);
    });
    return () => {
      alive = false;
    };
  }, []);
  return identity;
}
