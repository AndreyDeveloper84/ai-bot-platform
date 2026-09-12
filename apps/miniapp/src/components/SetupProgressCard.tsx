/**
 * Карточка «Продолжить настройку» в «Моём дне» (DRF-1807, M15; макет §7:
 * «If user leaves onboarding, normal Master App may show a small setup card»).
 *
 * Читает ту же ручку readiness, что и экран 01, и рисует только пока
 * настройка не закрыта: перечисляет незакрытые пункты (как факт, не как
 * счётчик времени — ни процентов, ни «N из M») и ведёт на экран 01.
 * Ручка недоступна — карточки нет: чек-лист не важнее кабинета.
 */
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import {
  drawnReadinessItems,
  getOnboardingReadiness,
  type OnboardingReadiness,
} from "../lib/master-api";
import { itemLabel, SETUP_ROUTE } from "../screens/MasterSetupLandingScreen";

export const SETUP_CARD_TITLE = "Продолжить настройку";
export const SETUP_CARD_CTA = "Открыть чек-лист";

export function SetupProgressCard() {
  const navigate = useNavigate();
  const [readiness, setReadiness] = useState<OnboardingReadiness | null>(null);

  useEffect(() => {
    let cancelled = false;
    getOnboardingReadiness()
      .then((data) => {
        if (!cancelled) setReadiness(data);
      })
      .catch(() => {
        /* без карточки — кабинет важнее */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!readiness || readiness.ready) return null;
  const open = drawnReadinessItems(readiness.items).filter((item) => item.state !== "done");
  if (open.length === 0) return null;

  return (
    <section className="master-dashboard__section" aria-labelledby="setup-card-title">
      <div className="setup-card" data-testid="setup-card">
        <h2 id="setup-card-title" className="setup-card__title">
          {SETUP_CARD_TITLE}
        </h2>
        <ul className="setup-card__list">
          {open.map((item) => (
            <li key={item.key} className="setup-card__item">
              {itemLabel(item)}
            </li>
          ))}
        </ul>
        <button type="button" className="btn-secondary" onClick={() => navigate(SETUP_ROUTE)}>
          {SETUP_CARD_CTA}
        </button>
      </div>
    </section>
  );
}
