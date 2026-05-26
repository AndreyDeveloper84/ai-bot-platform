/**
 * Root SPA shell — four role surfaces (customer / master / admin /
 * unified admin+master for solo providers and dual-role staff) coexist
 * behind a /api/v1/me boot fetch + role-gated routes.
 *
 * Spec: docs/design/handoffs/2026-05-18-master-management-handoff.md §MM0
 * (admin shell) + ADR-0008 (role detection) + memory
 * `project_solo_provider_universal_ui` (founder decision 2026-05-25 —
 * universal UI with smart defaults; solo provider = self-employed
 * Olga who is owner+admin+master in one tenant, one User row).
 *
 * Boot flow:
 *   1. Splash «Загружаем рабочее место…»
 *   2. GET /api/v1/me → cache MeResponse in state
 *   3. Branch (INCLUSIVE — issue #79):
 *      - has admin role (is_owner / is_admin / is_receptionist) AND
 *        is_master → UnifiedAdminMasterRoutes (tabbed surface giving
 *        access to BOTH /admin/* and /master/* routes; default landing
 *        is the UnifiedLanding chooser, or the last-chosen surface
 *        persisted in DeviceStorage). Covers the solo provider case
 *        (owner+admin+master = one Olga) AND the team admin who also
 *        delivers services.
 *      - has admin role only → AdminRoutes (default landing /admin/team).
 *        Receptionist sees the list but every owner-only action button
 *        is disabled with a tooltip — backend re-checks at /deactivate
 *        + /reactivate.
 *      - is_master only → /master/dashboard (unchanged from M0..M6).
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
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "./lib/api";
import { getMe, type MeResponse } from "./lib/admin-api";
import { AdminAvailabilityRequestsScreen } from "./screens/admin/AdminAvailabilityRequestsScreen";
import { AdminDeactivationFlowScreen } from "./screens/admin/AdminDeactivationFlowScreen";
import { AdminInternalChatListScreen } from "./screens/admin/AdminInternalChatListScreen";
import { AdminInternalChatThreadScreen } from "./screens/admin/AdminInternalChatThreadScreen";
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
import { MasterInternalChatListScreen } from "./screens/MasterInternalChatListScreen";
import { MasterInternalChatThreadScreen } from "./screens/MasterInternalChatThreadScreen";
import { MasterOnboardingScreen } from "./screens/MasterOnboardingScreen";
import { MasterPickerScreen } from "./screens/MasterPickerScreen";
import { MasterNotificationSettingsScreen } from "./screens/MasterNotificationSettingsScreen";
import { MasterProfileScreen } from "./screens/MasterProfileScreen";
import { MasterScheduleScreen } from "./screens/MasterScheduleScreen";
import { MasterSettingsScreen } from "./screens/MasterSettingsScreen";
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
      {/* Master ↔ admin internal chat — admin queue ("Чаты с мастерами"). */}
      <Route
        path="/admin/internal-chat"
        element={<AdminInternalChatListScreen me={me} />}
      />
      <Route
        path="/admin/internal-chat/threads/:threadId"
        element={<AdminInternalChatThreadScreen me={me} />}
      />
      {/*
        Legacy /admin/chats path (used by AdminTabBar) → redirect to the
        live internal-chat surface. Keeps deep-links from older bot DMs
        working.
      */}
      <Route
        path="/admin/chats"
        element={<Navigate to="/admin/internal-chat" replace />}
      />
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
      {/* M7 notification settings (Bundle B / item 3) */}
      <Route
        path="/master/settings/notifications"
        element={<MasterNotificationSettingsScreen />}
      />
      {/* M8 minimal — logout-only (full M8 deferred post-pilot) */}
      <Route
        path="/master/settings"
        element={<MasterSettingsScreen />}
      />
      {/* Internal chat «Со студией» (master-admin internal-chat handoff §3) */}
      <Route
        path="/master/internal-chat"
        element={<MasterInternalChatListScreen />}
      />
      <Route
        path="/master/internal-chat/threads/:threadId"
        element={<MasterInternalChatThreadScreen />}
      />
      {/* Default + unknown — land on dashboard. */}
      <Route path="*" element={<Navigate to="/master/dashboard" replace />} />
    </Routes>
  );
}

