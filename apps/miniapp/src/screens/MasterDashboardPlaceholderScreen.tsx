/**
 * Master dashboard — Phase 1 placeholder.
 *
 * Route: /master/dashboard
 *
 * Lands here after M0 onboarding completes (Step 3 accept) OR after a
 * re-entry that hits invite_already_used. Full M1 dashboard lands in a
 * later PR (master-mobile §M1). For now we just confirm the user is
 * past onboarding so manual UX testing has somewhere to land.
 */

import { ScreenLayout } from "../components/ScreenLayout";

export function MasterDashboardPlaceholderScreen() {
  return (
    <ScreenLayout title="Привет!">
      <p>Скоро здесь будет рабочий стол: ваши клиенты, расписание и сообщения.</p>
      <p style={{ color: "var(--c-text-secondary)" }}>
        Закройте Mini App — мы напишем, когда что-то появится.
      </p>
    </ScreenLayout>
  );
}
