/**
 * Admin bottom tab bar — mirrors MasterTabBar pattern for the
 * admin / owner Mini App surface.
 *
 * Tabs (per master-management-handoff §MM0 overview):
 *   [👥 Команда] [💈 Услуги] [💬 Чаты] [⚙ Настройки]
 *
 * Only «Команда» is live in this PR — the other three render
 * «Скоро» placeholder screens. Same SPA + role-routing pattern
 * means MasterTabBar / AdminTabBar live side-by-side and each
 * screen mounts the one matching the caller's role.
 */

import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { hapticSelection } from "../lib/max-sdk";

type TabKey = "team" | "services" | "chats" | "settings";

interface TabSpec {
  key: TabKey;
  label: string;
  to: string;
  icon: JSX.Element;
}

function IconTeam() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M17 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
    </svg>
  );
}

function IconServices() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" />
      <path d="M3.27 6.96 12 12.01l8.73-5.05" />
      <path d="M12 22.08V12" />
    </svg>
  );
}

function IconChats() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 11.5a8.4 8.4 0 0 1-1 4 8.5 8.5 0 0 1-7.6 4.6 8.4 8.4 0 0 1-4-1L3 21l1.9-5.4a8.5 8.5 0 1 1 16.1-4.1Z" />
    </svg>
  );
}

function IconSettings() {
  return (
    <svg
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h0a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5h0a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8v0a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </svg>
  );
}

export function AdminTabBar() {
  const navigate = useNavigate();
  const location = useLocation();

  const tabs: TabSpec[] = [
    { key: "team", label: "Команда", to: "/admin/team", icon: <IconTeam /> },
    {
      key: "services",
      label: "Услуги",
      to: "/admin/services",
      icon: <IconServices />,
    },
    { key: "chats", label: "Чаты", to: "/admin/chats", icon: <IconChats /> },
    {
      key: "settings",
      label: "Настройки",
      to: "/admin/settings",
      icon: <IconSettings />,
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
            <span className="master-tabbar__icon">{tab.icon}</span>
            <span className="master-tabbar__label">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
