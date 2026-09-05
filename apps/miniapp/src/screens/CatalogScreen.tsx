/** F1 — Service catalog list. Spec §8 F1 state matrix. */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchServices, type Service } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { ServiceCard } from "../components/ServiceCard";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { backTo } from "../lib/screen-back";

/**
 * Возврат (DRF-1493): наверх — на первый экран клиента. Легаси-каталог
 * не вкладка и не корень: в него приходят с приветствия и из старых
 * flow-экранов, а по deep link из бота — только на новый
 * `/customer/catalog`.
 */
const BACK = backTo("/");

type State =
  | { kind: "loading" }
  | { kind: "ok"; services: Service[] }
  | { kind: "error"; err: unknown };

export function CatalogScreen() {
  const navigate = useNavigate();
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    fetchServices()
      .then(({ services }) => {
        if (!cancelled) setState({ kind: "ok", services });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(), [load]);

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={BACK} title="Услуги студии">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={BACK} title="Услуги студии">
        <StateError err={state.err} onRetry={load} screenId="catalog" />
      </ScreenLayout>
    );
  }

  if (state.services.length === 0) {
    return (
      <ScreenLayout back={BACK} title="Услуги студии">
        <div className="callout">
          <p style={{ margin: 0 }}>У студии пока нет услуг в каталоге. Спросите у студии напрямую.</p>
        </div>
      </ScreenLayout>
    );
  }

  return (
    <ScreenLayout back={BACK} title="Услуги студии">
      {state.services.map((s) => (
        <ServiceCard key={s.id} service={s} onSelect={() => navigate(`/catalog/${s.id}`)} />
      ))}
    </ScreenLayout>
  );
}
