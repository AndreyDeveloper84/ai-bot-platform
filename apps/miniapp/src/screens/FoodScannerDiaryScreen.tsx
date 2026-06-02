/**
 * /дневник surface — Customer Food Scanner daily log view.
 *
 * Route: `/customer/food-scanner/diary`
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` (R5 bonus
 * surface) + memory `project_variant_b_wellness_mvp` (food scanner
 * P0 for pilot 2026-07-15).
 *
 * Hides numeric values when `health_flags.eating_disorder === true`
 * (per spec §10 Appendix ED Mode).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Skeleton } from "../components/Skeleton";
import { StateError } from "../components/StateError";
import {
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  entriesLabel,
  fetchDailySummary,
  fetchHealthFlags,
  formatTimeShort,
  type DailySummaryResponse,
  type MealType,
} from "../lib/food-scanner";

type Status =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "ready"; summary: DailySummaryResponse; edMode: boolean };

const MEAL_ORDER: ReadonlyArray<MealType> = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
];

export function FoodScannerDiaryScreen() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const load = useCallback(async () => {
    setStatus({ kind: "loading" });
    try {
      const [summary, flags] = await Promise.all([
        fetchDailySummary(),
        fetchHealthFlags(),
      ]);
      setStatus({
        kind: "ready",
        summary,
        edMode: Boolean(flags.health_flags.eating_disorder),
      });
    } catch (err) {
      setStatus({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate("/customer/main")}
        >
          <svg width="20" height="20" viewBox="0 0 20 20" fill="none">
            <path
              d="M12 4l-6 6 6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
        <h1 className="records-screen__title">Питание</h1>
      </header>

      <main className="food-scanner-screen__main">
        {status.kind === "loading" && (
          <div className="food-scanner-diary__skeleton" aria-hidden="true">
            <Skeleton width="40%" height="1.1em" />
            <div style={{ marginTop: "var(--s-3)" }}>
              <Skeleton width="90%" height="0.95em" />
            </div>
            <div style={{ marginTop: "var(--s-2)" }}>
              <Skeleton width="70%" height="0.95em" />
            </div>
          </div>
        )}

        {status.kind === "error" && (
          <StateError err={status.err} onRetry={load} />
        )}

        {status.kind === "ready" && (
          <DiaryReady
            summary={status.summary}
            edMode={status.edMode}
            onAddTap={() => navigate("/customer/food-scanner/capture")}
          />
        )}
      </main>
    </div>
  );
}

function DiaryReady({
  summary,
  edMode,
  onAddTap,
}: {
  summary: DailySummaryResponse;
  edMode: boolean;
  onAddTap: () => void;
}) {
  const grouped = groupByMeal(summary.entries);
  const totalCount = summary.entries.length;
  const showNumbers = !edMode;
  return (
    <>
      <p className="food-scanner-diary__caption">
        {totalCount === 0
          ? "Пока ничего не записано. Можно добавить приём через скан."
          : `Сегодня — ${entriesLabel(totalCount)}.`}
      </p>

      {MEAL_ORDER.map((mt) => {
        const items = grouped[mt];
        if (!items || items.length === 0) return null;
        return (
          <section
            key={mt}
            className="food-scanner-diary__group"
            aria-labelledby={`food-diary-group-${mt}`}
          >
            <h2
              id={`food-diary-group-${mt}`}
              className="food-scanner-diary__group-heading"
            >
              <span aria-hidden="true">{MEAL_TYPE_ICON[mt]}</span>{" "}
              {MEAL_TYPE_LABEL[mt]}
            </h2>
            <ul className="food-scanner-diary__list">
              {items.map((entry) => (
                <li
                  key={entry.log_id}
                  className="food-scanner-diary__entry"
                >
                  <div className="food-scanner-diary__entry-main">
                    <span className="food-scanner-diary__entry-time">
                      {formatTimeShort(entry.logged_at_iso)}
                    </span>
                    <span className="food-scanner-diary__entry-dish">
                      {entry.dish_name}
                    </span>
                  </div>
                  {showNumbers && (
                    <span className="food-scanner-diary__entry-cal">
                      ~{entry.calories} ккал
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        );
      })}

      {totalCount > 0 && showNumbers && (
        <section
          className="food-scanner-diary__totals"
          aria-labelledby="food-diary-totals-h2"
        >
          <h2
            id="food-diary-totals-h2"
            className="food-scanner-screen__section-heading"
          >
            Сегодня
          </h2>
          <p className="food-scanner-saved__total">
            {summary.calories_total} / {summary.calories_goal} ккал
          </p>
          <p className="food-scanner-saved__macros">
            Б {summary.protein_g} · Ж {summary.fat_g} · У {summary.carbs_g} г
          </p>
          {summary.ai_comment && (
            <p className="food-scanner-diary__comment">{summary.ai_comment}</p>
          )}
        </section>
      )}

      <div className="food-scanner-screen__cta-stack">
        <button
          type="button"
          className="btn-primary"
          onClick={onAddTap}
        >
          Добавить приём
        </button>
      </div>
    </>
  );
}

function groupByMeal(
  entries: DailySummaryResponse["entries"],
): Partial<Record<MealType, DailySummaryResponse["entries"]>> {
  const out: Partial<Record<MealType, DailySummaryResponse["entries"]>> = {};
  for (const e of entries) {
    const bucket = out[e.meal_type] ?? [];
    bucket.push(e);
    out[e.meal_type] = bucket;
  }
  return out;
}
