/**
 * Brand-level honest placeholder for stub surfaces gated off in prod
 * builds (pilot commit 4, orchestrator decision).
 *
 * Shown instead of a stub screen when `STUB_SURFACES_ENABLED` is false:
 * calm coming-soon copy that is explicitly honest about not showing
 * made-up data, a working CTA to the real profile (where the C5 152-ФЗ
 * export/delete actions live), and the standard bottom nav so the app
 * stays navigable. NEVER an empty screen, NEVER a redirect into
 * another stub surface.
 *
 * Surfaces:
 *   - "home"    — `/customer/main` (wellness dashboard, hidden until
 *                 S4/post-pilot; phase 3 target: home becomes «Мои
 *                 записи» on real data).
 *   - "catalog" — `/customer/catalog` (stub recommendations; real
 *                 customer endpoints land as phase 3 item 1).
 *
 * Voice: first-person Ayla, «ты», no exclamation marks, no selling
 * tone — same rules as `ComingSoonCard` / profile copy.
 */

import { useNavigate } from "react-router-dom";

import { ComingSoonCard } from "../components/ComingSoonCard";
import { useScreenBack } from "../hooks/useScreenBack";
import { screenRoot } from "../lib/screen-back";

type Surface = "home" | "catalog";

const COPY: Record<Surface, { title: string; primary: string; secondary: string }> = {
  home: {
    title: "Главная",
    primary:
      "Скоро здесь будет твой день: ближайшие записи, забота и рекомендации Ayla — всё в одном месте.",
    secondary:
      "Раздел в работе. Я не показываю выдуманных данных, чтобы не путать тебя.",
  },
  catalog: {
    title: "Услуги",
    primary: "Скоро здесь будет каталог: услуги и мастера рядом с тобой.",
    secondary:
      "Раздел в работе. Я не показываю выдуманных витрин — только настоящие данные.",
  },
};

interface Props {
  surface: Surface;
}

export function PilotComingSoonScreen({ surface }: Props) {
  const navigate = useNavigate();

  // Вид экрана (DRF-1493): корень. Это то, что видно в prod-сборке на
  // месте закрытой поверхности, и у него та же нижняя навигация из
  // пяти вкладок — выход отсюда через неё, а не «назад». Объявление
  // стоит здесь, а не только у вызывающего экрана: в prod до тела
  // `CustomerWellnessDashboardScreen` дело не доходит вовсе.
  useScreenBack(
    screenRoot(
      "Честная заглушка закрытой вкладки: своя нижняя навигация, " +
        "родителя нет.",
    ),
  );

  const copy = COPY[surface];
  const activeTab = surface === "home" ? "Главная" : "Услуги";
  const tabs: Array<{ label: string; icon: string; path: string }> = [
    { label: "Главная", icon: "🏠", path: "/customer/main" },
    { label: "День", icon: "☀", path: "/customer/wellness" },
    { label: "Записи", icon: "📅", path: "/customer/records" },
    { label: "Услуги", icon: "💅", path: "/customer/catalog" },
    { label: "Я", icon: "👤", path: "/customer/profile" },
  ];

  return (
    <div className="profile-screen">
      <header className="records-screen__header">
        <h1 className="records-screen__title">{copy.title}</h1>
      </header>

      <main className="profile-screen__main">
        <section className="profile-section">
          <ComingSoonCard primary={copy.primary} secondary={copy.secondary} />
          <div
            className="profile-section__cta-row"
            style={{ marginTop: "var(--s-3)" }}
          >
            <button
              type="button"
              className="btn-primary"
              onClick={() => navigate("/customer/profile")}
            >
              Открыть профиль
            </button>
          </div>
        </section>
      </main>

      {/* Bottom nav — mirrors the other tab screens; the active tab is
          the gated surface itself. */}
      <nav className="wellness-dash__nav" aria-label="Основная навигация">
        {tabs.map((tab) => {
          const active = tab.label === activeTab;
          return (
            <button
              key={tab.label}
              type="button"
              className={`wellness-dash__nav-tab${
                active ? " wellness-dash__nav-tab--active" : ""
              }`}
              aria-label={tab.label}
              aria-current={active ? "page" : undefined}
              onClick={() => {
                if (!active) navigate(tab.path);
              }}
            >
              <span className="wellness-dash__nav-icon" aria-hidden="true">
                {tab.icon}
              </span>
              <span className="wellness-dash__nav-label">{tab.label}</span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}
