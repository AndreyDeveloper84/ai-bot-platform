/** F-myvisits — list of customer's bookings.
 *
 * Spec §3.4 / customer-first-touch-and-mini-app-states §8 — filter
 * chips «Предстоящие» / «История» + empty / loading / error states.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ScreenLayout } from "../components/ScreenLayout";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import { fetchMyBookings, type BookingItem } from "../lib/api";
import { formatVisitFull } from "../lib/format";

type Mode = "upcoming" | "past";

type State =
  | { kind: "loading" }
  | { kind: "ok"; items: BookingItem[] }
  | { kind: "error"; err: unknown };

export function MyVisitsScreen() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("upcoming");
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(
    (m: Mode) => {
      setState({ kind: "loading" });
      let cancelled = false;
      fetchMyBookings({ past: m === "past" })
        .then(({ items }) => {
          if (!cancelled) setState({ kind: "ok", items });
        })
        .catch((err: unknown) => {
          if (!cancelled) setState({ kind: "error", err });
        });
      return () => {
        cancelled = true;
      };
    },
    [],
  );

  useEffect(() => load(mode), [mode, load]);

  return (
    <ScreenLayout title="Мои записи">
      {/* Filter chips per spec §3.4 list view */}
      <div className="chip-row" role="tablist" aria-label="Фильтр записей">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "upcoming"}
          className={`chip ${mode === "upcoming" ? "chip--active" : ""}`}
          onClick={() => setMode("upcoming")}
        >
          Предстоящие
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "past"}
          className={`chip ${mode === "past" ? "chip--active" : ""}`}
          onClick={() => setMode("past")}
        >
          История
        </button>
      </div>

      {state.kind === "loading" && (
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      )}

      {state.kind === "error" && <StateError err={state.err} onRetry={() => load(mode)} screenId="my-visits" />}

      {state.kind === "ok" && state.items.length === 0 && (
        // Empty-state copy per customer-first-touch-and-mini-app-states §7.3.
        <div className="callout">
          <p style={{ margin: 0 }}>У вас нет активных записей.</p>
          {mode === "upcoming" && (
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
        state.items.length > 0 &&
        state.items.map((b) => (
          <div key={b.id} className="visit-row">
            <button
              type="button"
              className="service-card"
              data-testid="my-visit-card"
              onClick={() => navigate(`/my-visits/${b.id}`)}
              style={{ textAlign: "left", width: "100%" }}
            >
              <div style={{ fontWeight: 600 }}>{formatVisitFull(b.visit_at)}</div>
              <div style={{ color: "var(--text-muted, #888)", marginTop: "var(--s-1)" }}>
                {b.service_name}
                {b.master_name ? ` · ${b.master_name}` : ""}
              </div>
              {b.status === "cancel_requested" && (
                <div
                  style={{
                    color: "var(--warning, #c70)",
                    marginTop: "var(--s-1)",
                    fontSize: "0.9em",
                  }}
                >
                  Отменяется…
                </div>
              )}
              {b.status === "reschedule_requested" && (
                <div
                  style={{
                    color: "var(--warning, #c70)",
                    marginTop: "var(--s-1)",
                    fontSize: "0.9em",
                  }}
                >
                  Переносится…
                </div>
              )}
              {(b.status === "cancelled" || b.status === "rescheduled") && (
                <div
                  style={{
                    color: "var(--text-muted, #888)",
                    marginTop: "var(--s-1)",
                    fontSize: "0.9em",
                  }}
                >
                  {b.status === "cancelled" ? "Отменена" : "Перенесена"}
                </div>
              )}
              {b.rating !== null && (
                <div
                  className="visit-rating"
                  aria-label={`Оценка ${b.rating} из 5`}
                  style={{ marginTop: "var(--s-1)" }}
                >
                  {"★".repeat(b.rating)}
                  {"☆".repeat(5 - b.rating)}
                </div>
              )}
            </button>
            {(b.reschedulable || b.cancellable || b.can_rate) && (
              <div className="visit-row__actions">
                {b.reschedulable && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/my-visits/${b.id}/reschedule`);
                    }}
                  >
                    Перенести
                  </button>
                )}
                {b.cancellable && (
                  <button
                    type="button"
                    className="btn-secondary btn-secondary--danger"
                    onClick={(e) => {
                      e.stopPropagation();
                      // Cancel lives on the detail screen — the undo-window
                      // countdown UI is heavier than a list-row can hold.
                      navigate(`/my-visits/${b.id}`);
                    }}
                  >
                    Отменить
                  </button>
                )}
                {b.can_rate && (
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/feedback/${b.id}`);
                    }}
                  >
                    Оценить визит
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
    </ScreenLayout>
  );
}
