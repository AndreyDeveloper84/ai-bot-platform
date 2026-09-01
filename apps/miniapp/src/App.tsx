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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "./lib/api";
import { getMe, type MeResponse } from "./lib/admin-api";
import { getStartPayload, parseStartRoute } from "./lib/max-sdk";
import {
  SurfaceModeContext,
  type SurfaceModeContextValue,
} from "./components/SurfaceSwitch";
import {
  clearLastSurface,
  readLastSurface,
  requestSurfaceChooser,
  useLastSurface,
  useSurfaceChooserRequested,
  writeLastSurface,
  type UnifiedSurface,
} from "./state/surface";
import { BootReloadContext } from "./state/boot";
import { AdminAvailabilityRequestsScreen } from "./screens/admin/AdminAvailabilityRequestsScreen";
import { AdminDeactivationFlowScreen } from "./screens/admin/AdminDeactivationFlowScreen";
import { AdminInternalChatListScreen } from "./screens/admin/AdminInternalChatListScreen";
import { AdminInternalChatThreadScreen } from "./screens/admin/AdminInternalChatThreadScreen";
import { AdminInviteMasterScreen } from "./screens/admin/AdminInviteMasterScreen";
import { AdminMasterDetailScreen } from "./screens/admin/AdminMasterDetailScreen";
import { AdminNewBookingScreen } from "./screens/admin/AdminNewBookingScreen";
import { AdminPeopleScreen } from "./screens/admin/AdminPeopleScreen";
import { AdminSalonDayScreen } from "./screens/admin/AdminSalonDayScreen";
import { AdminServicesMatrixScreen } from "./screens/admin/AdminServicesMatrixScreen";
import { AdminSettingsPlaceholderScreen } from "./screens/admin/AdminSettingsPlaceholderScreen";
import { AdminStaffAccessScreen } from "./screens/admin/AdminStaffAccessScreen";
import { AdminTeamScreen } from "./screens/admin/AdminTeamScreen";
import { BookingConfirmScreen } from "./screens/BookingConfirmScreen";
import { BookingSuccessScreen } from "./screens/BookingSuccessScreen";
import { BookingWhenScreen } from "./screens/BookingWhenScreen";
import { CatalogScreen } from "./screens/CatalogScreen";
import { CustomerBookingConfirmScreen } from "./screens/CustomerBookingConfirmScreen";
import { CustomerBookingDetailScreen } from "./screens/CustomerBookingDetailScreen";
import { CustomerBookingSuccessScreen } from "./screens/CustomerBookingSuccessScreen";
import { CustomerCatalogScreen } from "./screens/CustomerCatalogScreen";
import { CustomerMasterDetailScreen } from "./screens/CustomerMasterDetailScreen";
import { CustomerProfileScreen } from "./screens/CustomerProfileScreen";
import { CustomerNotificationSettingsScreen } from "./screens/CustomerNotificationSettingsScreen";
import { CustomerCardsScreen } from "./screens/CustomerCardsScreen";
import { CustomerRecordsScreen } from "./screens/CustomerRecordsScreen";
import { CustomerSlotsScreen } from "./screens/CustomerSlotsScreen";
import { CustomerWellnessDashboardScreen } from "./screens/CustomerWellnessDashboardScreen";
import { GoalSelectScreen } from "./screens/GoalSelectScreen";
import { FeedbackScreen } from "./screens/FeedbackScreen";
import { FoodScannerCaptureScreen } from "./screens/FoodScannerCaptureScreen";
import { FoodScannerDiaryScreen } from "./screens/FoodScannerDiaryScreen";
import { FoodScannerManualScreen } from "./screens/FoodScannerManualScreen";
import { FoodScannerProcessingScreen } from "./screens/FoodScannerProcessingScreen";
import { FoodScannerResultScreen } from "./screens/FoodScannerResultScreen";
import { FoodScannerSavedScreen } from "./screens/FoodScannerSavedScreen";
import { HelloScreen } from "./screens/HelloScreen";
import { RoleNotReadyScreen } from "./screens/RoleNotReadyScreen";
import { MasterConversationDetailScreen } from "./screens/MasterConversationDetailScreen";
import { MasterConversationsScreen } from "./screens/MasterConversationsScreen";
import { MasterCustomersScreen } from "./screens/MasterCustomersScreen";
import { MasterBillingScreen } from "./screens/MasterBillingScreen";
import { MasterDashboardScreen } from "./screens/MasterDashboardScreen";
import { MasterInternalChatListScreen } from "./screens/MasterInternalChatListScreen";
import { MasterInternalChatThreadScreen } from "./screens/MasterInternalChatThreadScreen";
import { MasterOnboardingScreen } from "./screens/MasterOnboardingScreen";
import { MasterPickerScreen } from "./screens/MasterPickerScreen";
import { MasterNotificationSettingsScreen } from "./screens/MasterNotificationSettingsScreen";
import { MasterProfileScreen } from "./screens/MasterProfileScreen";
import { MasterScheduleScreen } from "./screens/MasterScheduleScreen";
import { MasterServicesScreen } from "./screens/MasterServicesScreen";
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
      {/* Phase 2 — the salon's day. First tab in AdminTabBar. */}
      <Route path="/admin/day" element={<AdminSalonDayScreen />} />
      <Route path="/admin/booking/new" element={<AdminNewBookingScreen />} />
      <Route path="/admin/team" element={<AdminTeamScreen me={me} />} />
      <Route
        path="/admin/team/invite"
        element={<AdminInviteMasterScreen me={me} />}
      />
      {/*
        DRF-1061 block 2.4 — access codes. Sits beside `/admin/team/invite`
        rather than inside it: that screen CREATES a catalog master, this
        one GRANTS ACCESS to a person who already exists. The backend keeps
        the two endpoints apart for the same reason.
      */}
      <Route
        path="/admin/team/access"
        element={<AdminStaffAccessScreen me={me} />}
      />
      {/*
        The roster of PEOPLE — every role, both tables (ADR-0008). Owner
        only; the backend answers 403 to anyone else and the screen says
        so rather than rendering an error card.

        Declared before `/admin/team/:masterId`. React Router already
        ranks a static segment above a dynamic one, so `people` could
        never be read as a master id — the placement matches the
        convention the URLconf uses on the backend for the same
        collision, and costs nothing to keep explicit.
      */}
      <Route
        path="/admin/team/people"
        element={<AdminPeopleScreen me={me} />}
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
 * Jump to the screen the launch payload names, once per boot (DRF-1349).
 *
 * MAX carries an `open_app` button's `payload` into `initData` as
 * `start_param`, and `parseStartRoute` turns it into an in-app path.
 * This used to live inside `HelloScreen` — which is mounted only in
 * `CustomerRoutes`. Every other surface has its own catch-all
 * (`/admin/team`, `/master/dashboard`, `/solo/my-day`), so for anyone
 * holding a role the payload was read by nobody: MAX opens the Mini App
 * at `/`, the role cascade picked a route tree, and the tree's catch-all
 * won before anything looked at the payload.
 *
 * That is why it sits here, above the cascade, rather than being
 * repeated per surface. An owner who invites herself as a master boots
 * into `AdminRoutes`, and mounting `/onboarding/master` there without
 * this hook would have been an unreachable route — the same defect one
 * level up.
 *
 * `enabled` gates it on a finished `/me` boot, matching the previous
 * behaviour of only redirecting after auth resolved. The ref makes it
 * once-per-session: after the jump the user owns the navigation, and
 * `start_param` does not change for the life of the webview, so
 * re-running it would fight every subsequent `navigate`.
 */
