/**
 * Admin «Чаты» tab placeholder — admin conversation oversight ships
 * separately. This screen exists only so the bottom tab has a
 * destination during MM5 frontend rollout.
 */

import { AdminTabBar } from "../../components/AdminTabBar";

export function AdminChatsPlaceholderScreen() {
  return (
    <div className="screen">
      <header className="screen__header">
        <h1 className="screen__title">Чаты</h1>
      </header>
      <div className="callout" role="status">
        <p style={{ margin: 0 }}>Скоро здесь будет обзор разговоров.</p>
      </div>
      <AdminTabBar />
    </div>
  );
}