/**
 * Storage key + helpers for remembering which top-level surface the
 * solo / dual-role user last picked (or last navigated into). We use
 * plain ``localStorage`` with the ``max:`` prefix the rest of the app
 * uses for the DeviceStorage fallback (see ``setDeviceStorage`` in
 * lib/max-sdk.ts). Synchronous access is required because we read it
 * during the initial render to pick a landing route. The real MAX
 * DeviceStorage bridge is async (callback-based), so we deliberately
 * stick to localStorage — best-effort, gracefully ignored when
 * unavailable (private mode / SSR).
 */
const UNIFIED_LAST_SURFACE_KEY = "max:unified_last_surface";
type UnifiedSurface = "admin" | "master";

function readLastSurface(): UnifiedSurface | null {
  if (typeof window === "undefined" || !window.localStorage) return null;
  try {
    const v = window.localStorage.getItem(UNIFIED_LAST_SURFACE_KEY);
    return v === "admin" || v === "master" ? v : null;
  } catch {
    return null;
  }
}

function writeLastSurface(s: UnifiedSurface): void {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(UNIFIED_LAST_SURFACE_KEY, s);
  } catch {
    /* private mode / quota — best effort */
  }
}

/**
 * Surface chooser for users with BOTH admin and master roles (solo
 * provider OR dual-role team member). Surfaces two big buttons that
 * jump into the respective surface and persist the choice so re-opens
 * land where the user last was.
 *
 * Memory ref: project_solo_provider_universal_ui — universal UI with
 * smart defaults; tabbed surface keeps cognitive separation between
 * «сейчас я как админ» and «сейчас я как мастер».
 */
function UnifiedLanding({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const goAdmin = useCallback(() => {
    writeLastSurface("admin");
    navigate("/admin/team");
  }, [navigate]);
  const goMaster = useCallback(() => {
    writeLastSurface("master");
    navigate("/master/dashboard");
  }, [navigate]);

  const userName = me.user.name || "мастер";

  return (
    <div className="screen">
      <h1 className="screen__title">Здравствуйте, {userName}!</h1>
      <p style={{ color: "var(--c-text-secondary)", marginTop: 0 }}>
        У вас два режима в этом салоне — выберите, с чего начать.
      </p>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--s-3)",
          marginTop: "var(--s-4)",
        }}
      >
        <button
          type="button"
          onClick={goAdmin}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: "var(--s-1)",
            padding: "var(--s-4)",
            minHeight: 88,
            background: "var(--c-surface-1)",
            border: "1px solid var(--c-divider)",
            borderRadius: "var(--r-md)",
            textAlign: "left",
          }}
          aria-label="Перейти в Салон"
        >
          <span style={{ fontSize: "var(--font-size-300)", fontWeight: 600 }}>
            🏢 Салон
          </span>
          <span
            style={{
              fontSize: "var(--font-size-200)",
              color: "var(--c-text-secondary)",
            }}
          >
            Команда, услуги, запросы графика
          </span>
        </button>
        <button
          type="button"
          onClick={goMaster}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "flex-start",
            gap: "var(--s-1)",
            padding: "var(--s-4)",
            minHeight: 88,
            background: "var(--c-surface-1)",
            border: "1px solid var(--c-divider)",
            borderRadius: "var(--r-md)",
            textAlign: "left",
          }}
          aria-label="Открыть профиль мастера"
        >
          <span style={{ fontSize: "var(--font-size-300)", fontWeight: 600 }}>
            👤 Мой профиль мастера
          </span>
          <span
            style={{
              fontSize: "var(--font-size-200)",
              color: "var(--c-text-secondary)",
            }}
          >
            Моё расписание, диалоги, черновики
          </span>
        </button>
      </div>
    </div>
  );
}

/**
 * Wrapper that mounts BOTH /admin/* and /master/* route trees in a
 * single top-level <Routes> tree, so deep links to either prefix work
 * for users with overlapping roles (solo provider / dual-role team
 * member). Top-level `/` lands on UnifiedLanding — a manual chooser
 * that respects the persisted last-surface choice.
 *
 * Side-effect: every navigation into /admin/* or /master/* updates the
 * persisted last-surface flag (via UnifiedSurfaceTracker mounted on
 * the route element). On re-open, the chooser auto-redirects to the
 * last-chosen surface if one is recorded.
 *
 * Implementation note: we deliberately do NOT delegate to the existing
 * AdminRoutes / MasterRoutes components (which each define their own
 * <Routes> with absolute paths). Nesting two <Routes> roots with
 * absolute paths under <Route path="/foo/*"> is fragile in v6 — the
 * inner Routes match against the URL suffix, so absolute paths like
 * "/admin/team" don't resolve. Instead we re-declare the routes here
 * inline. Each inner element is the SAME screen component (no new
 * screens are introduced), so the route map stays in sync with the
 * single-role surfaces by sharing the underlying screens.
 */
