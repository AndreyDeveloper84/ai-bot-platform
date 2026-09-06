/**
 * Admin bottom tab bar — mirrors MasterTabBar pattern for the
 * admin / owner Mini App surface.
 *
 * Tabs (per master-management-handoff §MM0 overview):
 *   [📅 День] [👥 Команда] [💈 Услуги] [💬 Чаты] [⚙ Настройки]
 *
 * Пять разделов — решение владельца 05.09.2026 (DRF-1522):
 * для владельца и администратора состав не меняется.
 *
 * Ресепшн видит три (DRF-1522). Панель больше не рисует один и тот же
 * набор всем: она принимает `me` и спрашивает `adminTabsFor`. Раньше
 * данных о человеке у неё не было вовсе, поэтому она показывала ресепшн
 * «Чаты» (бэкенд отвечает 403) и «Настройки» (заглушка). Проп
 * обязательный намеренно — так ни один экран не сможет молча смонтировать
 * панель «для всех»: без него не соберётся тип.
 *
 * Ширина колонок берётся из числа вкладок, а не из класса. Раньше
 * `.master-tabbar--admin` жёстко задавал пять колонок, и панель из трёх
 * вкладок сжалась бы в левые три пятых экрана.
 */

import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { hapticSelection } from "../lib/max-sdk";
import type { MeResponse } from "../lib/admin-api";
import { adminTabsFor, type AdminTabKey } from "../lib/admin-tabs";

interface TabSpec {
  key: AdminTabKey;
  label: string;
  to: string;
  icon: JSX.Element;
}

function IconDay() {
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
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4" />
      <path d="M8 2v4" />
      <path d="M3 10h18" />
    </svg>
  );
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

/** Все вкладки поверхности. Кто какие из них видит — решает `adminTabsFor`. */
const ALL_TABS: readonly TabSpec[] = [
    // «День» leads because it is what the front desk opens first every
    // morning — the roster is a setup screen, the day is the work.
    { key: "day", label: "День", to: "/admin/day", icon: <IconDay /> },
    { key: "team", label: "Команда", to: "/admin/team", icon: <IconTeam /> },
    {
      key: "services",
      label: "Услуги",
      to: "/admin/services",
      icon: <IconServices />,
    },
    {
      key: "chats",
      label: "Чаты",
      to: "/admin/internal-chat",
      icon: <IconChats />,
    },
    {
      key: "settings",
      label: "Настройки",
      to: "/admin/settings",
      icon: <IconSettings />,
    },
  ];

export function AdminTabBar({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const location = useLocation();

  const visible = adminTabsFor(me);
  const tabs = ALL_TABS.filter((tab) => visible.includes(tab.key));

  const handleTap = useCallback(
    (to: string) => {
      hapticSelection();
      navigate(to);
    },
    [navigate],
  );

  return (
    <nav
      className="master-tabbar master-tabbar--admin"
      style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }}
      aria-label="Основная навигация"
    >
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
