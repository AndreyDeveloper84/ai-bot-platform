/**
 * Нижняя панель клиентского Mini App — одна на всех экранах (DRF-2191, H01-b).
 *
 * Макет DRF-1321 v1.2, решение владельца §55 б (20.09): «Главная · План ·
 * Дневник · Записи · Профиль». DRF-2144 переименовал панель на Главной;
 * «Мои записи», «Профиль» и заглушка пилота рисовали прежнюю «Главная ·
 * Записи · Услуги · Я» каждая своей разметкой — четыре набора вкладок в
 * коде и три из них устаревшие. Теперь набор один и живёт здесь;
 * переписьный сторож (`CustomerTabBar.test.tsx`) не даёт экрану нарисовать
 * «Основная навигация» самому.
 *
 * Правило класса (инцидент 20.09, #1918): состояние ошибки не убирает
 * навигацию — экран ставит панель ВНЕ веток loading/error/ready, и с любого
 * состояния есть выход («Профиль» → «Сменить режим» для многоролевого).
 *
 * «Услуги» из панели ушли — вход в каталог с Главной («Записаться», «Новая
 * запись») и из чата (§60); «Я» стало «Профиль». Активная вкладка —
 * `aria-current="page"` и не кликается: тап по текущей вкладке ничего не
 * обещает. Классы `wellness-dash__nav*` — прежние (globals.css), сетка
 * подстраивается под число вкладок.
 *
 * Сегодня «План» и «Дневник» — листовые экраны со стрелкой «назад»
 * (DRF-1493); по макету вкладки панели — корни. Смена их контракта «назад»
 * — отдельный лист (главное окно заведёт), здесь не трогается.
 */
import { useNavigate } from "react-router-dom";

export type CustomerTabKey = "home" | "plan" | "diary" | "records" | "profile";

export interface CustomerTab {
  key: CustomerTabKey;
  label: string;
  icon: string;
  route: string;
}

/** Ровно пять, в порядке макета. Менять состав/порядок — новое решение владельца. */
export const CUSTOMER_TABS: ReadonlyArray<CustomerTab> = [
  { key: "home", label: "Главная", icon: "🏠", route: "/customer/main" },
  { key: "plan", label: "План", icon: "📋", route: "/customer/plan" },
  { key: "diary", label: "Дневник", icon: "📔", route: "/customer/food-scanner/diary" },
  { key: "records", label: "Записи", icon: "📅", route: "/customer/records" },
  { key: "profile", label: "Профиль", icon: "👤", route: "/customer/profile" },
];

/**
 * ``active`` не обязателен: экран, которого в панели нет (каталог — вход в
 * него с Главной и из чата), панель рисует без подсвеченной вкладки. Врать
 * «ты в Записях», стоя в каталоге, хуже, чем не подсвечивать ничего.
 */
export function CustomerTabBar({ active }: { active?: CustomerTabKey }) {
  const navigate = useNavigate();
  return (
    <nav className="wellness-dash__nav" aria-label="Основная навигация">
      {CUSTOMER_TABS.map((tab) => {
        const isActive = tab.key === active;
        return (
          <button
            key={tab.key}
            type="button"
            className={
              isActive
                ? "wellness-dash__nav-tab wellness-dash__nav-tab--active"
                : "wellness-dash__nav-tab"
            }
            aria-current={isActive ? "page" : undefined}
            aria-label={tab.label}
            onClick={isActive ? undefined : () => navigate(tab.route)}
          >
            <span className="wellness-dash__nav-icon" aria-hidden="true">
              {tab.icon}
            </span>
            <span className="wellness-dash__nav-label">{tab.label}</span>
          </button>
        );
      })}
    </nav>
  );
}
