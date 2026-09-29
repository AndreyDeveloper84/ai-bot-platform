/**
 * Нижняя навигация пилотной салонной админки — три вкладки (DRF-1235).
 *
 *     [🗓 Сегодня]   [📅 Расписание]   [✦ Ayla]
 *
 * Отдельный компонент, а не режим `AdminTabBar`. Мостовая панель уже
 * обслуживает два набора разделов и заперта тестом
 * (`App.receptionSurface.test.tsx` пинует «День · Команда · Услуги ·
 * Чаты · Настройки» у владельца и «День · Команда» у ресепшн —
 * DRF-1552); подмешать в неё третий набор значило бы либо сломать этот
 * тест, либо спрятать пилот за флагом внутри чужой панели. Пилот и мост
 * живут параллельно, и панели у них тоже две.
 *
 * # Что здесь НЕ рисуется
 *
 * Иконки взяты с макета (DRF-1236): календарь с точкой у «Сегодня»,
 * календарь у «Расписания», звёздочка у «Ayla». Значков-счётчиков нет —
 * `master-tabbar__badge` и `__dot` существуют, но число «требует
 * внимания» на пилотной поверхности ещё никто не считает, и рисовать
 * пустой кружок значило бы обещать несуществующее.
 *
 * # Число колонок
 *
 * Колонки ставит сам компонент из длины списка вкладок, как это уже
 * делает `AdminTabBar`. Правило `.salon-pilot-tabbar` в стилях —
 * запасной вариант на случай, если inline-стиль не применился; оно же
 * то, что читает `tools/lint/miniapp_style_contract.py`, сверяя число
 * колонок с числом вкладок.
 */

import { useCallback } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { hapticSelection } from "../lib/max-sdk";
import type { SalonPilotTabKey } from "../lib/salon-pilot";

/** Календарь с отмеченным днём — «Сегодня» на макете. */
function IconToday() {
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
      <path d="M12 15h.01" />
    </svg>
  );
}

/** Пустой календарь — «Расписание». */
function IconSchedule() {
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

/** Звёздочки ассистента — «Ayla». */
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

interface TabSpec {
  key: SalonPilotTabKey;
  label: string;
  to: string;
  icon: JSX.Element;
}

/**
 * Вкладки панели.
 *
 * Адреса выписаны строками, а не собраны из `SALON_PILOT_PATHS`
 * циклом, — и это не небрежность. `tools/lint/miniapp_style_contract.py`
 * считает вкладки, читая файл ТЕКСТОМ — по литералам поля адреса:
 * вычисленный список
 * ему не виден, и панель, собранная из `map`, прошла бы проверку числа
 * колонок вхолостую. Расхождение с маршрутами ловит
 * `SalonPilotTabBar.test.tsx`, который сверяет этот список с
 * `SALON_PILOT_PATHS` поимённо.
 */
export const SALON_PILOT_TAB_SPECS: readonly TabSpec[] = [
  { key: "today", label: "Сегодня", to: "/admin/today", icon: <IconToday /> },
  {
    key: "schedule",
    label: "Расписание",
    to: "/admin/schedule",
    icon: <IconSchedule />,
  },
  { key: "ayla", label: "Ayla", to: "/admin/ayla", icon: <IconAyla /> },
];

const TABS = SALON_PILOT_TAB_SPECS;

export function SalonPilotTabBar() {
  const navigate = useNavigate();
  const location = useLocation();

  const handleTap = useCallback(
    (to: string) => {
      hapticSelection();
      navigate(to);
    },
    [navigate],
  );

  return (
    <nav
      className="master-tabbar salon-pilot-tabbar"
      style={{ gridTemplateColumns: `repeat(${TABS.length}, 1fr)` }}
      aria-label="Основная навигация"
    >
      {TABS.map((tab) => {
        // Точное совпадение, а не префикс: вложенные потоки пилота
        // (создание записи, детали записи) — отдельные task flows, и
        // DRF-1235 прямо запрещает держать под ними нижнюю панель.
        // Префиксное сравнение подсветило бы вкладку на экране, где
        // панели вообще не должно быть.
        const isActive = location.pathname === tab.to;
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
