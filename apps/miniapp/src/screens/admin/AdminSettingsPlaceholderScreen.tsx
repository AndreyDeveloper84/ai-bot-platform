/**
 * Admin «Настройки» tab placeholder — salon-wide settings UI ships
 * separately. This screen exists only so the bottom tab has a
 * destination during MM5 frontend rollout.
 */

import { AdminTabBar } from "../../components/AdminTabBar";

export function AdminSettingsPlaceholderScreen() {
  return (
    <div className="screen">
      <header className="screen__header">
        <h1 className="screen__title">Настройки</h1>
      </header>
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>Скоро здесь будут настройки салона.</p>
      </div>
      <AdminTabBar />
    </div>
  );
}