function useStartParamRedirect(enabled: boolean): void {
  const navigate = useNavigate();
  const done = useRef(false);
  useEffect(() => {
    if (!enabled || done.current) return;
    const target = parseStartRoute(getStartPayload());
    done.current = true;
    if (target) navigate(target, { replace: true });
  }, [enabled, navigate]);
}

/**
 * The master-invitation onboarding route — mounted on EVERY surface
 * (DRF-1349).
 *
 * It used to live inside `masterRouteElements()`, which made it
 * reachable only for someone the backend already considers a master.
 * That is precisely the person who does not need it. `resolve_role`
 * (`apps/identity/services/role_resolver.py`) reports `is_master` only
 * for a CatalogMaster row that is ACCEPTED **and** linked to the calling
 * BotUser — and accepting the invitation is what this screen exists to
 * do. An invitee is PENDING and unlinked by definition, so `/api/v1/me`
 * returns `is_master: false`, the role cascade drops them onto the
 * customer surface, and the catch-all there renders `HelloScreen`. The
 * invitation died on a route that was never mounted, with no error
 * anywhere: the deeplink effect in `HelloScreen` navigated, the
 * catch-all rendered `HelloScreen` again, and the master saw the
 * greeting instead of the invitation.
 *
 * Mounted on every surface rather than only on the customer one because
 * the invitee's role is not knowable here: an owner adding herself as a
 * master boots into `AdminRoutes`, a returning master into
 * `MasterRoutes`, a solo provider into the unified surface. The screen
 * carries its own authorisation — every step of it goes through
 * `/onboarding/claim` and `/onboarding/accept`, which validate the
 * token against the caller's own MAX session — so mounting it widely
 * grants nothing: without a valid token for *that* BotUser the screen
 * only renders its own error states.
 */
