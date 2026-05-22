/**
 * Root SPA shell — three role surfaces (customer / master / admin)
 * coexist behind a /api/v1/me boot fetch + role-gated routes.
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM0
 * (admin shell) + ADR-0008 (role detection).
 *
 * Boot flow:
 *   1. Splash «Загружаем рабочее место…»
 *   2. GET /api/v1/me → cache MeResponse in state
 *   3. Branch:
 *      - is_owner / is_admin / is_receptionist → admin routes (default
 *        landing /admin/team). Receptionist sees the list but every
 *        owner-only action button is disabled with a tooltip — backend
 *        re-checks at /deactivate + /reactivate.
 *      - is_master → /master/dashboard (unchanged from M0..M6).
 *      - is_customer → existing customer routes (unchanged).
 *      - 401 / unmapped → «Доступ не настроен» error screen with
 *        link to open the bot DM.
 *
 * On role boot failure we DO NOT block forever — the error screen
 * gives a retry button + chat link, and the customer fallback routes
 * remain reachable if the backend can't resolve a role at all (Phase
 * 1 single-tenant install where some users haven't DM'd the bot yet).
 */

import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "./lib/api";
import { getMe, type MeResponse } from "./lib/admin-api";
import { AdminAvailabilityRequestsScreen } from "./screens/admin/AdminAvailabilityRequestsScreen";
import { AdminChatsPlaceholderScreen } from "./screens/admin/AdminChatsPlaceholderScreen";
import { AdminDeactivationFlowScreen } from "./screens/admin/AdminDeactivationFlowScreen";
import { AdminInviteMasterScreen } from "./screens/admin/AdminInviteMasterScreen";
import { AdminMasterDetailScreen } from "./screens/admin/AdminMasterDetailScreen";
import { AdminServicesMatrixScreen } from "./screens/admin/AdminServicesMatrixScreen";
import { AdminSettingsPlaceholderScreen } from "./screens/admin/AdminSettingsPlaceholderScreen";
import { AdminTeamScreen } from "./screens/admin/AdminTeamScreen";
import { BookingConfirmScreen } from "./screens/BookingConfirmScreen";
import { BookingSuccessScreen } from "./screens/BookingSuccessScreen";
import { BookingWhenScreen } from "./screens/BookingWhenScreen";
import { CatalogScreen } from "./screens/CatalogScreen";
import { FeedbackScreen } from "./screens/FeedbackScreen";
import { HelloScreen } from "./screens/HelloScreen";
import { MasterConversationDetailScreen } from "./screens/MasterConversationDetailScreen";
import { MasterConversationsScreen } from "./screens/MasterConversationsScreen";
import { MasterDashboardScreen } from "./screens/MasterDashboardScreen";
import { MasterOnboardingScreen } from "./screens/MasterOnboardingScreen";
import { MasterPickerScreen } from "./screens/MasterPickerScreen";
import { MasterProfileScreen } from "./screens/MasterProfileScreen";
import { MasterScheduleScreen } from "./screens/MasterScheduleScreen";
import { MyVisitDetailScreen } from "./screens/MyVisitDetailScreen";
import { MyVisitsScreen } from "./screens/MyVisitsScreen";
import { ProfileScreen } from "./screens/ProfileScreen";
import { RescheduleScreen } from "./screens/RescheduleScreen";
import { ServiceDetailScreen } from "./screens/ServiceDetailScreen";

type BootStatus = "loading" | "ready" | "error" | "no_role";

interface BootState {
  status: BootStatus;
  me: MeResponse | null;
  err: unknown;
}

const INITIAL: BootState = { status: "loading", me: null, err: null };

function SplashScreen() {
  return (
    <div className="screen" role="status" aria-live="polite">
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "60vh",
          gap: "var(--s-3)",
        }}
      >
        <div className="skeleton" style={{ width: 80, height: 80, borderRadius: "50%" }} />
        <p style={{ color: "var(--c-text-secondary)" }}>
          Загружаем рабочее место…
        </p>
      </div>
    </div>
  );
}

function NoRoleScreen({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="screen">
      <h1 className="screen__title">Доступ не настроен</h1>
      <p>
        Кажется, эта учётная запись пока не привязана к салону. Откройте
        чат с ботом, чтобы зарегистрироваться, и попробуйте снова.
      </p>
      <div
        style={{ display: "flex", gap: "var(--s-2)", marginTop: "var(--s-4)" }}
      >
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Попробовать снова
        </button>
      </div>
    </div>
  );
}

