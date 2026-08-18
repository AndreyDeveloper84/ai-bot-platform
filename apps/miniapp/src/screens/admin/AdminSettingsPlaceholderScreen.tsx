/**
 * Admin «Настройки» tab placeholder — salon-wide settings UI ships
 * separately. This screen exists only so the bottom tab has a
 * destination during MM5 frontend rollout.
 */

import { AdminTabBar } from "../../components/AdminTabBar";
import { SurfaceSwitchButton } from "../../components/SurfaceSwitch";

export function AdminSettingsPlaceholderScreen() {
  return (
    <div className="screen">
      <header className="screen__header">
        <h1 className="screen__title">Настройки</h1>
      </header>
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>Скоро здесь будут настройки салона.</p>
      </div>
      {/*
        The way back out of a surface. Renders itself away for anyone
        holding a single role, so the ordinary receptionist never sees a
        control that would only confuse her.
      */}
      <SurfaceSwitchButton />
      <AdminTabBar />
    </div>
  );
}
