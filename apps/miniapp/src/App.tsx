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

import type React from "react";
import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

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

/**
 * Shared admin route elements — single source of truth consumed by both
 * `AdminRoutes` (single-role admin user) and `UnifiedAdminMasterRoutes`
 * (solo provider / dual-role). PRE_PILOT fix for issue #79: prevents
 * route-list drift where a future PR adding an admin route to one
 * component would silently deny access to the other class of users.
 *
 * React Router v6 supports `<Route>` elements wrapped in a Fragment as
 * children of `<Routes>` — the Routes component flattens nested
 * fragments before matching.
 *
 * Surface tracking (persisting last-chosen `admin` vs `master` for the
 * unified-landing redirect heuristic) is now done at the top of
 * `UnifiedAdminMasterRoutes` via a single `useLocation()` listener
 * keyed off the pathname prefix — see #746. The previous per-route
 * `<UnifiedSurfaceTracker>` mount pattern re-wrote on every SPA
 * back/forward route mount (cheap but redundant) and added JSX noise.
 */
function adminRouteElements(me: MeResponse): React.ReactNode {
  return (
    <>
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
    </>
  );
}

/**
 * Shared master route elements — single source of truth consumed by
 * both `MasterRoutes` (single-role master user) and
 * `UnifiedAdminMasterRoutes` (solo provider / dual-role). See
 * `adminRouteElements` for the rationale.
 */
function masterRouteElements(): React.ReactNode {
  return (
    <>
      <Route
        path="/onboarding/master"
        element={<MasterOnboardingScreen />}
      />
      <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
      <Route path="/master/schedule" element={<MasterScheduleScreen />} />
      <Route
        path="/master/conversations"
        element={<MasterConversationsScreen />}
      />
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
      <Route path="/master/settings" element={<MasterSettingsScreen />} />
      {/* Internal chat «Со студией» (master-admin internal-chat handoff §3) */}
      <Route
        path="/master/internal-chat"
        element={<MasterInternalChatListScreen />}
      />
      <Route
        path="/master/internal-chat/threads/:threadId"
        element={<MasterInternalChatThreadScreen />}
      />
    </>
  );
}

/**
 * Catch-all route redirect with developer-mode logging — issue #748.
 *
 * Previously the catch-all routes (`<Route path="*" element={<Navigate
 * to="/foo" replace />} />`) silently redirected typos / stale deep
 * links with no feedback. A user who pasted `/admin/teem` would land
 * silently on `/admin/team` (or, in the unified surface, possibly on
 * the master dashboard via the auto-restore heuristic) and have no
 * idea why their URL didn't go where they expected.
 *
 * We surface this in dev with a `console.warn` (so future agents
 * notice when they typo a path during testing). In prod we still
 * redirect silently — Mini Apps don't really expose a URL bar to end
 * users, so a transient toast was rejected as unnecessary noise (no
 * imperative-API Snackbar in the codebase; the existing controlled
 * Snackbar requires lifting state into the route component, which is
 * overkill for the redirect-on-mount flow). If users start manually
 * editing URLs in future channels (web sidebar, etc.) we can revisit.
 */
function CatchAllRedirect({ to }: { to: string }) {
  const location = useLocation();
  useEffect(() => {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(
        `[App] Unknown route ${location.pathname} — redirecting to ${to}`,
      );
    }
  }, [location.pathname, to]);
  return <Navigate to={to} replace />;
}

/** Routes for admin / owner / receptionist roles. */
function AdminRoutes({ me }: { me: MeResponse }) {
  return (
    <Routes>
      {adminRouteElements(me)}
      {/* Default + unknown — land on team. */}
      <Route path="*" element={<CatchAllRedirect to="/admin/team" />} />
    </Routes>
  );
}