function UnifiedSurfaceTracker({ surface }: { surface: UnifiedSurface }) {
  // Run once on mount of a route element — records that the user is
  // currently inside this surface. The chooser reads this on next
  // open to auto-redirect.
  useEffect(() => {
    writeLastSurface(surface);
  }, [surface]);
  return null;
}

function UnifiedLandingOrRedirect({ me }: { me: MeResponse }) {
  // If we have a persisted last-surface, jump directly into it. Otherwise
  // show the chooser. We do this with a <Navigate> on mount.
  const last = readLastSurface();
  if (last === "admin") {
    return <Navigate to="/admin/team" replace />;
  }
  if (last === "master") {
    return <Navigate to="/master/dashboard" replace />;
  }
  return <UnifiedLanding me={me} />;
}

function UnifiedAdminMasterRoutes({ me }: { me: MeResponse }) {
  return (
    <Routes>
      {/* --- Admin surface ---------------------------------------- */}
      <Route
        path="/admin/team"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminTeamScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/team/invite"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminInviteMasterScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/team/:masterId/deactivate"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminDeactivationFlowScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/team/:masterId"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminMasterDetailScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/services"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminServicesMatrixScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/availability-requests"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminAvailabilityRequestsScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/internal-chat"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminInternalChatListScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/internal-chat/threads/:threadId"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminInternalChatThreadScreen me={me} />
          </>
        }
      />
      <Route
        path="/admin/chats"
        element={<Navigate to="/admin/internal-chat" replace />}
      />
      <Route
        path="/admin/settings"
        element={
          <>
            <UnifiedSurfaceTracker surface="admin" />
            <AdminSettingsPlaceholderScreen />
          </>
        }
      />
      {/* --- Master surface --------------------------------------- */}
      <Route
        path="/onboarding/master"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterOnboardingScreen />
          </>
        }
      />
      <Route
        path="/master/dashboard"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterDashboardScreen />
          </>
        }
      />
      <Route
        path="/master/schedule"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterScheduleScreen />
          </>
        }
      />
      <Route
        path="/master/conversations"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterConversationsScreen />
          </>
        }
      />
      <Route
        path="/master/conversations/:id"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterConversationDetailScreen />
          </>
        }
      />
      <Route
        path="/master/profile"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterProfileScreen />
          </>
        }
      />
      <Route
        path="/master/settings/notifications"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterNotificationSettingsScreen />
          </>
        }
      />
      <Route
        path="/master/settings"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterSettingsScreen />
          </>
        }
      />
      <Route
        path="/master/internal-chat"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterInternalChatListScreen />
          </>
        }
      />
      <Route
        path="/master/internal-chat/threads/:threadId"
        element={
          <>
            <UnifiedSurfaceTracker surface="master" />
            <MasterInternalChatThreadScreen />
          </>
        }
      />
      {/* --- Landing / fallback ----------------------------------- */}
      <Route path="/" element={<UnifiedLandingOrRedirect me={me} />} />
      <Route path="*" element={<Navigate to="/" replace />} />
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
    const hasAdmin = me.is_owner || me.is_admin || me.is_receptionist;
    const hasMaster = me.is_master;
    // Inclusive routing — issue #79 (memory:
    // project_solo_provider_universal_ui). Solo provider Olga (owner +
    // admin + master in one tenant) and dual-role team members get
    // access to BOTH /admin/* and /master/* via a unified surface; the
    // previous exclusive cascade locked them out of /master/*.
    if (hasAdmin && hasMaster) {
      return <UnifiedAdminMasterRoutes me={me} />;
    }
    if (hasAdmin) return <AdminRoutes me={me} />;
    if (hasMaster) return <MasterRoutes />;
    return <CustomerRoutes />;
  }

  // Network / 5xx — customer fallback with a retry banner.
  return <CustomerFallbackWithBanner onRetry={() => void loadMe()} />;
}
