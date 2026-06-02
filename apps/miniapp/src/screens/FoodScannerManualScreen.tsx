/**
 * Manual entry fallback — Customer Food Scanner.
 *
 * Route: `/customer/food-scanner/manual`
 *
 * Spec: `docs/screens/customer-food-scanner-flow.md` §8.
 *
 * Activated from F2 error states «Не разобралась» / «Сервис
 * недоступен» → `Написать вручную`. Customer types dish name +
 * optional portion + meal-type. log_meal() called with `scan_id=None`.
 */

import { useCallback, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { Snackbar } from "../components/Snackbar";
import {
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  defaultMealTypeForHour,
  logMeal,
  type MealType,
} from "../lib/food-scanner";

interface RouterState {
  mealType?: MealType;
}

const MEAL_TYPES: ReadonlyArray<MealType> = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
];

export function FoodScannerManualScreen() {
  const navigate = useNavigate();
  const location = useLocation();
  const state = (location.state ?? {}) as RouterState;
  const [dishName, setDishName] = useState("");
  const [mealType, setMealType] = useState<MealType>(
    state.mealType ?? defaultMealTypeForHour(new Date().getHours()),
  );
  const [portion, setPortion] = useState("");
  const [busy, setBusy] = useState(false);
  const [snack, setSnack] = useState<{ visible: boolean; message: string }>({
    visible: false,
    message: "",
  });

  const onSave = useCallback(async () => {
    const trimmed = dishName.trim();
    if (!trimmed) {
      setSnack({
        visible: true,
        message: "Подскажи, что съела — пара слов.",
      });
      return;
    }
    setBusy(true);
    try {
      await logMeal({
        dish_name: trimmed,
        meal_type: mealType,
        portion_multiplier: 1.0,
      });
      navigate("/customer/food-scanner/saved", {
        replace: true,
        state: { dishName: trimmed, calories: null, edMode: false },
      });
    } catch {
      setSnack({
        visible: true,
        message: "Не получилось сохранить. Попробуй ещё раз.",
      });
    } finally {
      setBusy(false);
    }
  }, [dishName, mealType, navigate]);

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button
          type="button"
          className="records-screen__back"
          aria-label="Назад"
          onClick={() => navigate(-1)}
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
        <h1 className="records-screen__title">Запись вручную</h1>
      </header>

      <main className="food-scanner-screen__main">
        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-manual-name-h3"
        >
          <h3
            id="food-manual-name-h3"
            className="food-scanner-screen__section-heading"
          >
            Что съела?
          </h3>
          <input
            type="text"
            className="food-scanner-result__name-input"
            value={dishName}
            onChange={(e) => setDishName(e.target.value)}
            placeholder="например, гречка с курицей"
            aria-label="Название блюда"
          />
        </section>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-manual-meal-h3"
        >
          <h3
            id="food-manual-meal-h3"
            className="food-scanner-screen__section-heading"
          >
            Когда
          </h3>
          <div
            className="food-scanner-screen__meal-chips"
            role="radiogroup"
            aria-labelledby="food-manual-meal-h3"
          >
            {MEAL_TYPES.map((mt) => (
              <button
                key={mt}
                type="button"
                role="radio"
                aria-checked={mealType === mt}
                className={`food-scanner-screen__chip${
                  mealType === mt ? " food-scanner-screen__chip--active" : ""
                }`}
                onClick={() => setMealType(mt)}
              >
                <span aria-hidden="true">{MEAL_TYPE_ICON[mt]}</span>
                <span>{MEAL_TYPE_LABEL[mt]}</span>
              </button>
            ))}
          </div>
        </section>

        <section
          className="food-scanner-screen__section"
          aria-labelledby="food-manual-portion-h3"
        >
          <h3
            id="food-manual-portion-h3"
            className="food-scanner-screen__section-heading"
          >
            Порция (необязательно)
          </h3>
          <input
            type="text"
            inputMode="numeric"
            className="food-scanner-result__name-input"
            value={portion}
            onChange={(e) => setPortion(e.target.value)}
            placeholder="например, 150"
            aria-label="Порция в граммах"
          />
        </section>

        <div className="food-scanner-screen__cta-stack">
          <button
            type="button"
            className="btn-primary"
            disabled={busy}
            onClick={onSave}
          >
            Записать
          </button>
        </div>
      </main>

      <Snackbar
        visible={snack.visible}
        message={snack.message}
        durationMs={3000}
        onTimeout={() => setSnack({ visible: false, message: "" })}
      />
    </div>
  );
}
