/**
 * Один запрос готовности на «Сегодня» (DRF-2117): карточка и строка сводки
 * читают одно состояние — два вызова одной ручки за один экран были бы
 * лишним стуком в каталог.
 *
 * Роль решает хук, а не вызывающий: ресепшну (`require_admin_role` → 403)
 * ручка не зовётся вовсе — `{kind: "hidden"}`. 403 от сервера, если он всё
 * же пришёл (роль сменилась под открытым экраном), — тоже `hidden`: это не
 * «не удалось проверить», это «не тебе».
 */

import { useEffect, useState } from "react";

import { getSalonReadiness, type MeResponse } from "../lib/admin-api";
import { ApiError } from "../lib/api";
import { readinessState, type ReadinessState } from "../lib/salon-readiness";

export type ReadinessView = ReadinessState | { kind: "hidden" };

export function canSeeReadiness(me: Pick<MeResponse, "is_owner" | "is_admin">): boolean {
  return Boolean(me.is_owner || me.is_admin);
}

export function useSalonReadiness(me: MeResponse, attempt = 0): ReadinessView {
  const allowed = canSeeReadiness(me);
  const [view, setView] = useState<ReadinessView>(allowed ? { kind: "loading" } : { kind: "hidden" });

  useEffect(() => {
    if (!allowed) {
      setView({ kind: "hidden" });
      return;
    }
    const ctrl = new AbortController();
    setView({ kind: "loading" });
    getSalonReadiness({ signal: ctrl.signal })
      .then((doc) => setView(readinessState(doc)))
      .catch((err: unknown) => {
        if (ctrl.signal.aborted) return;
        if (err instanceof ApiError && (err as ApiError).status === 403) setView({ kind: "hidden" });
        else setView({ kind: "failed" });
      });
    return () => ctrl.abort();
  }, [allowed, attempt]);

  return view;
}
