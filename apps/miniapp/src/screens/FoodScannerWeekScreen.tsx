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
 *
 * DRF-2954 — вид по доске «Дневник» (кадр 12), решение владельца 12.11 п.8:
 * полоса из семи дней с отметкой и легенда «Калории посчитаны / Есть записи,
 * калории не посчитаны / Нет записей». День нажимается в полосе; строки под
 * ней — те же цифры, без второй кнопки на тот же день. Отметки — формой
 * (галочка, точка, пустой круг) и нейтральными цветами: ряд зелёных галочек
 * читался бы серией, а серий здесь нет (В-5). В режиме «Без чисел» —
 * только «есть записи / нет», легенды нет (`dayMark`).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { RecordIcon } from "../components/records/RecordIcons";
import { DiaryState } from "../components/diary/DiaryState";
import { RecordButton } from "../components/records/RecordsState";

import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import {
  canGoEarlier,
  canGoLater,
  dayLabel,
  dayMark,
  dayOfMonth,
  earlierWeek,
  getDiaryDays,
  laterWeek,
  periodLabel,
  type DayMark,
  type DiaryDayRow,
  type DiaryDays,
  WEEK_DAYS,
  weekdayShort,
} from "../lib/diary-days";
import { entriesLabel } from "../lib/food-scanner";
import { KCAL_TOTAL_AI_NOTE, kcalTotalMarks } from "../lib/format";
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
  /** DRF-2766 — короткая форма «итог неполный» для строки недели; [confirmable]. */
  incomplete: "итог неполный",
  none: "—",
  earlier: "Раньше",
  later: "Позже",
  retry: "Повторить",
  loading: "Загружаю…",
  diaryOff: "Дневник питания пока недоступен.",
  unavailable: "Неделя сейчас недоступна — сервис питания не отвечает. Попробуй чуть позже.",
  // Легенда полосы — слова владельца 12.11 (DRF-2954, п.8).
  markCounted: "Калории посчитаны",
  markUncounted: "Есть записи, калории не посчитаны",
  markNone: "Нет записей",
} as const;

/** Отметка дня — значок в круге; форма, а не только цвет, различает три вида. */
function MarkDot({ mark }: { mark: DayMark }) {
  if (mark === "counted") {
    return (
      <span className="diary-mark diary-mark--counted" aria-hidden="true">
        <RecordIcon name="check" size={12} />
      </span>
    );
  }
  if (mark === "uncounted") return <span className="diary-mark diary-mark--uncounted" aria-hidden="true" />;
  if (mark === "entries") return <span className="diary-mark diary-mark--entries" aria-hidden="true" />;
  return <span className="diary-mark diary-mark--none" aria-hidden="true" />;
}

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
    if (showNumbers && row.kcal !== null) {
      // DRF-2766 фаза 2 — итог дня с оценками ИИ и неполный итог названы.
      const marks = kcalTotalMarks(row);
      const notes = [marks.approx ? KCAL_TOTAL_AI_NOTE : null, marks.incomplete ? WEEK_COPY.incomplete : null].filter(Boolean);
      const base = WEEK_COPY.meals(row.meals_count, row.kcal);
      return notes.length ? `${base} · ${notes.join(" · ")}` : base;
    }
    return WEEK_COPY.mealsOnly(row.meals_count);
  };

  // Имя дня для экранного диктора: дата + отметка. Видимое «Пн 14» входит в
  // него (WCAG 2.5.3); «включая оценки ИИ» — та же пометка, что в строке.
  const dayName = (row: DiaryDayRow): string => {
    const mark = dayMark(row, showNumbers);
    const what =
      mark === "counted"
        ? kcalTotalMarks(row).approx
          ? `${WEEK_COPY.markCounted}, ${KCAL_TOTAL_AI_NOTE}`
          : WEEK_COPY.markCounted
        : mark === "uncounted"
          ? WEEK_COPY.markUncounted
          : mark === "entries"
            ? WEEK_COPY.mealsOnly(row.meals_count)
            : WEEK_COPY.markNone;
    return `${dayLabel(row.date)}: ${what}`;
  };

  return (
    <div className="cr-screen cr-screen--nested diary-screen">
      <header className="cr-topbar diary-topbar">
        <button type="button" className="cr-back" aria-label="Назад" onClick={onBack}>
          <RecordIcon name="back" size={24} />
        </button>
        <h1 className="diary-topbar__title">{WEEK_COPY.title}</h1>
      </header>

      <main className="diary-main">
        {status.kind === "loading" && (
          <p className="diary-loading" role="status">
            {WEEK_COPY.loading}
          </p>
        )}

        {status.kind === "diary_off" && <DiaryState icon="info" lead={WEEK_COPY.diaryOff} />}

        {status.kind === "error" && (
          <DiaryState icon="alert" lead={status.text}>
            <RecordButton
              variant="soft"
              onClick={() => void load(period.current.from, period.current.to)}
            >
              {WEEK_COPY.retry}
            </RecordButton>
          </DiaryState>
        )}

        {week && (
          <>
            <div className="cr-week diary-week">
              <div className="cr-week__head">
                <button
                  type="button"
                  className="cr-week__nav"
                  aria-label={WEEK_COPY.earlier}
                  disabled={!earlierOk}
                  onClick={() => {
                    const next = earlierWeek(week.from);
                    void load(next.from, next.to);
                  }}
                >
                  <RecordIcon name="back" />
                </button>
                <span className="cr-week__month diary-week__period">
                  {periodLabel(week.from, week.to)}
                </span>
                <button
                  type="button"
                  className="cr-week__nav"
                  aria-label={WEEK_COPY.later}
                  disabled={!laterOk}
                  onClick={() => {
                    if (anchor === null) return;
                    const next = laterWeek(week.to, anchor);
                    void load(next.from, next.to);
                  }}
                >
                  <RecordIcon name="chevron" />
                </button>
              </div>
              <div className="cr-week__days">
                {week.days.map((row) => (
                  <button
                    type="button"
                    key={row.date}
                    className="cr-week__day diary-week__day"
                    aria-label={dayName(row)}
                    onClick={() => navigate(dayRoute(row.date))}
                  >
                    <span className="cr-week__name">{weekdayShort(row.date)}</span>
                    <span className="cr-week__num">{dayOfMonth(row.date)}</span>
                    <MarkDot mark={dayMark(row, showNumbers)} />
                  </button>
                ))}
              </div>
            </div>

            {showNumbers && (
              <ul className="diary-legend">
                <li className="diary-legend__item">
                  <MarkDot mark="counted" />
                  {WEEK_COPY.markCounted}
                </li>
                <li className="diary-legend__item">
                  <MarkDot mark="uncounted" />
                  {WEEK_COPY.markUncounted}
                </li>
                <li className="diary-legend__item">
                  <MarkDot mark="none" />
                  {WEEK_COPY.markNone}
                </li>
              </ul>
            )}

            <p className="diary-caption">{WEEK_COPY.summary(daysWithEntries)}</p>

            {/* Цифры дня — строками; нажимается день в полосе выше. */}
            <ul className="diary-week-rows">
              {week.days.map((row) => (
                <li key={row.date} className="diary-week-row">
                  <span className="diary-week-row__day">{dayLabel(row.date)}</span>
                  <span className="diary-week-row__text">{rowText(row)}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </main>
    </div>
  );
}
