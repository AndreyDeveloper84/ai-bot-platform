/** F2 — Masters filtered by selected service. */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchMasters, type Master } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { MasterCard } from "../components/MasterCard";
import { DelayedSkeleton, MasterCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useHaptics } from "../hooks/useHaptics";
import { setMaster, useBookingDraft } from "../state/booking";
import { backTo } from "../lib/screen-back";

/** Возврат (DRF-1493): в каталог — шаг назад легаси-сценария записи. */
const BACK = backTo("/catalog");

type State =
  | { kind: "loading" }
  | { kind: "ok"; masters: Master[] }
  | { kind: "error"; err: unknown };

export function MasterPickerScreen() {
  const navigate = useNavigate();
  const draft = useBookingDraft();
  const haptics = useHaptics();
  const [state, setState] = useState<State>({ kind: "loading" });


  const load = useCallback(() => {
    if (!draft.serviceId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    fetchMasters({ serviceId: draft.serviceId })
      .then(({ masters }) => {
        if (!cancelled) setState({ kind: "ok", masters });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [draft.serviceId]);

  useEffect(() => {
    if (!draft.serviceId) {
      navigate("/catalog", { replace: true });
      return;
    }
    return load();
  }, [draft.serviceId, navigate, load]);

  function onPick(m: Master) {
    haptics.selection();
    setMaster(m.id, m.name);
    navigate("/book/when");
  }

  if (state.kind === "loading") {
    return (
      <ScreenLayout back={BACK} title="Кто сделает">
        <DelayedSkeleton loading>
          <MasterCardSkeleton />
          <MasterCardSkeleton />
          <MasterCardSkeleton />
        </DelayedSkeleton>
      </ScreenLayout>
    );
  }

  if (state.kind === "error") {
    return (
      <ScreenLayout back={BACK} title="Кто сделает">
        <StateError err={state.err} onRetry={load} screenId="masters" />
      </ScreenLayout>
    );
  }

  if (state.masters.length === 0) {
    return (
      <ScreenLayout back={BACK} title="Кто сделает">
        <div className="callout">
          <p style={{ margin: 0 }}>У этой услуги пока нет доступных мастеров.</p>
          <button
            type="button"
            className="btn-secondary"
            style={{ marginTop: "var(--s-3)" }}
            onClick={() => navigate("/catalog", { replace: true })}
          >
            Другие услуги
          </button>
        </div>
      </ScreenLayout>
    );
  }

  return (
    <ScreenLayout back={BACK} title="Кто сделает">
      {state.masters.map((m) => (
        <MasterCard
          key={m.id}
          master={m}
          selected={draft.masterId === m.id}
          onSelect={() => onPick(m)}
        />
      ))}
    </ScreenLayout>
  );
}
