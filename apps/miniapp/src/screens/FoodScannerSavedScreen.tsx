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
} from "../lib/food-scanner";
import { getWellnessToday, type WellnessToday } from "../lib/customer-wellness";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

interface RouterState {
  dishName?: string;
  calories?: number | null;
  edMode?: boolean;
}

export function FoodScannerSavedScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом; адрес прежний, теперь объявленный.
  const onBack = useScreenBack(backTo("/customer/main"));
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

  const [summary, setSummary] = useState<WellnessToday | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const s = await getWellnessToday();
      setSummary(s);
      // Признак приходит от источника; отсутствие ключа ПРЯЧЕТ числа —
      // «не смогли спросить» не становится разрешением их показать
      // (§10 Appendix ED Mode). Раньше здесь стояла заглушка, которая
      // на этот вопрос отвечала выдумкой.
      setEdMode(s.nutrition_numbers_hidden !== false);
    } catch (e) {
      setErr(e);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Процент считается, ТОЛЬКО когда известны оба числа. Цели нет —
  // нет и шкалы: доля от несуществующей цели это не ноль процентов,
  // это отсутствие ответа (§65).
  const eaten = summary?.calories_eaten;
  const target = summary?.calories_target;
  const progressPct =
    eaten !== undefined && target !== undefined && target > 0
      ? Math.min(100, Math.round((eaten * 100) / target))
      : null;

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="На главную"
          onClick={onBack}
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
                {eaten !== undefined && (
                  <p className="food-scanner-saved__total">
                    {target !== undefined ? `${eaten} / ${target} ккал` : `${eaten} ккал`}
                  </p>
                )}
                {progressPct !== null && (
                  <>
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
                  </>
                )}
                {summary.pfc && (
                  <p className="food-scanner-saved__macros">
                    Б {summary.pfc.protein_g} · Ж {summary.pfc.fat_g} · У{" "}
                    {summary.pfc.carbs_g} г
                  </p>
                )}
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
