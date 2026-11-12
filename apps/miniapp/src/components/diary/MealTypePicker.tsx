/**
 * Выбор приёма пищи — радиогруппа из четырёх равных кнопок (DRF-2954). Общая
 * для съёмки и результата: одно и то же решение человека выглядит одинаково.
 * Эмодзи-значков нет — как в группах дневника; подписи — `MEAL_TYPE_LABEL`.
 */
import { MEAL_TYPE_LABEL, type MealType } from "../../lib/food-scanner";

const MEAL_TYPES: ReadonlyArray<MealType> = ["breakfast", "lunch", "dinner", "snack"];

export function MealTypePicker({
  value,
  onChange,
  labelledBy,
}: {
  value: MealType;
  onChange: (next: MealType) => void;
  labelledBy: string;
}) {
  return (
    <div className="diary-meals" role="radiogroup" aria-labelledby={labelledBy}>
      {MEAL_TYPES.map((mt) => (
        <button
          key={mt}
          type="button"
          role="radio"
          aria-checked={value === mt}
          className={value === mt ? "diary-meal diary-meal--on" : "diary-meal"}
          onClick={() => onChange(mt)}
        >
          {MEAL_TYPE_LABEL[mt]}
        </button>
      ))}
    </div>
  );
}
