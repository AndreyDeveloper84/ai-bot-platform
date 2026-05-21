/**
 * Master conversations list (M5) — placeholder.
 *
 * Full M5 implementation tracked separately. State-matrix discipline
 * per customer-first-touch-and-mini-app-states §7 — informational
 * empty, no spinner.
 */

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { MasterTabBar } from "../components/MasterTabBar";
import { setBackButton } from "../lib/max-sdk";

export function MasterConversationsPlaceholderScreen() {
  const navigate = useNavigate();
  useEffect(() => {
    setBackButton(false);
    return () => setBackButton(false);
  }, []);
  return (
    <>
      <ScreenLayout title="Диалоги">
        <p>Скоро здесь будут ваши диалоги с клиентами.</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate("/master/dashboard")}
          >
            На главную
          </button>
        </div>
      </ScreenLayout>
      <MasterTabBar
        unreadCount={0}
        scheduleHasPendingChange={false}
        profileHasOwnerPendingChange={false}
      />
    </>
  );
}
