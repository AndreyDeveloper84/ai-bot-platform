/**
 * Admin «Услуги» tab placeholder — MM4 services↔masters matrix
 * UI ships separately. This screen exists only so the bottom tab
 * has a destination during MM5 frontend rollout.
 */

import { AdminTabBar } from "../../components/AdminTabBar";

export function AdminServicesPlaceholderScreen() {
  return (
    <div className="screen">
      <header className="screen__header">
        <h1 className="screen__title">Услуги</h1>
      </header>
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>Скоро здесь будет редактор услуг.</p>
      </div>
      <AdminTabBar />
    </div>
  );
}
