/** F3 — My visits. Three tabs (Предстоящие / Прошедшие / Все). */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchVisits, type Visit } from "../lib/api";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { useBackButton } from "../hooks/useBackButton";
import { useHaptics } from "../hooks/useHaptics";
import { formatVisitFull } from "../lib/format";
import { setRescheduleContext, setService } from "../state/booking";

type Tab = "upcoming" | "past" | "all";

type State =
  | { kind: "loading" }
  | { kind: "ok"; visits: Visit[] }
  | { kind: "error"; err: unknown };

export function MyVisitsScreen() {
  const navigate = useNavigate();
  const haptics = useHaptics();
  const [tab, setTab] = useState<Tab>("upcoming");
  const [state, setState] = useState<State>({ kind: "loading" });

  useBackButton({ onBack: () => navigate(-1) });

  const load = useCallback(() => {
    setState({ kind: "loading" });
    let cancelled = false;
    fetchVisits(tab)
      .then(({ visits }) => {
        if (!cancelled) setState({ kind: "ok", visits });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [tab]);

  useEffect(() => load(), [load]);

  function onReschedule(v: Visit) {
    if (!v.service_id || !v.master_id) return;
    haptics.selection();
    setRescheduleContext(v.id, v.service_id, v.service_name, v.master_id, v.master_name);
    navigate("/book/when");
  }

  function onRebook(v: Visit) {
    if (!v.service_id) return;
    haptics.selection();
    setService(v.service_id, v.service_name);
    navigate(`/catalog/${v.service_id}`);
  }

  return (
    <ScreenLayout title="Мои визиты">
      <div role="tablist" className="tabs">
        {(["upcoming", "past", "all"] as Tab[]).map((t) => (
          <button
            type="button"
            key={t}
            role="tab"
            aria-selected={tab === t}
            className={`tab ${tab === t ? "tab--active" : ""}`}
            onClick={() => {
              haptics.selection();
              setTab(t);
            }}
          >
            {t === "upcoming" ? "Предстоящие" : t === "past" ? "Прошедшие" : "Все"}
          </button>
        ))}
      </div>

      {state.kind === "loading" && (
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      )}

      {state.kind === "error" && (
        <StateError err={state.err} onRetry={load} screenId="visits" />
      )}

      {state.kind === "ok" && state.visits.length === 0 && (
        <div className="callout">
          <p style={{ margin: 0 }}>
            {tab === "upcoming"
              ? "У вас нет активных записей."
              : "Записей пока нет."}
          </p>
          {tab === "upcoming" && (
            <button
              type="button"
              className="btn-secondary"
              style={{ marginTop: "var(--s-3)" }}
              onClick={() => navigate("/catalog")}
            >
              Записаться
            </button>
          )}
        </div>
      )}

      {state.kind === "ok" &&
        state.visits.map((v) => (
          <div className="confirm-card" key={v.id}>
            <dl>
              <dt>Услуга</dt>
              <dd>{v.service_name}</dd>
              <dt>Мастер</dt>
              <dd>{v.master_name}</dd>
              {v.visit_at && (
                <>
                  <dt>Время</dt>
                  <dd>{formatVisitFull(v.visit_at)}</dd>
                </>
              )}
              {v.rating !== null && (
                <>
                  <dt>Оценка</dt>
                  <dd>
                    <span className="visit-rating" aria-label={`${v.rating} из 5`}>
                      {"★".repeat(v.rating)}
                      {"☆".repeat(5 - v.rating)}
                    </span>
                  </dd>
                </>
              )}
            </dl>
            {v.status === "confirmed" && tab !== "past" && (
              <button
                type="button"
                className="btn-secondary"
                style={{ marginTop: "var(--s-3)" }}
                onClick={() => onReschedule(v)}
              >
                Перенести
              </button>
            )}
            {v.can_rate && (
              <button
                type="button"
                className="btn-secondary"
                style={{ marginTop: "var(--s-3)" }}
                onClick={() => {
                  haptics.selection();
                  navigate(`/feedback/${v.id}`);
                }}
              >
                Оценить визит
              </button>
            )}
            {(tab === "past" || v.status === "cancelled" || v.status === "rescheduled") &&
              v.service_id && (
                <button
                  type="button"
                  className="btn-secondary"
                  style={{ marginTop: "var(--s-3)" }}
                  onClick={() => onRebook(v)}
                >
                  Повторить запись
                </button>
              )}
          </div>
        ))}
    </ScreenLayout>
  );
}
