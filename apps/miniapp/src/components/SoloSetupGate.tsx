/**
 * Вход в соло-поверхность по готовности (DRF-1807, M15).
 *
 * Макет §7: после регистрации в MAX мастер попадает на экран 01 «всё
 * готово» с чек-листом. Готовность — проекция сервера (M2), поэтому
 * корневой маршрут спрашивает её и ведёт: не готово → экран 01, готово →
 * «Мой день». Сеть упала — «Мой день»: кабинет важнее чек-листа, и карточка
 * «Продолжить настройку» там всё равно есть.
 *
 * Ничего не хранится: «Продолжить позже» — просто уход с экрана 01, и
 * следующий вход снова спросит сервер.
 */
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";

import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { getOnboardingReadiness } from "../lib/master-api";
import { HOME_ROUTE, SETUP_ROUTE } from "../screens/MasterSetupLandingScreen";

export function SoloSetupGate() {
  const [target, setTarget] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getOnboardingReadiness()
      .then((readiness) => {
        if (!cancelled) setTarget(readiness.ready ? HOME_ROUTE : SETUP_ROUTE);
      })
      .catch(() => {
        if (!cancelled) setTarget(HOME_ROUTE);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (target === null) {
    return (
      <main className="screen">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </main>
    );
  }
  return <Navigate to={target} replace />;
}
