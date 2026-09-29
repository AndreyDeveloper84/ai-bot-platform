/**
 * «Сменить режим» — the way back out of a chosen surface.
 *
 * A user who holds more than one role (an owner who is also a master,
 * a solo provider) picks a surface once and then lives in it: the
 * choice is persisted and the routing cascade in `App` honours it on
 * every open. Without a visible way back, picking «Клиент» once would
 * strand her in the customer app with no route to her own salon — worse
 * than never having the customer surface at all. So the switch ships in
 * the same PR as the choice.
 *
 * Rendered from the settings/profile screen of each surface:
 *   - `AdminSettingsPlaceholderScreen`  (/admin/settings)
 *   - `MasterSettingsScreen`            (/master/settings, /solo/settings)
 *   - `CustomerProfileScreen`           (/customer/profile)
 *
 * DRF-1469 — и с КОРНЯ клиентской поверхности, через компактный
 * `SurfaceSwitchExit` в закреплённой нижней панели. Пока выход жил
 * только на трёх экранах настроек, до него надо было ещё дойти: у
 * клиента нет нижней навигации, и человек, встреченный на корне
 * анкетой, до профиля не добирался. Из-за этого многоролевым анкету
 * просто не показывали (`goalEntry={false}` в `App.tsx`) — то есть
 * владелец и вся команда не видели новую поверхность вовсе.
 *
 * Приём тот же, что вылечил DRF-1349 и DRF-1434: то, без чего человек
 * застревает, объявляется НЕ на одном удобном экране, а там, куда он
 * реально попадает.
 *
 * It renders NOTHING for a single-role caller — a plain customer has no
 * other surface to switch to, and an unexplained «Сменить режим» button
 * would only raise questions. Visibility is driven by `canSwitch` from
 * the context that `App` provides, which is the same `hasAdmin &&
 * hasMaster` predicate that decides whether the chooser exists at all.
 */

import { createContext, useContext } from "react";

export interface SurfaceModeContextValue {
  /** True when the caller holds both an admin-side role AND a master link. */
  canSwitch: boolean;
  /** Drops the persisted choice and brings the surface chooser up. */
  requestChooser: () => void;
}

const NOOP_MODE: SurfaceModeContextValue = {
  canSwitch: false,
  requestChooser: () => {},
};

/**
 * Default = «no switching». Screens are also rendered by focused unit
 * tests and (historically) by storybook-less dev harnesses that don't
 * mount `App`; those get the inert default instead of a crash.
 */
export const SurfaceModeContext =
  createContext<SurfaceModeContextValue>(NOOP_MODE);

export function useSurfaceMode(): SurfaceModeContextValue {
  return useContext(SurfaceModeContext);
}

/**
 * The reusable action itself. One component, three call sites — the
 * copy and the affordance stay identical whichever surface the user is
 * currently in.
 */
export const SURFACE_SWITCH_LABEL = "Сменить режим";

export function SurfaceSwitchButton() {
  const { canSwitch, requestChooser } = useSurfaceMode();
  if (!canSwitch) return null;
  return (
    <div className="surface-switch" style={{ margin: "var(--s-4) 0" }}>
      <button
        type="button"
        className="btn-secondary"
        style={{ width: "100%", justifyContent: "center" }}
        onClick={requestChooser}
      >
        {SURFACE_SWITCH_LABEL}
      </button>
      <p
        style={{
          margin: "var(--s-2) 0 0",
          fontSize: "var(--font-size-200)",
          color: "var(--c-text-secondary)",
        }}
      >
        Например, посмотреть приложение глазами клиента.
      </p>
    </div>
  );
}

/**
 * Тот же выход, компактный — для закреплённой нижней панели (DRF-1469).
 *
 * Отдельный вид, а не отдельное действие: подпись и `requestChooser` те
 * же, что у кнопки на экранах настроек. В панели выход стоит рядом с
 * главным действием экрана и потому нарочно тише его: уйти с
 * поверхности — не то, зачем сюда пришли, но и не то, что должно
 * требовать прокрутки.
 *
 * Как и `SurfaceSwitchButton`, ничего не рисует одноролевому: анкета
 * обычного клиента этой правкой не меняется вовсе.
 */
export function SurfaceSwitchExit() {
  const { canSwitch, requestChooser } = useSurfaceMode();
  if (!canSwitch) return null;
  return (
    <button type="button" className="cta-bar__exit" onClick={requestChooser}>
      {SURFACE_SWITCH_LABEL}
    </button>
  );
}
