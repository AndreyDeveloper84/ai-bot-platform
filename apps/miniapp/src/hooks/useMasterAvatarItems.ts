/**
 * Пункты листа аватара мастера — по поверхности (DRF-2127).
 *
 * Общие мастерские экраны (Dashboard, Schedule, Ayla) живут и на
 * `/master/*`, и на `/solo/*`. Набор пунктов и их адреса зависят от
 * поверхности, а не от экрана: на соло — соло-адреса и «Управление
 * салоном» при владельческой роли. Роль приходит из `SoloSurfaceContext`,
 * который ставит `UnifiedSoloSurface`; вне соло контекст пуст.
 */
import { createContext, useContext } from "react";
import { useLocation } from "react-router-dom";

import { masterAvatarSheetItems, type AvatarSheetItem } from "../lib/avatar-sheet";

export interface SoloSurfaceInfo {
  /** Владелец / администратор / ресепшн поверх соло-профиля (DRF-1149). */
  salonAdmin: boolean;
}

export const SoloSurfaceContext = createContext<SoloSurfaceInfo | null>(null);

export function useMasterAvatarItems(): AvatarSheetItem[] {
  const location = useLocation();
  const solo = useContext(SoloSurfaceContext);
  if (location.pathname.startsWith("/solo/")) {
    return masterAvatarSheetItems({ surface: "solo", salonAdmin: solo?.salonAdmin ?? false });
  }
  return masterAvatarSheetItems({ surface: "master" });
}
