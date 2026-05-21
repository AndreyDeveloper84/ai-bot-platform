/**
 * Master schedule (M3) — placeholder.
 *
 * Full M3 implementation tracked separately. This screen renders a
 * neutral "Скоро здесь будет расписание." stub plus the shared master
 * tab bar so navigation stays consistent. State-matrix discipline per
 * customer-first-touch-and-mini-app-states §7 — placeholder is its
 * own «not-yet-implemented» variant, never a generic spinner.
 */

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { MasterTabBar } from "../components/MasterTabBar";
import { setBackButton } from "../lib/max-sdk";

export function MasterSchedulePlaceholderScreen() {
  const navigate = useNavigate();
  useEffect(() => {
    // Not root; show back to dashboard.
    setBackButton(false);
    return () => setBackButton(false);
  }, []);
  return (
    <>
      <ScreenLayout title="Расписание">
        <p>Скоро здесь будет расписание на день и неделю.</p>
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
