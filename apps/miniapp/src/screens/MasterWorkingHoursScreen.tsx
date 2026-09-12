/**
 * Экран 06 — рабочие часы (DRF-1817, M25; макет FINAL FREEZE §13).
 *
 * Тот же контракт часов, что у обычного кабинета: `GET/PUT /working-hours`
 * бота → каталог (M24 → M23). Onboarding-only записей нет: экран пишет туда
 * же, откуда движок слотов читает.
 *
 * Состояния макета:
 * - A — дни чекбоксами, **ничего не предвыбрано** (пустой каталог = пустые
 *   чекбоксы; «10:00–19:00» из воздуха здесь не берётся);
 * - B — одно время начала/конца и «Применить ко всем выбранным дням»;
 * - C — редактор дня в листе: один переключатель «Рабочий день», время и
 *   перерыв; отдельного «сделать выходным» нет — это и есть переключатель;
 * - D — неделя целиком и «Сохранить расписание».
 *
 * Семантика (§13.2): часы — не слоты, которые видит клиент. Единственная
 * верная фраза — «По этим часам Ayla будет рассчитывать доступное время для
 * записи»; «Клиенты увидят ваше расписание» на экране не звучит.
 *
 * Проверка на месте (§13.3): начало раньше конца, перерыв внутри смены —
 * та же, что на сервере; сервер остаётся последним словом (400/409).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SheetChrome } from "../components/PersonalDataSheets";
import { DelayedSkeleton, ServiceCardSkeleton } from "../components/Skeleton";
import { Snackbar } from "../components/Snackbar";
import { StateError } from "../components/StateError";
import { ToggleSwitch } from "../components/ToggleSwitch";
import { ApiError } from "../lib/api";
import {
  getWorkingHours,
  putWorkingHours,
  type WorkingHoursDay,
  type WorkingHoursResponse,
} from "../lib/master-api";
import { setBackButton, signalReady } from "../lib/max-sdk";

export const WORKING_HOURS_ROUTE = "/solo/working-hours";
export const DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"] as const;
export const DAY_NAMES = [
  "Понедельник",
  "Вторник",
  "Среда",
  "Четверг",
  "Пятница",
  "Суббота",
  "Воскресенье",
] as const;

export const TITLE = "Рабочие часы";
export const SEMANTIC_NOTE = "По этим часам Ayla будет рассчитывать доступное время для записи.";
export const PICK_DAYS_LABEL = "Выберите рабочие дни";
export const APPLY_ALL_LABEL = "Применить ко всем выбранным дням";
export const SAVE_LABEL = "Сохранить расписание";
export const SAVE_LATER_LABEL = "Сохранить и продолжить позже";
export const SAVED_MESSAGE = "Расписание сохранено!";
export const WORKING_DAY_SWITCH = "Рабочий день";
export const INVALID_INTERVAL = "Начало должно быть раньше конца.";
export const INVALID_BREAK = "Перерыв должен быть внутри рабочего времени.";
export const NO_DAYS_YET = "Пока ни одного рабочего дня.";
export const CONFLICT_MESSAGE = "В это время уже есть записи. Сначала разберитесь с ними.";
export const NOT_LINKED_MESSAGE =
  "Профиль ещё не связан с каталогом — сохранить часы пока некуда.";

type Phase =
  | { kind: "loading" }
  | { kind: "error"; err: unknown }
  | { kind: "ready"; timezone: string | null };

export type WeekDraft = WorkingHoursDay[];

export function emptyWeek(): WeekDraft {
  return Array.from({ length: 7 }, (_, d) => ({
    day_of_week: d,
    is_working_day: false,
    start_time: null,
    end_time: null,
    break_start: null,
    break_end: null,
  }));
}

/** Неделя из ответа сервера, всегда 7 дней в порядке Пн…Вс. */
export function weekFrom(schedule: WorkingHoursDay[]): WeekDraft {
  const week = emptyWeek();
  for (const row of schedule) {
    const d = Number(row.day_of_week);
    if (d >= 0 && d < 7) {
      week[d] = {
        day_of_week: d,
        is_working_day: Boolean(row.is_working_day),
        start_time: row.start_time ? row.start_time.slice(0, 5) : null,
        end_time: row.end_time ? row.end_time.slice(0, 5) : null,
        break_start: row.break_start ? row.break_start.slice(0, 5) : null,
        break_end: row.break_end ? row.break_end.slice(0, 5) : null,
      };
    }
  }
  return week;
}

/** Ошибка дня по правилам §13.3 или null. Та же проверка, что на сервере. */
export function dayError(day: WorkingHoursDay): string | null {
  if (!day.is_working_day) return null;
  if (!day.start_time || !day.end_time || day.start_time >= day.end_time) return INVALID_INTERVAL;
  if (day.break_start || day.break_end) {
    if (!day.break_start || !day.break_end) return INVALID_BREAK;
    if (day.break_start >= day.break_end) return INVALID_BREAK;
    if (day.break_start < day.start_time || day.break_end > day.end_time) return INVALID_BREAK;
  }
  return null;
}