/** Routes for the master role (existing M0-M6 surface). */
function MasterRoutes() {
  return (
    <Routes>
      {masterRouteElements()}
      {/* Default + unknown — land on dashboard. */}
      <Route path="*" element={<CatchAllRedirect to="/master/dashboard" />} />
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
 * persisted last-surface flag via a single top-level `useLocation()`
 * listener (see #746 — replaces the previous per-route
 * `UnifiedSurfaceTracker` mount, which re-wrote on every SPA
 * back/forward and added JSX noise to every route declaration). The
 * chooser reads this on next open to auto-redirect.
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
  // Top-level surface tracker (#746) — single listener keyed off the
  // pathname prefix. Fires once on each real navigation (vs the
  // previous per-route mount which re-wrote on every back/forward).
  // The landing `/` route deliberately does NOT write — the user
  // hasn't picked a surface yet at that point.
  const location = useLocation();
  useEffect(() => {
    if (location.pathname.startsWith("/admin/")) {
      writeLastSurface("admin");
    } else if (location.pathname.startsWith("/master/")) {
      writeLastSurface("master");
    }
  }, [location.pathname]);

  return (
    <Routes>
      {/* --- Admin surface ---------------------------------------- */}
      {adminRouteElements(me)}
      {/* --- Master surface --------------------------------------- */}
      {masterRouteElements()}
      {/* --- Landing / fallback ----------------------------------- */}
      <Route path="/" element={<UnifiedLandingOrRedirect me={me} />} />
      <Route path="*" element={<CatchAllRedirect to="/" />} />
    </Routes>
  );
}

/**
 * Solo provider unified surface — Tau §5 MVP.
 *
 * Activated only when `me.is_solo_provider === true` (W4 PR #760 added
 * the field to `/api/v1/me`; the helper rule is `len(active_staff_user_ids
 * ∪ active_master_user_ids) == 1` per Tau §3.1). For these tenants the
 * chooser used by `UnifiedAdminMasterRoutes` is overkill — Ольга is the
 * only person in her tenant, so we collapse everything to a single 8-tab
 * shell and skip the «Салон vs Мой профиль мастера» pick.
 *
 * Memory ref: `project_solo_provider_universal_ui` (founder 2026-05-25).
 *
 * Tab → screen mapping (decided here; documented in PR body):
 *   📋 Мой день   → MasterDashboardScreen  (today agenda; reuse)
 *   📅 Записи     → MasterScheduleScreen   (booking calendar; reuse)
 *   👥 Клиенты    → SoonScreen (no screen built — placeholder)
 *   💼 Услуги     → SoonScreen (AdminServicesMatrixScreen exists but is
 *                   team-tenant overkill for 1-master — separate solo
 *                   screen tracked post-pilot)
 *   ⏰ Расписание → MasterScheduleScreen   (same screen as Записи; the
 *                   separate working-hours editor is not built yet)
 *   💰 Доходы     → SoonScreen
 *   ⭐ Отзывы     → SoonScreen (per pilot runbook 3.2.3 — N/A reviews
 *                   not built)
 *   🤖 AI         → MasterConversationsScreen (M5 list w/ AI drafts —
 *                   most pragmatic «chat с Ayla» surface today)
 *
 * Legacy `/admin/*` + `/master/*` routes are also mounted inside this
 * shell so deep-links from bot DMs / bookmarks keep working — Ольга
 * who has the URL to `/admin/services` still gets there. The bottom nav
 * just doesn't surface those paths anymore.
 */
const SOLO_TABS: ReadonlyArray<{
  path: string;
  label: string;
  icon: string;
  ariaLabel: string;
}> = [
  { path: "/solo/my-day", label: "День", icon: "📋", ariaLabel: "Мой день" },
  { path: "/solo/bookings", label: "Записи", icon: "📅", ariaLabel: "Записи" },
  { path: "/solo/clients", label: "Клиенты", icon: "👥", ariaLabel: "Клиенты" },
  { path: "/solo/services", label: "Услуги", icon: "💼", ariaLabel: "Услуги и цены" },
  { path: "/solo/schedule", label: "Расп.", icon: "⏰", ariaLabel: "Расписание" },
  { path: "/solo/earnings", label: "Доходы", icon: "💰", ariaLabel: "Доходы" },
  { path: "/solo/reviews", label: "Отзывы", icon: "⭐", ariaLabel: "Отзывы" },
  { path: "/solo/ai", label: "AI", icon: "🤖", ariaLabel: "AI-помощник" },
];

function SoloBottomNav() {
  const location = useLocation();
  return (
    <nav className="solo-tabbar" aria-label="Основная навигация">
      {SOLO_TABS.map((t) => {
        const isActive = location.pathname.startsWith(t.path);
        return (
          <Link
            key={t.path}
            to={t.path}
            className={`solo-tabbar__tab${isActive ? " solo-tabbar__tab--active" : ""}`}
            aria-current={isActive ? "page" : undefined}
            aria-label={t.ariaLabel}
          >
            <span className="solo-tabbar__icon" aria-hidden="true">
              {t.icon}
            </span>
            <span className="solo-tabbar__label">{t.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

/**
 * Empty-state «скоро» placeholder for the four solo tabs whose
 * underlying screens haven't shipped (Клиенты / Услуги / Доходы /
 * Отзывы). Voice follows Tau §5.4 — frame as a promise, not as
 * «functionality is broken». Tracking issues filed separately
 * post-PR; the slug is rendered as a small debug breadcrumb so QA
 * can repro from a screenshot.
 */
function SoonScreen({ tab, slug }: { tab: string; slug: string }) {
  return (
    <div className="screen soon-screen">
      <span className="soon-screen__icon" aria-hidden="true">
        🌿
      </span>
      <h1 className="soon-screen__title">«{tab}» — скоро</h1>
      <p className="soon-screen__body">
        Этот раздел появится в следующих обновлениях. Пока работаю над тем,
        что уже есть — день, записи, расписание.
      </p>
      <p
        className="soon-screen__body"
        style={{ fontSize: "var(--font-size-100)", opacity: 0.6 }}
      >
        {slug}
      </p>
    </div>
  );
}

function UnifiedSoloSurface({ me }: { me: MeResponse }) {
  return (
    <div className="solo-surface">
      <Routes>
        {/* Default landing — Tau §5.1 specifies «Мой день» as solo home. */}
        <Route path="/" element={<Navigate to="/solo/my-day" replace />} />

        {/* Tabs with real screens — reuse existing master surface. */}
        <Route path="/solo/my-day" element={<MasterDashboardScreen />} />
        <Route path="/solo/bookings" element={<MasterScheduleScreen />} />
        {/* Schedule shares the master schedule screen for now — separate
         * working-hours editor lives in MM3 backlog (not built). */}
        <Route path="/solo/schedule" element={<MasterScheduleScreen />} />
        <Route path="/solo/ai" element={<MasterConversationsScreen />} />

        {/* Tabs whose screens haven't been built yet — placeholder. */}
        <Route
          path="/solo/clients"
          element={<SoonScreen tab="Клиенты" slug="solo-clients-screen" />}
        />
        <Route
          path="/solo/services"
          element={
            <SoonScreen tab="Услуги и цены" slug="solo-services-screen" />
          }
        />
        <Route
          path="/solo/earnings"
          element={<SoonScreen tab="Доходы" slug="solo-earnings-screen" />}
        />
        <Route
          path="/solo/reviews"
          element={<SoonScreen tab="Отзывы" slug="solo-reviews-screen" />}
        />

        {/* Legacy admin/master routes still accessible via deep link.
         * The bot DM might link directly to `/admin/services` or
         * `/master/conversations/:id` — those must keep working even
         * though the solo bottom nav doesn't surface them. */}
        {adminRouteElements(me)}
        {masterRouteElements()}

        {/* Catch-all → land on solo home. */}
        <Route path="*" element={<CatchAllRedirect to="/solo/my-day" />} />
      </Routes>
      <SoloBottomNav />
    </div>
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

  // Round-1 FOLLOW_UP cleanup (#79): when the resolved role pattern is
  // single (admin-only OR master-only OR customer-only) OR solo (Tau §5
  // unified surface — chooser bypassed), drop any stale
  // unified-last-surface key. Prevents a dead key from lingering after
  // a role revocation (e.g. owner who was demoted to master-only and
  // had previously chosen the admin surface) OR a team → solo
  // transition (Tau §9.4 — tenant shrinks to 1 person, chooser no
  // longer renders). Without this, a team-mode visit that wrote
  // `master:unified_last_surface=admin` would leak through to a
  // post-shrink solo session as an orphan key.
  useEffect(() => {
    if (boot.status !== "ready" || !boot.me) return;
    const hasAdmin =
      boot.me.is_owner || boot.me.is_admin || boot.me.is_receptionist;
    const hasMaster = boot.me.is_master;
    const isSolo = boot.me.is_solo_provider === true;
    // The chooser is rendered only when `hasAdmin && hasMaster && !isSolo`.
    // Any other resolved shape => the persisted key is dead weight.
    const choosing = hasAdmin && hasMaster && !isSolo;
    if (!choosing) {
      if (typeof window === "undefined" || !window.localStorage) return;
      try {
        window.localStorage.removeItem(UNIFIED_LAST_SURFACE_KEY);
      } catch {
        /* SSR / private mode / quota — best effort */
      }
    }
  }, [boot.status, boot.me]);

  if (boot.status === "loading") return <SplashScreen />;
  if (boot.status === "no_role") {
    return <NoRoleScreen onRetry={() => void loadMe()} />;
  }

  if (boot.status === "ready" && boot.me) {
    const me = boot.me;
    const hasAdmin = me.is_owner || me.is_admin || me.is_receptionist;
    const hasMaster = me.is_master;
    // Solo provider hint from W4 (`is_solo_provider(tenant)`) per Tau
    // §3.1 — true only when the tenant has exactly one distinct active
    // person. Missing field → false (graceful fallback for older
    // backends that haven't shipped #760 yet).
    const isSolo = me.is_solo_provider === true;
    // Routing cascade — Tau §5 + memory project_solo_provider_universal_ui.
    //
    //   1. Solo provider with ANY admin/master role → 8-tab unified
    //      solo surface. Skips the chooser entirely; Olga lands on
    //      /solo/my-day. Covers her dual roles (owner+admin+master)
    //      without forcing «Салон vs Мой профиль» pick.
    //   2. Team dual-role (NOT solo, has both admin AND master) →
    //      existing chooser surface from PR #753. Татьяна owner+master
    //      with junior masters under her keeps the «Салон»/«Мой
    //      профиль мастера» split.
    //   3-5. Single-role cascade unchanged.
    if (isSolo && (hasAdmin || hasMaster)) {
      return <UnifiedSoloSurface me={me} />;
    }
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
