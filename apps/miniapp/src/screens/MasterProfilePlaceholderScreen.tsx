/**
 * Master profile editor (M4) — placeholder.
 *
 * Full M4 implementation tracked separately.
 */

import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { MasterTabBar } from "../components/MasterTabBar";
import { setBackButton } from "../lib/max-sdk";

export function MasterProfilePlaceholderScreen() {
  const navigate = useNavigate();
  useEffect(() => {
    setBackButton(false);
    return () => setBackButton(false);
  }, []);
  return (
    <>
      <ScreenLayout title="Профиль">
        <p>Скоро здесь будет ваш профиль: фото, «о себе» и услуги.</p>
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