/** Routes for admin / owner / receptionist roles. */
function AdminRoutes({ me }: { me: MeResponse }) {
  return (
    <Routes>
      <Route path="/admin/team" element={<AdminTeamScreen me={me} />} />
      <Route
        path="/admin/team/invite"
        element={<AdminInviteMasterScreen me={me} />}
      />
      <Route
        path="/admin/team/:masterId/deactivate"
        element={<AdminDeactivationFlowScreen me={me} />}
      />
      <Route
        path="/admin/team/:masterId"
        element={<AdminMasterDetailScreen me={me} />}
      />
      <Route
        path="/admin/services"
        element={<AdminServicesMatrixScreen me={me} />}
      />
      <Route
        path="/admin/availability-requests"
        element={<AdminAvailabilityRequestsScreen me={me} />}
      />
      <Route path="/admin/chats" element={<AdminChatsPlaceholderScreen />} />
      <Route
        path="/admin/settings"
        element={<AdminSettingsPlaceholderScreen />}
      />
      {/* Default + unknown — land on team. */}
      <Route path="*" element={<Navigate to="/admin/team" replace />} />
    </Routes>
  );
}

/** Routes for the master role (existing M0-M6 surface). */
function MasterRoutes() {
  return (
    <Routes>
      <Route path="/onboarding/master" element={<MasterOnboardingScreen />} />
      <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
      <Route path="/master/schedule" element={<MasterScheduleScreen />} />
      <Route path="/master/conversations" element={<MasterConversationsScreen />} />
      <Route
        path="/master/conversations/:id"
        element={<MasterConversationDetailScreen />}
      />
      <Route path="/master/profile" element={<MasterProfileScreen />} />
      {/* Default + unknown — land on dashboard. */}
      <Route path="*" element={<Navigate to="/master/dashboard" replace />} />
    </Routes>
  );
}

/** Routes for the customer role (Phase 0c / Phase 4 surface). */
function CustomerRoutes() {
  return (
    <Routes>
      <Route path="/" element={<HelloScreen />} />
      <Route path="/catalog" element={<CatalogScreen />} />
      <Route path="/catalog/:serviceId" element={<ServiceDetailScreen />} />
      <Route path="/book/master" element={<MasterPickerScreen />} />
      <Route path="/book/when" element={<BookingWhenScreen />} />
      <Route path="/book/confirm" element={<BookingConfirmScreen />} />
      <Route path="/book/success/:bookingId" element={<BookingSuccessScreen />} />
      <Route path="/my-visits" element={<MyVisitsScreen />} />
      <Route path="/my-visits/:bookingId" element={<MyVisitDetailScreen />} />
      <Route
        path="/my-visits/:bookingId/reschedule"
        element={<RescheduleScreen />}
      />
      <Route path="/me" element={<ProfileScreen />} />
      <Route path="/feedback/:bookingId" element={<FeedbackScreen />} />
      <Route path="*" element={<HelloScreen />} />
    </Routes>
  );
}

/**
 * Fallback when /api/v1/me fails with a non-auth error — keep the
 * customer surface reachable so a partial outage doesn't lock users
 * out of catalog browsing. The error banner sits on top of the
 * customer routes via a wrapper.
 */
function CustomerFallbackWithBanner({
  onRetry,
}: {
  onRetry: () => void;
}) {
  const location = useLocation();
  // Banner shows only on the root page so customers browsing don't
  // see a perpetual error toast.
  const showBanner = location.pathname === "/";
  return (
    <>
      {showBanner && (
        <div
          className="callout callout--danger"
          role="alert"
          style={{ margin: "var(--s-2) var(--s-3)" }}
        >
          <p style={{ margin: 0 }}>
            Не получилось загрузить ваш профиль.{" "}
          </p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-2)" }}
            onClick={onRetry}
          >
            Попробовать снова
          </button>
        </div>
      )}
      <CustomerRoutes />
    </>
  );
}

export function App() {
  const [boot, setBoot] = useState<BootState>(INITIAL);

  const loadMe = useCallback(async () => {
    setBoot({ status: "loading", me: null, err: null });
    try {
      const me = await getMe();
      setBoot({ status: "ready", me, err: null });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        // user_not_registered — first-time visitor who hasn't DM'd
        // the bot. Surface the «Доступ не настроен» onboarding hint.
        setBoot({ status: "no_role", me: null, err: e });
        return;
      }
      // Any other failure — log and fall back to customer surface
      // with a retry banner. This keeps single-tenant Phase 0 demos
      // resilient when the /me endpoint can't be reached.
      setBoot({ status: "error", me: null, err: e });
    }
  }, []);

  useEffect(() => {
    void loadMe();
  }, [loadMe]);

  if (boot.status === "loading") return <SplashScreen />;
  if (boot.status === "no_role") {
    return <NoRoleScreen onRetry={() => void loadMe()} />;
  }

  if (boot.status === "ready" && boot.me) {
    const me = boot.me;
    if (me.is_owner || me.is_admin || me.is_receptionist) {
      return <AdminRoutes me={me} />;
    }
    if (me.is_master) return <MasterRoutes />;
    return <CustomerRoutes />;
  }

  // Network / 5xx — customer fallback with a retry banner.
  return <CustomerFallbackWithBanner onRetry={() => void loadMe()} />;
}
