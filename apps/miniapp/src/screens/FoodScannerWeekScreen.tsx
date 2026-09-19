/**
 * Дневник за неделю — Customer Food Diary (DRF-2099).
 *
 * Route: `/customer/food-scanner/week`. Вход — «Неделя» из дневника (F5).
 * Источник — сервер (`customer/diary/days` → каталог): строка на КАЖДЫЙ
 * день, границы суток каталог считает по поясу человека; «сегодня» — конец
 * первой (беспараметровой) недели из ответа, не часы устройства.
 *
 * Что здесь есть:
 *   - «N из 7 дней с записями» — факт, не оценка;
 *   - строка дня: дата, «N приёмов · ~K ккал» или «—»; калории под тем же
 *     ED-признаком, что у дневника (`nutrition_numbers_hidden`; отсутствие
 *     признака ПРЯЧЕТ числа — fail-closed, §10 Appendix ED Mode);
 *   - нажатие дня → экран дня (`/customer/food-scanner/day/:date`);
 *   - «Раньше» / «Позже» — неделями; «раньше» не дальше 28 дней по пределу
 *     ручки каталога: на пределе кнопка НЕАКТИВНА, а не ошибка; «позже» не
 *     дальше сегодня.
 *
 * Чего здесь нет (В-5, DRF-1332): напоминаний, серий, «пропущенных дней»
 * и оценок — «0 из 7» показывается тем же тоном, что «7 из 7».
 *
 * Отказы — по имени слага, как у соседей: `nutrition_disabled` — дневник
 * недоступен; `consent_required` — гейт согласия (живёт в экране съёмки) с
 * возвратом сюда; остальное — фраза и «Повторить».
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import {
  canGoEarlier,
  canGoLater,
  dayLabel,
  earlierWeek,
  getDiaryDays,
  laterWeek,
  periodLabel,
  type DiaryDayRow,
  type DiaryDays,
  WEEK_DAYS,
} from "../lib/diary-days";
import { entriesLabel } from "../lib/food-scanner";
import { backTo } from "../lib/screen-back";
import { dayRoute } from "./FoodScannerDayScreen";
import { DIARY_ROUTE } from "./FoodScannerManualScreen";

export const WEEK_ROUTE = "/customer/food-scanner/week";
const CONSENT_GATE_ROUTE = "/customer/food-scanner/capture";

export const WEEK_COPY = {
  title: "Неделя",
  openFromDiary: "Неделя",
  summary: (n: number) => `${n} из ${WEEK_DAYS} дней с записями`,
  meals: (n: number, kcal: number) => `${entriesLabel(n)} · ~${Math.round(kcal)} ккал`,
  mealsOnly: (n: number) => entriesLabel(n),
  none: "—",
  earlier: "Раньше",
  later: "Позже",
  retry: "Повторить",
  loading: "Загружаю…",
  diaryOff: "Дневник питания пока недоступен.",
  unavailable: "Неделя сейчас недоступна — сервис питания не отвечает. Попробуй чуть позже.",
} as const;

export function weekRefusalText(err: unknown): string {
  if (err instanceof ApiError && err.slug === "nutrition_disabled") return WEEK_COPY.diaryOff;
  return WEEK_COPY.unavailable;
}

type Status =
  | { kind: "loading" }
  | { kind: "ready"; week: DiaryDays }
  | { kind: "diary_off" }
  | { kind: "error"; text: string };

export function FoodScannerWeekScreen() {
  const navigate = useNavigate();
  const onBack = useScreenBack(backTo(DIARY_ROUTE));

  const [status, setStatus] = useState<Status>({ kind: "loading" });
  // «Сегодня» в поясе человека — конец первой недели из ответа каталога.
  const [today, setToday] = useState<string | null>(null);
  // Последний запрошенный период — чтобы «Повторить» повторял его, а не первую неделю.
  const period = useRef<{ from?: string; to?: string }>({});

  const toConsentGate = useCallback(() => {
    navigate(CONSENT_GATE_ROUTE, { replace: true, state: { returnTo: WEEK_ROUTE } });
  }, [navigate]);

  const load = useCallback(
    async (from?: string, to?: string) => {
      period.current = { from, to };
      setStatus({ kind: "loading" });
      try {
        const week = await getDiaryDays(from, to);
        setToday((t) => t ?? week.to);
        setStatus({ kind: "ready", week });
      } catch (err) {
        if (err instanceof ApiError && err.slug === "consent_required") {
          toConsentGate();
          return;
        }
        if (err instanceof ApiError && err.slug === "nutrition_disabled") {
          setStatus({ kind: "diary_off" });
          return;
        }
        setStatus({ kind: "error", text: weekRefusalText(err) });
      }
    },
    [toConsentGate],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const week = status.kind === "ready" ? status.week : null;
  const anchor = today ?? week?.to ?? null;
  const earlierOk = week !== null && anchor !== null && canGoEarlier(week.from, anchor);
  const laterOk = week !== null && anchor !== null && canGoLater(week.to, anchor);
  // Признак прячет числа, пока источник не сказал обратное (fail-closed).
  const showNumbers = week?.nutrition_numbers_hidden === false;
  const daysWithEntries = week ? week.days.filter((d) => d.has_entries).length : 0;

  const rowText = (row: DiaryDayRow): string => {
    if (!row.has_entries) return WEEK_COPY.none;
    if (showNumbers && row.kcal !== null) return WEEK_COPY.meals(row.meals_count, row.kcal);
    return WEEK_COPY.mealsOnly(row.meals_count);
  };

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
        <h1 className="records-screen__title">{WEEK_COPY.title}</h1>
      </header>

      <main className="food-scanner-screen__main">
        {status.kind === "loading" && (
          <p className="food-scanner-diary__caption" role="status">
            {WEEK_COPY.loading}
          </p>
        )}

        {status.kind === "diary_off" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{WEEK_COPY.diaryOff}</p>
          </div>
        )}

        {status.kind === "error" && (
          <div className="food-scanner-diary__unreadable" role="status">
            <p>{status.text}</p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void load(period.current.from, period.current.to)}
            >
              {WEEK_COPY.retry}
            </button>
          </div>
        )}

        {week && (
          <>
            <div className="food-scanner-week__nav">
              <button
                type="button"
                className="food-scanner-diary__entry-action"
                disabled={!earlierOk}
                onClick={() => {
                  const next = earlierWeek(week.from);
                  void load(next.from, next.to);
                }}
              >
                {WEEK_COPY.earlier}
              </button>
              <span className="food-scanner-week__period">{periodLabel(week.from, week.to)}</span>
              <button
                type="button"
                className="food-scanner-diary__entry-action"
                disabled={!laterOk}
                onClick={() => {
                  if (anchor === null) return;
                  const next = laterWeek(week.to, anchor);
                  void load(next.from, next.to);
                }}
              >
                {WEEK_COPY.later}
              </button>
            </div>

            <p className="food-scanner-diary__caption">{WEEK_COPY.summary(daysWithEntries)}</p>

            <ul className="food-scanner-diary__list">
              {week.days.map((row) => (
                <li key={row.date} className="food-scanner-diary__entry">
                  <button
                    type="button"
                    className="food-scanner-week__day"
                    onClick={() => navigate(dayRoute(row.date))}
                  >
                    <span className="food-scanner-diary__entry-dish">{dayLabel(row.date)}</span>
                    <span className="food-scanner-diary__entry-time">{rowText(row)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
      </main>
    </div>
  );
}
