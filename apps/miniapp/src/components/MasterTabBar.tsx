/**
 * Master bottom tab bar — ровно три (DRF-2121; §28 п.2, §50).
 *
 * «Сегодня | Расписание | Ayla». Решение владельца 05.09 (§28): «Профиль»
 * и «Ещё» в панель не добавляются, вход в Профиль — аватар справа вверху
 * на всех трёх разделах (`AvatarSheet`, DRF-2115). «Диалоги» (переписка
 * мастер↔клиент, DRF-1039) из панели сняты — маршрут живёт по прямой
 * ссылке до DRF-1255; счётчик непрочитанных — на кнопке в шапке «Сегодня».
 *
 * Icons are hand-rolled SVG ~matching the Lucide outline set, kept inline
 * to avoid a runtime icon dep.
 *
 * Layout:
 *   ┌──────────────────────────────────────┐
 *   │   [📅 Сегодня]  [📅● Расписание]  [✦ Ayla]   │
 *   └──────────────────────────────────────┘
 */

import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { hapticSelection } from "../lib/max-sdk";

interface TabBarProps {
  scheduleHasPendingChange: boolean;
}

type TabKey = "today" | "schedule" | "ayla";

/** Подписи в порядке панели — сторож «ровно три» сверяет с ними. */
export const MASTER_TAB_LABELS = ["Сегодня", "Расписание", "Ayla"] as const;

interface TabSpec {
  key: TabKey;
  label: string;
  to: string;
  /** Дополнительные префиксы адресов, при которых вкладка активна. */
  also?: readonly string[];
  icon: JSX.Element;
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


function IconAyla() {
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
      <path d="M12 3.5 13.3 8l4.5 1.3-4.5 1.3L12 15l-1.3-4.4L6.2 9.3 10.7 8 12 3.5Z" />
      <path d="M18.5 15.5l.6 2 2 .6-2 .6-.6 2-.6-2-2-.6 2-.6.6-2Z" />
    </svg>
  );
}

// --- Component ------------------------------------------------------------

export function MasterTabBar({ scheduleHasPendingChange }: TabBarProps) {
  const navigate = useNavigate();
  const location = useLocation();

  // Хук стоит ВЫШЕ раннего возврата ниже: порядок хуков не должен зависеть от
  // ветки (DRF-2388, `react-hooks/rules-of-hooks`). Перенос ничего не меняет —
  // `handleTap` нужен только в разметке, до которой ранний возврат не доходит.
  const handleTap = useCallback(
    (to: string) => {
      hapticSelection();
      navigate(to);
    },
    [navigate],
  );

  // Solo unified surface (Tau §5) owns its own bottom nav (`SoloBottomNav`
  // in App.tsx). The solo surface deliberately reuses several master
  // screens — MasterDashboardScreen, MasterScheduleScreen — which
  // render this MasterTabBar. Without an early-return here, those reused screens
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
      key: "today",
      label: MASTER_TAB_LABELS[0],
      to: "/master/dashboard",
      icon: <IconHome />,
    },
    {
      key: "schedule",
      label: MASTER_TAB_LABELS[1],
      to: "/master/schedule",
      // «Детали записи» (DRF-2156) — часть раздела «Расписание», как в макете DRF-1185.
      also: ["/master/bookings/"],
      icon: <IconCalendar />,
      badgeDot: scheduleHasPendingChange,
    },
    {
      key: "ayla",
      label: MASTER_TAB_LABELS[2],
      to: "/master/ayla",
      icon: <IconAyla />,
    },
  ];

  return (
    <nav className="master-tabbar" aria-label="Основная навигация">
      {tabs.map((tab) => {
        const isActive =
          location.pathname.startsWith(tab.to) ||
          (tab.also ?? []).some((prefix) => location.pathname.startsWith(prefix));
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
              {tab.badgeDot ? (
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
