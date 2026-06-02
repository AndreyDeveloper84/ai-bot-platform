/**
 * F4 Saved Confirmation — Customer Food Scanner Phase B.
 *
 * Route: `/customer/food-scanner/saved` (router state from F3 carries
 * `{ dishName, calories, edMode }`).
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §6.
 *
 * # ED-mode rendering
 *
 * If `edMode`, hide all numbers (recap line, daily total, progress
 * bar, macros). Show only «✓ Записала» + dish name + non-numeric
 * dish recap.
 *
 * # No cross-domain insight card
 *
 * Per spec §6 + memory `project_cross_domain_insight_safety_gap` —
 * REMOVED from MVP. Anti-medical safety filter not in production yet.
 * Re-introduce post-pilot after Alpha safety audit.
 */

import { useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { StateError } from "../components/StateError";
import {
  fetchDailySummary,
  fetchHealthFlags,
  type DailySummaryResponse,
} from "../lib/food-scanner";

interface RouterState {
  dishName?: string;
  calories?: number | null;
  edMode?: boolean;
}

export function FoodScannerSavedScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  const dishName = state.dishName ?? "Запись";
  const recapCalories = state.calories ?? null;
  // Default ED-mode TRUE — defence-in-depth for deep-link refresh
  // (friendly CR #5). Router state is lost on refresh; if a customer
  // with eating_disorder hits /saved fresh, we must NOT leak numbers
  // until fetchHealthFlags confirms otherwise.
  const [edMode, setEdMode] = useState<boolean>(
    state.edMode === undefined ? true : Boolean(state.edMode),
  );

  const [summary, setSummary] = useState<DailySummaryResponse | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [s, flags] = await Promise.all([
        fetchDailySummary(),
        fetchHealthFlags(),
      ]);
      setSummary(s);
      setEdMode(Boolean(flags.health_flags.eating_disorder));
    } catch (e) {
      setErr(e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const progressPct =
    summary && summary.calories_goal > 0
      ? Math.min(
          100,
          Math.round((summary.calories_total * 100) / summary.calories_goal),
        )
      : 0;

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="На главную"
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
        <h1 className="records-screen__title">Записано</h1>
      </header>

      <main className="food-scanner-screen__main">
        <div className="food-scanner-saved__check" role="status">
          <svg width="32" height="32" viewBox="0 0 32 32" fill="none">
            <circle
              cx="16"
              cy="16"
              r="14"
              stroke="currentColor"
              strokeWidth="2"
            />
            <path
              d="M9 16l5 5 9-10"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          <span className="food-scanner-saved__check-text">Записала</span>
        </div>

        {edMode ? (
          <p className="food-scanner-saved__recap">{dishName} — записала.</p>
        ) : (
          <p className="food-scanner-saved__recap">
            {dishName}
            {recapCalories != null ? ` — ~${recapCalories} ккал.` : "."}
          </p>
        )}

        {!edMode && (
          <section
            className="food-scanner-saved__daily"
            aria-labelledby="food-saved-daily-h2"
          >
            <h2
              id="food-saved-daily-h2"
              className="food-scanner-screen__section-heading"
            >
              Сегодня
            </h2>
            {err !== null && <StateError err={err} onRetry={load} />}
            {err === null && summary && (
              <>
                <p className="food-scanner-saved__total">
                  {summary.calories_total} / {summary.calories_goal} ккал
                </p>
                <div
                  className="food-scanner-saved__bar"
                  role="progressbar"
                  aria-valuenow={progressPct}
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-label={`Прогресс по калориям: ${progressPct} процентов`}
                >
                  <div
                    className="food-scanner-saved__bar-fill"
                    style={{ width: `${progressPct}%` }}
                  />
                </div>
                <p className="food-scanner-saved__pct">{progressPct} %</p>
                <p className="food-scanner-saved__macros">
                  Б {summary.protein_g} · Ж {summary.fat_g} · У{" "}
                  {summary.carbs_g} г
                </p>
              </>
            )}
          </section>
        )}

        <div className="food-scanner-screen__cta-stack">
          <button
            type="button"
            className="btn-primary"
            onClick={() => navigate("/customer/food-scanner/diary")}
          >
            Открыть дневник
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => navigate("/customer/main")}
          >
            Готово
          </button>
        </div>
      </main>
    </div>
  );
}
