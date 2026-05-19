/** F2 — Masters filtered by selected service. */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchMasters, type Master } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { MasterCard } from "../components/MasterCard";
import { DelayedSkeleton, MasterCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useBackButton } from "../hooks/useBackButton";
import { useHaptics } from "../hooks/useHaptics";
import { setMaster, useBookingDraft } from "../state/booking";

type State =
  | { kind: "loading" }
  | { kind: "ok"; masters: Master[] }
  | { kind: "error"; err: unknown };

export function MasterPickerScreen() {
  const navigate = useNavigate();
  const draft = useBookingDraft();
  const haptics = useHaptics();
  const [state, setState] = useState<State>({ kind: "loading" });

  useBackButton({ onBack: () => navigate(-1) });

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
      <ScreenLayout title="Кто сделает">
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
      <ScreenLayout title="Кто сделает">
        <StateError err={state.err} onRetry={load} screenId="masters" />
      </ScreenLayout>
    );
  }

  if (state.masters.length === 0) {
    return (
      <ScreenLayout title="Кто сделает">
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
    <ScreenLayout title="Кто сделает">
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
