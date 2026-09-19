/**
 * Записи одного дня — Customer Food Diary (DRF-2099).
 *
 * Route: `/customer/food-scanner/day/:date`. Вход — строка дня на экране
 * недели. Источник — сервер (`customer/diary/day?date=` → сводка каталога
 * за дату в поясе человека): записи по приёмам, время и блюдо, калории
 * под тем же ED-признаком, что у дневника (отсутствие признака ПРЯЧЕТ
 * числа — fail-closed, §10 Appendix ED Mode). Пустой день — «в этот день
 * записей нет», это не ошибка.
 *
 * Предел экрана: прошлые дни здесь ТОЛЬКО читаются — правки и удаления
 * записей за прошлые дни нет (не в DRF-2099); за сегодня это делает
 * дневник (F5).
 *
 * Отказы — по имени слага, как у соседей: `nutrition_disabled` — дневник
 * недоступен; `consent_required` — гейт согласия с возвратом сюда;
 * остальное — фраза и «Повторить».
 */
import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import type { FoodDiaryEntry } from "../lib/customer-wellness";
import { dayLabel, getDiaryDay, type DiaryDay } from "../lib/diary-days";
import {
  MEAL_TYPE_ICON,
  MEAL_TYPE_LABEL,
  entriesLabel,
  formatTimeShort,
  type MealType,
} from "../lib/food-scanner";
import { backTo } from "../lib/screen-back";

export const DAY_ROUTE_PATTERN = "/customer/food-scanner/day/:date";
export const dayRoute = (date: string) => `/customer/food-scanner/day/${date}`;
const WEEK_ROUTE = "/customer/food-scanner/week";
const CONSENT_GATE_ROUTE = "/customer/food-scanner/capture";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export const DAY_COPY = {
  title: (date: string) => dayLabel(date),
  count: (n: number) => `${entriesLabel(n)}.`,
  kcal: (kcal: number) => `~${Math.round(kcal)} ккал`,
  empty: "В этот день записей нет.",
  badDate: "Такого дня нет.",
  retry: "Повторить",
  loading: "Загружаю…",
  diaryOff: "Дневник питания пока недоступен.",
  unavailable: "День сейчас недоступен — сервис питания не отвечает. Попробуй чуть позже.",
} as const;

const KNOWN_MEALS = ["breakfast", "lunch", "dinner", "snack"] as const;
const OTHER_MEALS = "__other__";
type GroupKey = MealType | typeof OTHER_MEALS;
const MEAL_ORDER: ReadonlyArray<GroupKey> = [...KNOWN_MEALS, OTHER_MEALS];
const GROUP_ICON: Record<GroupKey, string> = { ...MEAL_TYPE_ICON, [OTHER_MEALS]: "🍽" };
const GROUP_LABEL: Record<GroupKey, string> = { ...MEAL_TYPE_LABEL, [OTHER_MEALS]: "Другое" };

/** Как в дневнике: незнакомый приём остаётся на экране, в «Другое». */
function groupByMeal(entries: FoodDiaryEntry[]): Partial<Record<GroupKey, FoodDiaryEntry[]>> {
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

type Status =
  | { kind: "loading" }
  | { kind: "ready"; day: DiaryDay }
  | { kind: "bad_date" }
  | { kind: "diary_off" }
  | { kind: "error"; text: string };

export function FoodScannerDayScreen() {
  const navigate = useNavigate();
  const { date = "" } = useParams<{ date: string }>();
  const onBack = useScreenBack(backTo(WEEK_ROUTE));
  const [status, setStatus] = useState<Status>({ kind: "loading" });

  const toConsentGate = useCallback(() => {
    navigate(CONSENT_GATE_ROUTE, { replace: true, state: { returnTo: dayRoute(date) } });
  }, [navigate, date]);

  const load = useCallback(async () => {
    if (!ISO_DATE.test(date)) {
      setStatus({ kind: "bad_date" });
      return;
    }
    setStatus({ kind: "loading" });
    try {
      const day = await getDiaryDay(date);
      setStatus({ kind: "ready", day });
    } catch (err) {
      if (err instanceof ApiError && err.slug === "consent_required") {
        toConsentGate();
        return;
      }
      if (err instanceof ApiError && err.slug === "nutrition_disabled") {
        setStatus({ kind: "diary_off" });
        return;
      }
      setStatus({ kind: "error", text: DAY_COPY.unavailable });
    }
  }, [date, toConsentGate]);

  useEffect(() => {
    void load();
  }, [load]);

  const day = status.kind === "ready" ? status.day : null;
  // Признак прячет числа, пока источник не сказал обратное (fail-closed).
  const showNumbers = day?.nutrition_numbers_hidden === false;
  const grouped = groupByMeal(day?.entries ?? []);

  return (
    <div className="food-scanner-screen">
      <header className="records-screen__header">
        <button type="button" className="records-screen__back" aria-label="Назад" onClick={onBack}>
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
        <h1 className="records-screen__title">
          {ISO_DATE.test(date) ? DAY_COPY.title(date) : "День"}
        </h1>
      </header>

      <main className="food-scanner-screen__main">
        {status.kind === "loading" && (
          <p className="food-scanner-diary__caption" role="status">
            {DAY_COPY.loading}
          </p>
        )}

        {status.kind === "bad_date" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{DAY_COPY.badDate}</p>
          </div>
        )}

        {status.kind === "diary_off" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{DAY_COPY.diaryOff}</p>
          </div>
        )}

        {status.kind === "error" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{status.text}</p>
            <button type="button" className="btn-secondary" onClick={() => void load()}>
              {DAY_COPY.retry}
            </button>
          </div>
        )}

        {day && day.entries.length === 0 && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{DAY_COPY.empty}</p>
          </div>
        )}

        {day && day.entries.length > 0 && (
          <>
            <p className="food-scanner-diary__caption">{DAY_COPY.count(day.entries.length)}</p>
            {MEAL_ORDER.map((mt) => {
              const items = grouped[mt];
              if (!items || items.length === 0) return null;
              return (
                <section
                  key={mt}
                  className="food-scanner-diary__group"
                  aria-labelledby={`food-day-group-${mt}`}
                >
                  <h2 id={`food-day-group-${mt}`} className="food-scanner-diary__group-heading">
                    <span aria-hidden="true">{GROUP_ICON[mt]}</span> {GROUP_LABEL[mt]}
                  </h2>
                  <ul className="food-scanner-diary__list">
                    {items.map((entry) => (
                      <li key={entry.id} className="food-scanner-diary__entry">
                        <div className="food-scanner-diary__entry-main">
                          <span className="food-scanner-diary__entry-time">
                            {formatTimeShort(entry.logged_at)}
                          </span>
                          <span className="food-scanner-diary__entry-dish">{entry.dish_name}</span>
                        </div>
                        {showNumbers && (
                          <span className="food-scanner-diary__entry-cal">
                            {DAY_COPY.kcal(entry.calories)}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </>
        )}
      </main>
    </div>
  );
}
