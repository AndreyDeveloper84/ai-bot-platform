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
  formatTimeShort,
  type MealType,
} from "../lib/food-scanner";
import {
  loadDiaryToday,
  type DiaryToday,
  type FoodDiaryEntry,
} from "../lib/customer-wellness";
import { useScreenBack } from "../hooks/useScreenBack";
import { backTo } from "../lib/screen-back";

/**
 * ЧЕТЫРЕ состояния, и свести любые два нельзя — у каждого своя правда
 * и своя цена молчания:
 *
 * * `loading`     — ждём;
 * * `error`       — **ответ не пришёл**: сеть, таймаут, не-2xx;
 * * `unreadable`  — **ответ пришёл, но без записей**: сервер не смог
 *   прочитать питательную половину. Повтор осмыслен, но сообщение
 *   другое, и «сегодня ничего не записано» тут было бы ложью;
 * * `ready`       — записи (возможно, ноль штук — это ответ, а не сбой).
 *
 * До 08.09.2026 состояний было три, и `ready` с пустым списком был
 * неотличим от «данные не доехали»: заглушка возвращала нули и пустой
 * список одинаково в обоих случаях.
 */
type Status =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "unreadable" }
  | {
      kind: "ready";
      day: Extract<DiaryToday, { state: "empty" | "entries" }>;
    };

const KNOWN_MEALS = ["breakfast", "lunch", "dinner", "snack"] as const;

/** Куда попадает приём пищи, которого экран ещё не знает. */
const OTHER_MEALS = "__other__";
type GroupKey = MealType | typeof OTHER_MEALS;

const MEAL_ORDER: ReadonlyArray<GroupKey> = [...KNOWN_MEALS, OTHER_MEALS];

const GROUP_ICON: Record<GroupKey, string> = {
  ...MEAL_TYPE_ICON,
  [OTHER_MEALS]: "🍽",
};
const GROUP_LABEL: Record<GroupKey, string> = {
  ...MEAL_TYPE_LABEL,
  [OTHER_MEALS]: "Другое",
};

export function FoodScannerDiaryScreen() {
  const navigate = useNavigate();

  // Возврат (DRF-1493) — на дом; адрес прежний, теперь объявленный.
  const onBack = useScreenBack(backTo("/customer/main"));
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const load = useCallback(async () => {
    setStatus({ kind: "loading" });
    try {
      const day = await loadDiaryToday();
      // «Ответ пришёл, записей в нём нет» — своё состояние, не ошибка
      // и не пустой день.
      setStatus(
        day.state === "unreadable" ? { kind: "unreadable" } : { kind: "ready", day },
      );
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

        {status.kind === "unreadable" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>Не удалось загрузить дневник за сегодня.</p>
            <p className="food-scanner-diary__unreadable-hint">
              Записи не потерялись — их сейчас не удалось прочитать.
            </p>
            <button type="button" className="btn-secondary" onClick={load}>
              Попробовать снова
            </button>
          </div>
        )}

        {status.kind === "ready" && (
          <DiaryReady
            day={status.day}
            onAddTap={() => navigate("/customer/food-scanner/capture")}
          />
        )}
      </main>
    </div>
  );
}

function DiaryReady({
  day,
  onAddTap,
}: {
  day: Extract<DiaryToday, { state: "empty" | "entries" }>;
  onAddTap: () => void;
}) {
  const entries = day.state === "entries" ? day.entries : [];
  const grouped = groupByMeal(entries);
  const totalCount = entries.length;
  // Признак приходит от источника; его отсутствие уже превращено в
  // «прятать» на уровне чтения (fail-closed, §10 Appendix ED Mode).
  const showNumbers = !day.hideNumbers;
  const { calories_eaten: eaten, calories_target: target, pfc } = day.today;
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
              <span aria-hidden="true">{GROUP_ICON[mt]}</span>{" "}
              {GROUP_LABEL[mt]}
            </h2>
            <ul className="food-scanner-diary__list">
              {items.map((entry) => (
                <li key={entry.id} className="food-scanner-diary__entry">
                  <div className="food-scanner-diary__entry-main">
                    <span className="food-scanner-diary__entry-time">
                      {formatTimeShort(entry.logged_at)}
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
          {/* Цель рисуется, ТОЛЬКО когда она есть. Ключа нет — цели нет
              (анкету человек не проходил), и «/ 0 ккал» на её месте
              было бы чужим числом, выданным за его собственное (§65). */}
          {eaten !== undefined && (
            <p className="food-scanner-saved__total">
              {target !== undefined ? `${eaten} / ${target} ккал` : `${eaten} ккал`}
            </p>
          )}
          {/* БЖУ — строка целевая: живёт и гаснет вместе с целью, ровно
              как на дашборде. Считать его здесь не из чего и незачем:
              настоящее приходит с каждой записью. */}
          {pfc && (
            <p className="food-scanner-saved__macros">
              Б {pfc.protein_g} · Ж {pfc.fat_g} · У {pfc.carbs_g} г
            </p>
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

/**
 * Разложить записи по приёмам пищи.
 *
 * `meal_type` приходит строкой, и у источника их сегодня ровно четыре
 * (`nutrition/models.py::FoodLog.MealType`). Пятый когда-нибудь
 * появится — и тогда запись обязана **остаться на экране**, а не
 * исчезнуть: тихий пропуск здесь означал бы, что экран решает, какую
 * из съеденных человеком тарелок он ему покажет. Незнакомый тип
 * попадает в «Другое» (§78: у каждого пропуска должно быть имя, а
 * лучший вид имени — отсутствие самого пропуска).
 */
function groupByMeal(
  entries: FoodDiaryEntry[],
): Partial<Record<GroupKey, FoodDiaryEntry[]>> {
  const out: Partial<Record<GroupKey, FoodDiaryEntry[]>> = {};
  for (const e of entries) {
    const key: GroupKey = (KNOWN_MEALS as readonly string[]).includes(e.meal_type)
      ? (e.meal_type as MealType)
      : OTHER_MEALS;
    const bucket = out[key] ?? [];
    bucket.push(e);
    out[key] = bucket;
  }
  return out;
}