export function weekErrors(week: WeekDraft): string[] {
  return week.map(dayError).filter((e): e is string => e !== null);
}

function summary(day: WorkingHoursDay): string {
  if (!day.is_working_day || !day.start_time || !day.end_time) return "Выходной";
  const brk = day.break_start && day.break_end ? ` · перерыв ${day.break_start}–${day.break_end}` : "";
  return `${day.start_time}–${day.end_time}${brk}`;
}

export function MasterWorkingHoursScreen() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [week, setWeek] = useState<WeekDraft>(emptyWeek());
  // Состояние B — одно время на все выбранные дни. Пусто, пока человек не
  // ввёл: предвыбора нет (макет A/B).
  const [bulkStart, setBulkStart] = useState("");
  const [bulkEnd, setBulkEnd] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [snack, setSnack] = useState<string | null>(null);
  const editTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    setBackButton(true);
    signalReady();
  }, []);

  const load = useCallback(async () => {
    setPhase({ kind: "loading" });
    try {
      const data = await getWorkingHours();
      setWeek(weekFrom(data.schedule));
      setPhase({ kind: "ready", timezone: data.timezone });
    } catch (err) {
      setPhase({ kind: "error", err });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleDay = (d: number) =>
    setWeek((prev) =>
      prev.map((day) =>
        day.day_of_week === d
          ? {
              ...day,
              is_working_day: !day.is_working_day,
              // Снятый день теряет время; включённый берёт общее, если оно есть.
              start_time: day.is_working_day ? null : day.start_time ?? (bulkStart || null),
              end_time: day.is_working_day ? null : day.end_time ?? (bulkEnd || null),
              break_start: day.is_working_day ? null : day.break_start,
              break_end: day.is_working_day ? null : day.break_end,
            }
          : day,
      ),
    );

  const applyToSelected = () => {
    if (!bulkStart || !bulkEnd) return;
    setWeek((prev) =>
      prev.map((day) =>
        day.is_working_day ? { ...day, start_time: bulkStart, end_time: bulkEnd } : day,
      ),
    );
  };

  const errors = weekErrors(week);
  const selectedCount = week.filter((d) => d.is_working_day).length;
  const canSave = errors.length === 0 && !saving;

  const save = async (then: "stay" | "later") => {
    if (!canSave) return;
    setSaving(true);
    setSaveError(null);
    try {
      const saved: WorkingHoursResponse = await putWorkingHours(week);
      setWeek(weekFrom(saved.schedule));
      setSnack(SAVED_MESSAGE);
      if (then === "later") navigate("/solo/setup");
    } catch (err) {
      setSaveError(saveErrorText(err));
    } finally {
      setSaving(false);
    }
  };

  if (phase.kind === "loading") {
    return (
      <main className="screen working-hours">
        <DelayedSkeleton loading>
          <ServiceCardSkeleton />
          <ServiceCardSkeleton />
        </DelayedSkeleton>
      </main>
    );
  }
  if (phase.kind === "error") {
    return (
      <main className="screen working-hours">
        <StateError err={phase.err} onRetry={() => void load()} />
      </main>
    );
  }

  const editingDay = editing === null ? null : week[editing];

  return (
    <main className="screen working-hours" aria-labelledby="working-hours-title">
      <h1 id="working-hours-title" className="working-hours__title">
        {TITLE}
      </h1>
      <p className="working-hours__note">{SEMANTIC_NOTE}</p>
      {phase.timezone && (
        <p className="working-hours__tz" data-testid="working-hours-tz">
          Часовой пояс: {phase.timezone}
        </p>
      )}

      {/* A — дни чекбоксами, без предвыбора */}
      <section aria-labelledby="working-hours-days">
        <h2 id="working-hours-days" className="working-hours__section-title">
          {PICK_DAYS_LABEL}
        </h2>
        <div className="chip-row" role="group" aria-label={PICK_DAYS_LABEL}>
          {week.map((day) => (
            <button
              key={day.day_of_week}
              type="button"
              role="checkbox"
              aria-checked={day.is_working_day}
              aria-label={DAY_NAMES[day.day_of_week]}
              className={`chip${day.is_working_day ? " chip--active" : ""}`}
              onClick={() => toggleDay(day.day_of_week)}
            >
              {DAY_LABELS[day.day_of_week]}
            </button>
          ))}
        </div>
      </section>

      {/* B — одно время на все выбранные дни */}
      <section aria-labelledby="working-hours-bulk" className="working-hours__bulk">
        <h2 id="working-hours-bulk" className="working-hours__section-title">
          Время работы
        </h2>
        <div className="working-hours__times">
          <label className="working-hours__time">
            <span>С</span>
            <input
              type="time"
              value={bulkStart}
              onChange={(e) => setBulkStart(e.target.value)}
              aria-label="Начало"
              className="working-hours__input"
            />
          </label>
          <label className="working-hours__time">
            <span>До</span>
            <input
              type="time"
              value={bulkEnd}
              onChange={(e) => setBulkEnd(e.target.value)}
              aria-label="Конец"
              className="working-hours__input"
            />
          </label>
        </div>
        <button
          type="button"
          className="btn-secondary"
          disabled={!bulkStart || !bulkEnd || selectedCount === 0}
          onClick={applyToSelected}
        >
          {APPLY_ALL_LABEL}
        </button>
      </section>

      {/* D — неделя целиком; тап по дню → C */}
      <section aria-labelledby="working-hours-week">
        <h2 id="working-hours-week" className="working-hours__section-title">
          Неделя
        </h2>
        {selectedCount === 0 && <p className="working-hours__empty">{NO_DAYS_YET}</p>}
        <ul className="working-hours__list" aria-label="Неделя">
          {week.map((day) => {
            const err = dayError(day);
            return (
              <li key={day.day_of_week} className="working-hours__item">
                <button
                  type="button"
                  className="working-hours__row"
                  data-testid={`day-${day.day_of_week}`}
                  onClick={(e) => {
                    editTriggerRef.current = e.currentTarget;
                    setEditing(day.day_of_week);
                  }}
                >
                  <span className="working-hours__day">{DAY_NAMES[day.day_of_week]}</span>
                  <span className={`working-hours__summary${err ? " working-hours__summary--error" : ""}`}>
                    {err ?? summary(day)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {saveError && (
        <p className="working-hours__error" role="alert">
          {saveError}
        </p>
      )}

      <div className="working-hours__actions">
        <button type="button" className="btn-primary" disabled={!canSave} onClick={() => void save("stay")}>
          {SAVE_LABEL}
        </button>
        <button
          type="button"
          className="btn-secondary"
          disabled={!canSave}
          onClick={() => void save("later")}
        >
          {SAVE_LATER_LABEL}
        </button>
      </div>

      {/* C — редактор дня */}
      {editingDay && (
        <DayEditor
          day={editingDay}
          triggerRef={editTriggerRef}
          onChange={(next) =>
            setWeek((prev) => prev.map((d) => (d.day_of_week === next.day_of_week ? next : d)))
          }
          onClose={() => setEditing(null)}
        />
      )}

      <Snackbar
        visible={snack !== null}
        message={snack ?? ""}
        durationMs={4000}
        onTimeout={() => setSnack(null)}
      />
    </main>
  );
}

function saveErrorText(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 409) return CONFLICT_MESSAGE;
    if (err.status === 403) return NOT_LINKED_MESSAGE;
    if (err.status === 400) return err.detail || INVALID_INTERVAL;
  }
  return "Не удалось сохранить. Попробуйте ещё раз.";
}

function DayEditor({
  day,
  triggerRef,
  onChange,
  onClose,
}: {
  day: WorkingHoursDay;
  triggerRef: React.RefObject<HTMLElement | null>;
  onChange: (next: WorkingHoursDay) => void;
  onClose: () => void;
}) {
  const set = (patch: Partial<WorkingHoursDay>) => onChange({ ...day, ...patch });
  const err = dayError(day);
  return (
    <SheetChrome
      headlineId="working-hours-day-headline"
      headline={DAY_NAMES[day.day_of_week] ?? ""}
      closeDisabled={false}
      triggerRef={triggerRef as React.RefObject<HTMLElement>}
      onClose={onClose}
    >
      <div className="working-hours__switch-row">
        <span id="working-hours-day-switch-label">{WORKING_DAY_SWITCH}</span>
        <ToggleSwitch
          checked={day.is_working_day}
          ariaLabel={WORKING_DAY_SWITCH}
          onChange={(next) =>
            set(
              next
                ? { is_working_day: true }
                : {
                    is_working_day: false,
                    start_time: null,
                    end_time: null,
                    break_start: null,
                    break_end: null,
                  },
            )
          }
        />
      </div>
      {day.is_working_day && (
        <>
          <div className="working-hours__times">
            <label className="working-hours__time">
              <span>С</span>
              <input
                type="time"
                value={day.start_time ?? ""}
                aria-label="Начало дня"
                className="working-hours__input"
                onChange={(e) => set({ start_time: e.target.value || null })}
              />
            </label>
            <label className="working-hours__time">
              <span>До</span>
              <input
                type="time"
                value={day.end_time ?? ""}
                aria-label="Конец дня"
                className="working-hours__input"
                onChange={(e) => set({ end_time: e.target.value || null })}
              />
            </label>
          </div>
          <div className="working-hours__times">
            <label className="working-hours__time">
              <span>Перерыв с</span>
              <input
                type="time"
                value={day.break_start ?? ""}
                aria-label="Перерыв с"
                className="working-hours__input"
                onChange={(e) => set({ break_start: e.target.value || null })}
              />
            </label>
            <label className="working-hours__time">
              <span>до</span>
              <input
                type="time"
                value={day.break_end ?? ""}
                aria-label="Перерыв до"
                className="working-hours__input"
                onChange={(e) => set({ break_end: e.target.value || null })}
              />
            </label>
          </div>
          {err && (
            <p className="working-hours__error" role="alert">
              {err}
            </p>
          )}
        </>
      )}
      <div className="profile-support-sheet__actions">
        <button type="button" className="btn-primary" onClick={onClose}>
          Готово
        </button>
      </div>
    </SheetChrome>
  );
}