function inviteOnboardingRouteElements(): React.ReactNode {
  return <Route path="/onboarding/master" element={<MasterOnboardingScreen />} />;
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
      {inviteOnboardingRouteElements()}
      <Route path="/master/dashboard" element={<MasterDashboardScreen />} />
      {/* D7 billing — subscription status + card binding (money path) */}
      <Route path="/master/billing" element={<MasterBillingScreen />} />
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
      {/* An owner who invites herself as a master boots here, not into
          MasterRoutes — she is not a master until she accepts. Without
          this the catch-all below would swallow her invitation and land
          her on the team screen (DRF-1349). Added on the surface rather
          than inside `adminRouteElements` so the unified surfaces, which
          already mount it via `masterRouteElements`, don't declare it
          twice. */}
      {inviteOnboardingRouteElements()}
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
 * Surface chooser for users who hold more than one role (solo provider
 * OR dual-role team member). Big buttons that jump into the respective
 * surface and persist the choice so re-opens land where the user last
 * was.
 *
 * Storage helpers now live in `state/surface.ts` — see the header there
 * for why they had to move out of this module.
 *
 * Memory ref: project_solo_provider_universal_ui — universal UI with
 * smart defaults; tabbed surface keeps cognitive separation between
 * «сейчас я как админ» and «сейчас я как мастер».
 *
 * The third option — «Клиент» — is the fix for the surface the owner
 * could never reach. She is owner+master, so the cascade below always
 * matched an earlier branch and `CustomerRoutes` was effectively
 * dead code for anyone with a role. The chooser is the entry; the
 * «Сменить режим» action (SurfaceSwitch.tsx) is the way back.
 *
 * Solo providers see a single «Моя работа» card instead of the
 * Салон/Мастер split: their surface is already unified (Tau §5.1), so
 * splitting it in the chooser would be a distinction without a
 * difference. They still get «Клиент».
 */
function SurfaceCard({
  icon,
  title,
  subtitle,
  ariaLabel,
  onClick,
}: {
  icon: string;
  title: string;
  subtitle: string;
  ariaLabel: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
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
      aria-label={ariaLabel}
    >
      <span style={{ fontSize: "var(--font-size-300)", fontWeight: 600 }}>
        {icon} {title}
      </span>
      <span
        style={{
          fontSize: "var(--font-size-200)",
          color: "var(--c-text-secondary)",
        }}
      >
        {subtitle}
      </span>
    </button>
  );
}

function UnifiedLanding({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const isSolo = me.is_solo_provider === true;
  const pick = useCallback(
    (surface: UnifiedSurface, path: string) => {
      writeLastSurface(surface);
      navigate(path);
    },
    [navigate],
  );

  const userName = me.user.name || "мастер";

  return (
    <div className="screen">
      <h1 className="screen__title">Здравствуйте, {userName}!</h1>
      <p style={{ color: "var(--c-text-secondary)", marginTop: 0 }}>
        У вас несколько режимов — выберите, с чего начать.
      </p>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: "var(--s-3)",
          marginTop: "var(--s-4)",
        }}
      >
        {isSolo ? (
          <SurfaceCard
            icon="💼"
            title="Моя работа"
            subtitle="День, записи, клиенты, услуги"
            ariaLabel="Перейти к своей работе"
            onClick={() => pick("master", "/solo/my-day")}
          />
        ) : (
          <>
            <SurfaceCard
              icon="🏢"
              title="Салон"
              subtitle="Команда, услуги, запросы графика"
              ariaLabel="Перейти в Салон"
              onClick={() => pick("admin", "/admin/team")}
            />
            <SurfaceCard
              icon="👤"
              title="Мой профиль мастера"
              subtitle="Моё расписание, диалоги, черновики"
              ariaLabel="Открыть профиль мастера"
              onClick={() => pick("master", "/master/dashboard")}
            />
          </>
        )}
        <SurfaceCard
          icon="👤"
          title="Клиент"
          subtitle="Мои записи, услуги, запись к мастеру"
          ariaLabel="Открыть клиентскую часть"
          onClick={() => pick("customer", "/customer/main")}
        />
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
  //
  // `"customer"` never reaches this point: the cascade in `App` swaps the
  // whole tree for `CustomerRoutes` before this component is mounted, so
  // the fall-through below (→ chooser) is defensive only.
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
  //
  // Customer paths cannot be tracked here and must not be: this
  // component is only mounted while the caller is in the admin/master
  // tree. Choosing «Клиент» flips the cascade in `App`, which unmounts
  // this whole subtree before any `/customer/*` navigation happens, so
  // the stored `"customer"` can never be overwritten with `"admin"` by
  // the prefix check below.
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
 * Solo provider unified surface — Variant B navigation per Tau §3 verdict
 * (master-solo-surface.md r1 2026-05-26). Rebuilt from PR #798's 8-tab
 * horizontal-scroll Variant A to the 5-tab + «Ещё» bottom-sheet pattern
 * mandated by founder + Tau review for WCAG 2.5.8 compliance on 360dp
 * viewport (360 ÷ 5 = 72dp per tab, no label truncation).
 *
 * Activated only when `me.is_solo_provider === true` (W4 PR #760).
 *
 * Bottom-bar tabs (5):
 *   📋 День     → MasterDashboardScreen  (today agenda; reuse)
 *   📅 Записи   → MasterScheduleScreen   (booking calendar; reuse)
 *   👥 Клиенты  → MasterCustomersScreen  (Tier 2 read-only roster — this PR)
 *   💼 Услуги   → MasterServicesScreen   (Tier 2 read-only catalog — this PR)
 *   ⋯ Ещё      → opens bottom sheet (does NOT navigate)
 *
 * «Ещё» bottom sheet (Tau §3 spec):
 *   ⏰ Расписание   → MasterScheduleScreen  (same screen as Записи today)
 *   💰 Доходы       → SoonScreen            (post-pilot per pilot runbook)
 *   ⭐ Отзывы       → SoonScreen            (post-pilot)
 *   🤖 AI-помощник → MasterConversationsScreen (M5 + AI drafts)
 *   ──────────────
 *   👤 Профиль      → MasterProfileScreen
 *   ⚙ Настройки     → MasterSettingsScreen (M8 logout-only)
 *
 * Deep-link behaviour for /solo/more: a direct URL hit (e.g. from a stale
 * bookmark) redirects to /solo/my-day AND opens the sheet — Tau's
 * "implementation choice: simplest" instruction. Tap «Ещё» tab from the
 * bottom bar toggles the sheet without navigation.
 *
 * Legacy `/admin/*` + `/master/*` routes are still mounted so deep-links
 * from bot DMs keep working. The bottom nav just doesn't surface them.
 */
const SOLO_NAV_TABS: ReadonlyArray<{
  path: string;
  label: string;
  icon: string;
  ariaLabel: string;
  /** When true, taps toggle the «Ещё» sheet instead of navigating. */
  opensSheet?: boolean;
}> = [
  { path: "/solo/my-day", label: "День", icon: "📋", ariaLabel: "Мой день" },
  { path: "/solo/bookings", label: "Записи", icon: "📅", ariaLabel: "Записи" },
  { path: "/solo/customers", label: "Клиенты", icon: "👥", ariaLabel: "Клиенты" },
  { path: "/solo/services", label: "Услуги", icon: "💼", ariaLabel: "Услуги и цены" },
  { path: "/solo/more", label: "Ещё", icon: "⋯", ariaLabel: "Меню «Ещё»", opensSheet: true },
];

interface SoloMoreSheetItem {
  path: string;
  label: string;
  icon: string;
  ariaLabel: string;
  /** Divider sits between functional and settings items per Tau §3 mock. */
  trailingDivider?: boolean;
}

const SOLO_MORE_SHEET_ITEMS: ReadonlyArray<SoloMoreSheetItem> = [
  { path: "/solo/schedule", label: "Расписание", icon: "⏰", ariaLabel: "Расписание" },
  { path: "/solo/earnings", label: "Доходы", icon: "💰", ariaLabel: "Доходы" },
  { path: "/solo/reviews", label: "Отзывы", icon: "⭐", ariaLabel: "Отзывы" },
  {
    path: "/solo/ai",
    label: "AI-помощник",
    icon: "🤖",
    ariaLabel: "AI-помощник",
    trailingDivider: true,
  },
  { path: "/solo/profile", label: "Профиль", icon: "👤", ariaLabel: "Профиль" },
  { path: "/solo/settings", label: "Настройки", icon: "⚙", ariaLabel: "Настройки" },
];

/**
 * The «Салон» escape hatch — DRF-1149 safety net.
 *
 * The solo surface mounts every `/admin/*` route (see
 * `UnifiedSoloSurface`) but the five-tab bar and the sheet above surface
 * none of them, so until now the only way in was typing a URL. That was
 * fine while «solo» meant «one person». It stopped being fine when the
 * pilot salon — four masters, three of them never bridged to a BotUser —
 * was mis-counted as solo and lost its entire admin surface.
 *
 * The counting bug itself is fixed in `is_solo_provider`; this item is
 * the second lock on the same door. If the classifier is ever wrong
 * again, an owner or admin can still reach the team screen instead of
 * being stranded on a surface with no way out.
 *
 * Gated on the role flags, not on `is_solo_provider`: a genuine solo
 * provider IS an owner, and «Салон» is where she manages her catalog and
 * her services regardless of headcount. A master-only caller never sees
 * it — same rule the backend enforces at `@require_admin_role`.
 */
const SOLO_ADMIN_SHEET_ITEM: SoloMoreSheetItem = {
  path: "/admin/team",
  label: "Салон",
  icon: "🏢",
  ariaLabel: "Управление салоном",
  trailingDivider: true,
};

/**
 * Sheet items for this caller: the Tau §3 base list, plus «Салон» when
 * the caller holds an admin-side role. The divider moves onto «Салон» so
 * the functional / settings split from the Tau mock is preserved.
 */
function soloMoreSheetItems(me: MeResponse): ReadonlyArray<SoloMoreSheetItem> {
  const hasAdmin = me.is_owner || me.is_admin || me.is_receptionist;
  if (!hasAdmin) return SOLO_MORE_SHEET_ITEMS;
  return SOLO_MORE_SHEET_ITEMS.flatMap((it) =>
    it.trailingDivider
      ? [{ ...it, trailingDivider: false }, SOLO_ADMIN_SHEET_ITEM]
      : [it],
  );
}

/**
 * Bottom-bar nav for the solo surface. Five tabs; the «Ещё» tab does
 * NOT navigate — it toggles the sheet via the `onOpenSheet` callback.
 * Active-state highlighting tracks the actual route prefix so the
 * indicator stays put even after the user dismisses the sheet.
 */
function SoloBottomNav({
  onOpenSheet,
  sheetOpen,
  moreItems,
}: {
  onOpenSheet: () => void;
  sheetOpen: boolean;
  moreItems: ReadonlyArray<SoloMoreSheetItem>;
}) {
  const location = useLocation();
  return (
    <nav className="solo-tabbar" aria-label="Основная навигация">
      {SOLO_NAV_TABS.map((t) => {
        // «Ещё» is active iff sheet open OR pathname is in one of the
        // nested sheet items (deep-link case).
        const isMore = t.opensSheet === true;
        const matchesPath = location.pathname.startsWith(t.path);
        const matchesSheetItem =
          isMore &&
          moreItems.some((it) => location.pathname.startsWith(it.path));
        const isActive = isMore
          ? sheetOpen || matchesSheetItem
          : matchesPath;
        if (isMore) {
          return (
            <button
              key={t.path}
              type="button"
              className={`solo-tabbar__tab${isActive ? " solo-tabbar__tab--active" : ""}`}
              aria-label={t.ariaLabel}
              aria-haspopup="menu"
              aria-expanded={sheetOpen}
              onClick={onOpenSheet}
            >
              <span className="solo-tabbar__icon" aria-hidden="true">
                {t.icon}
              </span>
              <span className="solo-tabbar__label">{t.label}</span>
            </button>
          );
        }
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
 * «Ещё» bottom sheet (Tau §3). Modal-like overlay anchored to the
 * bottom edge; tap-outside dismisses, Escape dismisses. Each item is a
 * full-width row; tapping navigates AND auto-dismisses per Tau spec.
 *
 * Accessibility (round-1 adversarial Code Reviewer amendment):
 *   - role=dialog + aria-modal so SR users get the modal semantic.
 *   - Focus moves to the first item on open (was previously left on the
 *     «Ещё» trigger, which is outside the modal — SR users had no
 *     anchor inside the sheet).
 *   - Tab / Shift+Tab is trapped within the panel so keyboard users
 *     can't tab out of the modal into the backgrounded surface.
 *   - On close, focus is restored to whatever element opened the sheet
 *     (snapshot of document.activeElement at mount). This handles the
 *     bottom-bar trigger case AND the deep-link case (where there is
 *     no opening trigger — restoreFocus is a no-op then).
 */
function SoloMoreSheet({
  open,
  onClose,
  items,
}: {
  open: boolean;
  onClose: () => void;
  items: ReadonlyArray<SoloMoreSheetItem>;
}) {
  const navigate = useNavigate();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // Escape-key dismiss for keyboard users.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Focus management — round-1 amendment. Snapshot the previously
  // focused element on open, move focus into the panel, trap Tab/
  // Shift+Tab within the panel, restore focus on close.
  useEffect(() => {
    if (!open) return;
    // Snapshot the element that had focus when the sheet opened — we
    // restore to it on close (typically the «Ещё» bottom-tab button).
    if (typeof document !== "undefined") {
      const active = document.activeElement;
      restoreFocusRef.current =
        active instanceof HTMLElement ? active : null;
    }
    const panel = panelRef.current;
    if (!panel) return;

    // Move focus to the first focusable item on open so SR users land
    // inside the dialog instead of staying on the trigger.
    const firstItem = panel.querySelector<HTMLElement>(
      '[role="menuitem"], button',
    );
    firstItem?.focus();

    // Trap Tab/Shift+Tab within the panel.
    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab") return;
      if (!panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>(
        'button, [tabindex="0"], a[href]',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      // Restore focus on close — guard against the trigger being
      // unmounted (deep-link → /solo/more redirect case).
      const target = restoreFocusRef.current;
      if (target && typeof document !== "undefined" && document.contains(target)) {
        target.focus();
      }
      restoreFocusRef.current = null;
    };
  }, [open]);

  if (!open) return null;

  const handleItemClick = (path: string) => {
    onClose();
    navigate(path);
  };

  return (
    <div className="solo-more-sheet" role="presentation">
      {/* Backdrop — tap-outside dismiss. */}
      <button
        type="button"
        className="solo-more-sheet__backdrop"
        aria-label="Закрыть меню"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        className="solo-more-sheet__panel"
        role="dialog"
        aria-modal="true"
        aria-label="Меню «Ещё»"
      >
        <div className="solo-more-sheet__grip" aria-hidden="true" />
        <h2 className="solo-more-sheet__title">Ещё</h2>
        <ul className="solo-more-sheet__list">
          {items.map((it) => (
            <li key={it.path}>
              <button
                type="button"
                className="solo-more-sheet__item"
                aria-label={it.ariaLabel}
                onClick={() => handleItemClick(it.path)}
              >
                <span className="solo-more-sheet__item-icon" aria-hidden="true">
                  {it.icon}
                </span>
                <span className="solo-more-sheet__item-label">{it.label}</span>
              </button>
              {it.trailingDivider && (
                <hr className="solo-more-sheet__divider" aria-hidden="true" />
              )}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/**
 * Empty-state «скоро» placeholder for tabs in the «Ещё» sheet whose
 * underlying screens haven't shipped (Доходы / Отзывы post-pilot).
 * Voice follows Tau §5.4 — frame as a promise, not as «functionality is
 * broken». The slug is rendered as a small debug breadcrumb so QA can
 * repro from a screenshot.
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

/**
 * Lands the user on /solo/my-day for deep-link hits to /solo/more.
 *
 * Round-1 amendment (adversarial Code Reviewer): previously this
 * component called `onOpenSheet()` in a `useEffect` AND returned
 * `<Navigate to="/solo/my-day" replace />` in the same render. That
 * effect-then-Navigate ordering relied on React running the effect
 * before unmount on Navigate — which works in practice but is timing-
 * fragile and `<StrictMode>`-sensitive. We've moved the sheet-open
 * decision into `UnifiedSoloSurface`'s initial state (read from
 * `location.pathname === "/solo/more"` on mount) so this component
 * now just redirects synchronously.
 */
function SoloMoreLanding() {
  return <Navigate to="/solo/my-day" replace />;
}

function UnifiedSoloSurface({ me }: { me: MeResponse }) {
  const location = useLocation();
  // Round-1 amendment: read deep-link sheet-open state from the URL
  // on mount. If the user pasted `/solo/more` (e.g. stale bot DM
  // bookmark), the parent renders with `moreOpen=true` immediately —
  // no effect-then-Navigate race in SoloMoreLanding. The Navigate
  // away to /solo/my-day still fires from the route element below;
  // sheet state is already captured here.
  const [moreOpen, setMoreOpen] = useState<boolean>(
    () => location.pathname === "/solo/more",
  );
  const openSheet = useCallback(() => setMoreOpen(true), []);
  const closeSheet = useCallback(() => setMoreOpen(false), []);
  // «Салон» appears for admin-side callers only — see soloMoreSheetItems.
  const moreItems = useMemo(() => soloMoreSheetItems(me), [me]);

  // Defensive sync — if the user navigates TO /solo/more after mount
  // (e.g. via browser back to a stale URL), re-open the sheet. Initial
  // state already covers the mount case; this is a belt-and-braces
  // guard for in-session URL changes.
  useEffect(() => {
    if (location.pathname === "/solo/more") {
      setMoreOpen(true);
    }
  }, [location.pathname]);

  return (
    <div className="solo-surface">
      <Routes>
        {/* Default landing — Tau §5.1 specifies «Мой день» as solo home. */}
        <Route path="/" element={<Navigate to="/solo/my-day" replace />} />

        {/* Bottom-bar destinations. */}
        <Route path="/solo/my-day" element={<MasterDashboardScreen />} />
        <Route path="/solo/bookings" element={<MasterScheduleScreen />} />
        <Route path="/solo/customers" element={<MasterCustomersScreen />} />
        <Route path="/solo/services" element={<MasterServicesScreen />} />
        {/* /solo/more — deep-link only; redirects synchronously to
         * /solo/my-day. The parent (`UnifiedSoloSurface`) reads the URL
         * on mount and initialises `moreOpen=true` for this path, so
         * the sheet appears without an effect-ordering race. The bottom
         * bar tap path uses the click handler instead (no navigation). */}
        <Route path="/solo/more" element={<SoloMoreLanding />} />

        {/* «Ещё» sheet destinations. */}
        <Route path="/solo/schedule" element={<MasterScheduleScreen />} />
        <Route
          path="/solo/earnings"
          element={<SoonScreen tab="Доходы" slug="solo-earnings-screen" />}
        />
        <Route
          path="/solo/reviews"
          element={<SoonScreen tab="Отзывы" slug="solo-reviews-screen" />}
        />
        <Route path="/solo/ai" element={<MasterConversationsScreen />} />
        <Route path="/solo/profile" element={<MasterProfileScreen />} />
        <Route path="/solo/settings" element={<MasterSettingsScreen />} />

        {/* Legacy admin/master routes still accessible via deep link.
         * The bot DM might link directly to `/admin/services` or
         * `/master/conversations/:id` — those must keep working even
         * though the solo bottom nav doesn't surface them. */}
        {adminRouteElements(me)}
        {masterRouteElements()}

        {/* Catch-all → land on solo home. */}
        <Route path="*" element={<CatchAllRedirect to="/solo/my-day" />} />
      </Routes>
      <SoloBottomNav
        onOpenSheet={openSheet}
        sheetOpen={moreOpen}
        moreItems={moreItems}
      />
      <SoloMoreSheet open={moreOpen} onClose={closeSheet} items={moreItems} />
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
      {/*
        Customer booking flow F1-F5 — Tier 1 Priority 3 Phase B
        (Ayla-first reskin per docs/screens/customer-booking-flow.md).
        Runs alongside the legacy /catalog + /book/* routes; F1 reads
        the real mirror catalog since pilot phase 3.1. The legacy routes
        stay reachable for deep-links from bot DMs and reschedule
        flows.
      */}
      {/* Home = «Мои записи» (pilot phase 3.2, orchestrator decision):
          records on real data is the pilot home. The wellness dashboard
          (stub surface, gated) moves to /customer/wellness until
          S4/post-pilot. */}
      <Route path="/customer/main" element={<CustomerRecordsScreen />} />
      {/*
        DRF-1190 — the goal surface. Registered by the main window at the
        conversation window's request: the screen is theirs, App.tsx is
        not, and shipping the screen without a route would land an
        unreachable surface — "looks done, does nothing".

        The welcome skill's `open_goal_select` slug resolves here
        (_ROUTE_MAP in lib/max-sdk.ts), so this path is a contract with
        the bot, not just an internal link.
      */}
      <Route path="/customer/goal-select" element={<GoalSelectScreen />} />
      <Route
        path="/customer/wellness"
        element={<CustomerWellnessDashboardScreen />}
      />
      <Route path="/customer/catalog" element={<CustomerCatalogScreen />} />
      <Route
        path="/customer/masters/:masterId"
        element={<CustomerMasterDetailScreen />}
      />
      <Route
        path="/customer/masters/:masterId/slots"
        element={<CustomerSlotsScreen />}
      />
      <Route
        path="/customer/booking/confirm"
        element={<CustomerBookingConfirmScreen />}
      />
      <Route
        path="/customer/booking/success/:bookingId"
        element={<CustomerBookingSuccessScreen />}
      />
      {/* Tier 1 Priority 5 Phase B — customer records (Tau R1-R6).
          New canonical routes. Legacy /my-visits stays mounted for
          deep links from bot DMs + reschedule flows. */}
      <Route path="/customer/records" element={<CustomerRecordsScreen />} />
      <Route
        path="/customer/records/:bookingId"
        element={<CustomerBookingDetailScreen />}
      />
      <Route path="/my-visits" element={<MyVisitsScreen />} />
      <Route path="/my-visits/:bookingId" element={<MyVisitDetailScreen />} />
      <Route
        path="/my-visits/:bookingId/reschedule"
        element={<RescheduleScreen />}
      />
      {/* Tier 1 Priority 6 Phase B — customer profile tab (Tau R1-R6,
          deferred Variant 3 per tech-lead 2026-06-01). New canonical
          route. Legacy /me stays mounted for bot DM deeplinks. */}
      <Route path="/customer/profile" element={<CustomerProfileScreen />} />
      <Route
        path="/customer/notification-settings"
        element={<CustomerNotificationSettingsScreen />}
      />
      <Route path="/customer/cards" element={<CustomerCardsScreen />} />
      {/* Tier 1 Priority 7 Phase B — food scanner (Tau F1-F4 wizard +
          /дневник + manual fallback). Stubs with guardProd until W4
          ships miniapp_api proxy endpoints. */}
      <Route
        path="/customer/food-scanner/capture"
        element={<FoodScannerCaptureScreen />}
      />
      <Route
        path="/customer/food-scanner/processing"
        element={<FoodScannerProcessingScreen />}
      />
      <Route
        path="/customer/food-scanner/result"
        element={<FoodScannerResultScreen />}
      />
      <Route
        path="/customer/food-scanner/saved"
        element={<FoodScannerSavedScreen />}
      />
      <Route
        path="/customer/food-scanner/diary"
        element={<FoodScannerDiaryScreen />}
      />
      <Route
        path="/customer/food-scanner/manual"
        element={<FoodScannerManualScreen />}
      />
      <Route path="/me" element={<ProfileScreen />} />
      <Route path="/feedback/:bookingId" element={<FeedbackScreen />} />
      {/* DRF-1349 — the surface an invited master actually boots into.
          `/api/v1/me` returns is_master=false until the invitation is
          ACCEPTED and linked, so this is where the invite button lands.
          Before it was mounted here the `*` route below matched instead
          and rendered HelloScreen, which is how the invitation vanished
          into the greeting screen with no error anywhere. */}
      {inviteOnboardingRouteElements()}
      {/*
        DRF-1434 — роль ещё не выдана, но человек уже на /master/* или
        /admin/*. Раньше эти адреса доедал `*` ниже и рисовал
        `HelloScreen`: мастер, только что принявший приглашение и
        отправленный на `/master/dashboard`, получал клиентское
        приветствие внутри бота для мастеров, без единого признака
        ошибки. Объявлены ДО `*`, чтобы забрать префикс у него.

        Это страховка, а не основной путь: основной — `useReloadMe()`
        в `MasterOnboardingScreen`, после которого `/me` отдаёт
        `is_master: true` и монтируется `MasterRoutes`. Экран ниже
        виден, только если роль действительно не пришла.
      */}
      <Route path="/master/*" element={<RoleNotReadyScreen surface="master" />} />
      <Route path="/admin/*" element={<RoleNotReadyScreen surface="admin" />} />
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
  // Surface choice drives the cascade below, so it has to be reactive —
  // a bare localStorage read wouldn't re-render when the user picks
  // «Клиент» or taps «Сменить режим». See state/surface.ts.
  const surfacePref = useLastSurface();
  const chooserRequested = useSurfaceChooserRequested();
  const navigate = useNavigate();

  const requestChooser = useCallback(() => {
    clearLastSurface();
    requestSurfaceChooser();
    // Keep the URL honest — the chooser is rendered above every route
    // tree, and leaving a stale `/customer/profile` in the address bar
    // would send the user back into the surface they just left if they
    // reloaded mid-choice.
    navigate("/");
  }, [navigate]);

  // Value handed to leaf screens through SurfaceModeContext. `canSwitch`
  // is what keeps «Сменить режим» invisible to a single-role person:
  // a receptionist has one surface and a control offering to leave it
  // would be noise she cannot act on.
  const surfaceMode = useMemo<SurfaceModeContextValue>(
    () => ({
      canSwitch:
        boot.status === "ready" &&
        boot.me != null &&
        [
          Boolean(boot.me.is_owner || boot.me.is_admin || boot.me.is_receptionist),
          Boolean(boot.me.is_master),
        ].filter(Boolean).length > 1,
      requestChooser,
    }),
    [boot.status, boot.me, requestChooser],
  );

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

  useStartParamRedirect(boot.status === "ready");

  // Round-1 FOLLOW_UP cleanup (#79): when the resolved role pattern
  // leaves nothing to choose between, drop any stale
  // unified-last-surface key. Prevents a dead key from lingering after
  // a role revocation (e.g. owner who was demoted to master-only and
  // had previously chosen the admin surface). Without this, a
  // team-mode visit that wrote `max:unified_last_surface=admin` would
  // leak through to a later single-role session as an orphan key.
  //
  // Amended for the customer surface: `"customer"` is meaningful for
  // ANY multi-role caller, solo providers included — they reach it
  // through «Сменить режим» rather than through the auto-shown chooser.
  // Wiping it here would have bounced the owner straight back into the
  // admin surface on her next open, which is the exact bug this branch
  // is fixing. `"admin"` / `"master"` stay solo-dead-weight as before:
  // the solo surface never reads them.
  useEffect(() => {
    if (boot.status !== "ready" || !boot.me) return;
    const hasAdmin =
      boot.me.is_owner || boot.me.is_admin || boot.me.is_receptionist;
    const hasMaster = boot.me.is_master;
    const isSolo = boot.me.is_solo_provider === true;
    const multiRole = hasAdmin && hasMaster;
    const stored = readLastSurface();
    if (stored === null) return;
    const meaningful =
      multiRole && (stored === "customer" || !isSolo);
    if (!meaningful) clearLastSurface();
  }, [boot.status, boot.me]);

  // DRF-1434 — the boot answer is cached for the life of the webview,
  // which is right for a role that cannot change mid-session and wrong
  // for the one that can: accepting a master invitation flips
  // `is_master` server-side while this component still holds the old
  // `false`. Screens that cause such a change call `useReloadMe()` (see
  // state/boot.ts); everything else never touches it.
  const reloadMe = useCallback(() => {
    void loadMe();
  }, [loadMe]);

  const body = (() => {
    if (boot.status === "loading") return <SplashScreen />;
    if (boot.status === "no_role") {
      return <NoRoleScreen onRetry={() => void loadMe()} />;
    }

    if (boot.status === "ready" && boot.me) {
      return (
        <SurfaceModeContext.Provider value={surfaceMode}>
          <RoleSurface
            me={boot.me}
            surfacePref={surfacePref}
            chooserRequested={chooserRequested}
          />
        </SurfaceModeContext.Provider>
      );
    }

    // Network / 5xx — customer fallback with a retry banner.
    return <CustomerFallbackWithBanner onRetry={() => void loadMe()} />;
  })();

  return (
    <BootReloadContext.Provider value={reloadMe}>
      {body}
    </BootReloadContext.Provider>
  );
}

/**
 * The routing cascade for a booted user. Split out of `App` so the
 * surface-mode provider can wrap it once instead of at every early
 * return.
 */
function RoleSurface({
  me,
  surfacePref,
  chooserRequested,
}: {
  me: MeResponse;
  surfacePref: UnifiedSurface | null;
  chooserRequested: boolean;
}) {
  const hasAdmin = me.is_owner || me.is_admin || me.is_receptionist;
  const hasMaster = me.is_master;
  // Solo provider hint from W4 (`is_solo_provider(tenant)`) per Tau
  // §3.1 — true only when the tenant has exactly one distinct active
  // person. Missing field → false (graceful fallback for older
  // backends that haven't shipped #760 yet).
  const isSolo = me.is_solo_provider === true;
  // Routing cascade — Tau §5 + memory project_solo_provider_universal_ui.
  //
  //   1. Solo provider WITH master link → 8-tab unified solo surface.
  //      Skips the chooser entirely; Olga lands on /solo/my-day.
  //      Covers her full triple-role (owner+admin+master) — the solo
  //      surface reuses master-flavored screens (My day / Bookings /
  //      Schedule / AI all read /master/* endpoints), so a master
  //      link is required for the surface to be functional.
  //
  //      Round-1 amendment (adversarial Code Reviewer): edge case —
  //      a newly bootstrapped solo tenant where the owner created
  //      themselves but hasn't been added as a master yet (solo=true,
  //      admin=true, master=false). Mounting `UnifiedSoloSurface` for
  //      that user would paint a 8-tab nav whose master-screen tabs
  //      can't load any data. We narrow the guard to require
  //      `hasMaster` so that case falls through to the existing
  //      `hasAdmin → AdminRoutes` branch — they get the admin team
  //      screen and can add themselves as a master to bootstrap.
  //
  //   2. Team dual-role (NOT solo, has both admin AND master) →
  //      existing chooser surface from PR #753. Татьяна owner+master
  //      with junior masters under her keeps the «Салон»/«Мой
  //      профиль мастера» split.
  //   3-5. Single-role cascade unchanged.
  //
  // Amendment (surface switch): the four branches below are all
  // role-derived, which is why the customer surface used to be
  // unreachable for anyone holding a role — an owner who is also a
  // master matched branch 1 or 2 and never fell through to
  // `CustomerRoutes`. The two explicit-intent checks are therefore
  // placed ABOVE the whole cascade rather than inside any branch:
  //
  //   - an explicit choice must beat a role-derived default, and
  //   - putting them first leaves branches 1-5 byte-for-byte as they
  //     were, so the solo / dual-role / single-role behaviour of a
  //     user who never touched the chooser is unchanged.
  //
  // Both are gated on `multiRole`: a plain customer has nothing to
  // switch between, and a stale key from a revoked role must never
  // divert a single-role user (the cleanup effect above also wipes
  // it, but the gate makes that a safety net rather than a
  // load-bearing invariant).
  const multiRole = hasAdmin && hasMaster;
  if (multiRole && chooserRequested) {
    return <UnifiedLanding me={me} />;
  }
  if (multiRole && surfacePref === "customer") {
    return <CustomerRoutes />;
  }
  if (isSolo && hasMaster) {
    return <UnifiedSoloSurface me={me} />;
  }
  if (hasAdmin && hasMaster) {
    return <UnifiedAdminMasterRoutes me={me} />;
  }
  if (hasAdmin) return <AdminRoutes me={me} />;
  if (hasMaster) return <MasterRoutes />;
  return <CustomerRoutes />;
}
