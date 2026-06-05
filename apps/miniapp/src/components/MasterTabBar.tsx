/**
 * Master bottom tab bar (master-mobile §M1).
 *
 * Sticky bottom row of 4 tabs with badge support. Icons are hand-rolled
 * SVG ~matching the Lucide outline set referenced by the spec, kept
 * inline to avoid a runtime icon dep (skill ref: «Don't add CSS-in-JS
 * frameworks» and analogous bundle discipline).
 *
 * Layout:
 *   ┌──────────────────────────────────────┐
 *   │  [🏠]    [📅]    [💬 2]    [👤●]    │
 *   └──────────────────────────────────────┘
 */

import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { hapticSelection } from "../lib/max-sdk";

interface TabBarProps {
  unreadCount: number;
  scheduleHasPendingChange: boolean;
  profileHasOwnerPendingChange: boolean;
}

type TabKey = "home" | "schedule" | "conversations" | "profile";

interface TabSpec {
  key: TabKey;
  label: string;
  to: string;
  icon: JSX.Element;
  badgeCount?: number;
  badgeDot?: boolean;
}

// --- Icons (24px outline, currentColor) -----------------------------------

function IconHome() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 9.5 12 3l9 6.5V21a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1V9.5Z" />
    </svg>
  );
}

function IconCalendar() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M16 3v4M8 3v4M3 10h18" />
    </svg>
  );
}

function IconMessage() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 11.5a8.4 8.4 0 0 1-1 4 8.5 8.5 0 0 1-7.6 4.6 8.4 8.4 0 0 1-4-1L3 21l1.9-5.4a8.5 8.5 0 1 1 16.1-4.1Z" />
    </svg>
  );
}

function IconUser() {
  return (
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

// --- Component ------------------------------------------------------------

export function MasterTabBar({
  unreadCount,
  scheduleHasPendingChange,
  profileHasOwnerPendingChange,
}: TabBarProps) {
  const navigate = useNavigate();
  const location = useLocation();

  // Solo unified surface (Tau §5) owns its own bottom nav (`SoloBottomNav`
  // in App.tsx). The solo surface deliberately reuses several master
  // screens — MasterDashboardScreen, MasterScheduleScreen,
  // MasterConversationsScreen — which historically rendered this
  // MasterTabBar. Without an early-return here, those reused screens
  // would paint a second fixed-position tab bar on top of the solo bar,
  // both at z-index 100, fighting for the same pixels.
  //
  // Gate on the URL prefix rather than threading a prop through every
  // master-screen consumer — single-file change with the same effect.
  // `/master/*` paths still render the master tab bar unchanged (the
  // prefix check is exclusive — `/master/dashboard` does NOT start with
  // `/solo/`). Adversarial Code Reviewer round-1 catch.
  if (location.pathname.startsWith("/solo/")) {
    return null;
  }

  const tabs: TabSpec[] = [
    {
      key: "home",
      label: "Дом",
      to: "/master/dashboard",
      icon: <IconHome />,
    },
    {
      key: "schedule",
      label: "Расписание",
      to: "/master/schedule",
      icon: <IconCalendar />,
      badgeDot: scheduleHasPendingChange,
    },
    {
      key: "conversations",
      label: "Диалоги",
      to: "/master/conversations",
      icon: <IconMessage />,
      badgeCount: unreadCount > 0 ? unreadCount : undefined,
    },
    {
      key: "profile",
      label: "Профиль",
      to: "/master/profile",
      icon: <IconUser />,
      badgeDot: profileHasOwnerPendingChange,
    },
  ];

  const handleTap = useCallback(
    (to: string) => {
      hapticSelection();
      navigate(to);
    },
    [navigate],
  );

  return (
    <nav className="master-tabbar" aria-label="Основная навигация">
      {tabs.map((tab) => {
        const isActive = location.pathname.startsWith(tab.to);
        return (
          <button
            type="button"
            key={tab.key}
            className={`master-tabbar__tab${isActive ? " master-tabbar__tab--active" : ""}`}
            onClick={() => handleTap(tab.to)}
            aria-current={isActive ? "page" : undefined}
            aria-label={tab.label}
          >
            <span className="master-tabbar__icon">
              {tab.icon}
              {tab.badgeCount ? (
                <span className="master-tabbar__badge" aria-label={`непрочитанных: ${tab.badgeCount}`}>
                  {tab.badgeCount > 99 ? "99+" : tab.badgeCount}
                </span>
              ) : tab.badgeDot ? (
                <span className="master-tabbar__dot" aria-label="есть изменения" />
              ) : null}
            </span>
            <span className="master-tabbar__label">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
