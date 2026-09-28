/**
 * Карточка «Продолжить настройку» в «Моём дне» (DRF-1807, M15; макет §7:
 * «If user leaves onboarding, normal Master App may show a small setup card»).
 *
 * Читает ту же ручку readiness, что и экран 01, и рисует только пока
 * настройка не закрыта: перечисляет незакрытые пункты (как факт, не как
 * счётчик времени — ни процентов, ни «N из M») и ведёт на экран 01.
 * Ручка недоступна — карточки нет: чек-лист не важнее кабинета.
 *
 * §6-квартер, вопрос 1 (решение 28.09): когда настройка закрыта, вход на
 * отправку профиля живёт здесь. Раньше он был только на экране 01, а экран 01
 * при «готов» не открывается (корень уводит на «Мой день», эта карточка
 * пряталась) — кнопка, чьё условие включения прячет её носителя, недостижима.
 * Зовём отправлять только профиль в `draft` по ответу каталога: отправленный
 * (`pending`) или опубликованный (`active`) не зовём; статус не прочитан —
 * карточки нет (не знаем — не утверждаем). Слова — экрана отправки.
 */
import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import {
  actionableReadinessItems,
  getOnboardingReadiness,
  getPublicationStatus,
  type OnboardingReadiness,
} from "../lib/master-api";
import { PUBLICATION_COPY } from "../screens/MasterPublicationScreen";
import {
  canSubmitProfile,
  itemLabel,
  PUBLICATION_ROUTE,
  SETUP_ROUTE,
} from "../screens/MasterSetupLandingScreen";

export const SETUP_CARD_TITLE = "Продолжить настройку";
export const SETUP_CARD_CTA = "Открыть чек-лист";

export function SetupProgressCard() {
  const navigate = useNavigate();
  const location = useLocation();
  // DRF-2150 (инцидент 20.09): чек-лист и его пункты живут на соло-поверхности
  // (`/solo/setup`, deep_link'и readiness — /solo/*); у салонного мастера
  // (/master/*) этих маршрутов нет — «Открыть чек-лист» вёл в никуда. На
  // салонной поверхности карточка не рисуется: рабочие часы там — заявка
  // владельцу, а не пункт самонастройки.
  const onSoloSurface = location.pathname.startsWith("/solo/");
  const [readiness, setReadiness] = useState<OnboardingReadiness | null>(null);
  // Профиль ещё не отправлен (`draft` по ответу каталога) — только тогда зовём.
  const [draft, setDraft] = useState(false);

  useEffect(() => {
    if (!onSoloSurface) return;
    let cancelled = false;
    getOnboardingReadiness()
      .then(async (data) => {
        if (cancelled) return;
        setReadiness(data);
        if (!canSubmitProfile(data)) return;
        const publication = await getPublicationStatus();
        if (!cancelled) setDraft(publication.profile_status === "draft");
      })
      .catch(() => {
        /* без карточки — кабинет важнее */
      });
    return () => {
      cancelled = true;
    };
  }, [onSoloSurface]);

  if (!onSoloSurface || !readiness) return null;
  if (readiness.ready) {
    if (!canSubmitProfile(readiness) || !draft) return null;
    return (
      <section
        className="master-dashboard__section"
        aria-labelledby="submit-card-title"
      >
        <div className="setup-card" data-testid="submit-card">
          <h2 id="submit-card-title" className="setup-card__title">
            {PUBLICATION_COPY.readyTitle}
          </h2>
          {/* Вид — тот же, что у действия карточки «Продолжить настройку»:
              новая главная кнопка на «Моём дне» была бы решением о виде. */}
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate(PUBLICATION_ROUTE)}
          >
            {PUBLICATION_COPY.submit}
          </button>
        </div>
      </section>
    );
  }
  const open = actionableReadinessItems(readiness.items).filter(
    (item) => item.state !== "done",
  );
  if (open.length === 0) return null;

  return (
    <section
      className="master-dashboard__section"
      aria-labelledby="setup-card-title"
    >
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
        <button
          type="button"
          className="btn-secondary"
          onClick={() => navigate(SETUP_ROUTE)}
        >
          {SETUP_CARD_CTA}
        </button>
      </div>
    </section>
  );
}
