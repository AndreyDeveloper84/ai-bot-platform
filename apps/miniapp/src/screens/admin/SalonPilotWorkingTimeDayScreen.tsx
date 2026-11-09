/**
 * «Рабочее время» — один день мастера (DRF-2936, макет DRF-2865, кадры 3–9).
 *
 * Адрес `/admin/working-time/:masterId/:date`. Правится ДАТА, неделя не
 * меняется (freeze MUST FIX 3, подпись «Изменение только на эту дату»):
 *
 * * рабочий день и его часы — изменение на дату (`PUT date-exceptions`), а
 *   возврат к шаблону — снятие изменения (`DELETE`), если оно ничего, кроме
 *   часов, не несёт;
 * * недоступность внутри дня — `POST/DELETE time-off`; правка строки =
 *   новая строка и снятие старой (PATCH нет).
 *
 * # Черновик — намерения, а не состояние
 *
 * Черновик помнит, ЧТО человек сделал: тронул ли часы, что добавил, какие
 * строки снял — и на каком снимке даты (`basis`). Снимается только снятое
 * им самим: чужая недоступность, появившаяся после открытия, правкой не
 * задевается. Черновик живёт на устройстве до часа с последней правки и
 * переживает переоткрытие Mini App (запись принимается ~10 минут от
 * открытия).
 *
 * # Как сохраняется
 *
 * 1. Окно записи закрылось — экран просит открыть заново, черновик остаётся.
 * 2. Живое состояние даты перечитывается. Снимок разошёлся с тем, на котором
 *    правили, — «Расписание могло измениться»: намерения переносятся на
 *    живое, человек видит новое и жмёт ещё раз.
 * 3. Предпросмотр записей для каждого окна, которое правка ОТНИМАЕТ. Есть
 *    записи — кадр 7, ничего не записано. Не прочитан — отказ.
 * 4. Шаги — сначала закрывающие время, потом открывающие; на первом сбое —
 *    стоп и перечитывание. Успех (кадр 8) — только когда перечитанное
 *    исполнило намерения; ответ потерян — кадр 9 («Проверяем результат»).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import {
  RECHECK_MIN_INTERVAL_MS,
  SYSTEM_STATE_COPY,
  SystemState,
} from "../../components/OperationalSystemState";
import { SheetChrome } from "../../components/PersonalDataSheets";
import { ToggleSwitch } from "../../components/ToggleSwitch";
import { useOnline } from "../../hooks/useOnline";
import { useScreenBack } from "../../hooks/useScreenBack";
import {
  createTimeOff,
  deleteDateException,
  deleteTimeOff,
  getMasterDaySchedule,
  getMasterDetail,
  getMasterExceptions,
  getMasterSchedule,
  getScheduleImpact,
  putDateException,
  type MasterDayBooking,
  type MasterDetail,
  type MasterExceptions,
  type MasterSchedule,
} from "../../lib/admin-api";
import { appStartedAtMs } from "../../lib/app-clock";
import { signalReady } from "../../lib/max-sdk";
import {
  WT_COPY,
  classifyWriteError,
  clearDraft,
  closingWindows,
  closureOn,
  conflictRows,
  dayMonth,
  dayTitle,
  draftApplied,
  durationLabel,
  fallbackInterval,
  freshDraft,
  hasIntent,
  isYmd,
  listsUnreadable,
  loadDraft,
  rebaseDraft,
  salonToday,
  saveDraft,
  snapshotKey,
  snapshotLayer,
  snapshotOf,
  snapshotsDiffer,
  stepsFor,
  timeOptions,
  toMinutes,
  visibleTimeOff,
  weekStart,
  workingTimeDayPath,
  workingTimeWeekPath,
  writeWindowClosed,
  type ConflictRow,
  type DayDraft,
  type DaySnapshot,
  type Step,
  type VisibleTimeOff,
} from "../../lib/salon-working-time";
import { backByAction } from "../../lib/screen-back";
import {
  IconCalendar,
  IconCheck,
  IconChevron,
  IconHourglass,
  IconInfo,
  IconPencil,
  IconPlus,
  IconTrash,
  IconUpDown,
  IconWarning,
  WtAvatar,
  WtHeader,
  WtLoadError,
  WtStateCard,
} from "./WorkingTimeParts";

interface Ready {
  detail: MasterDetail;
  schedule: MasterSchedule;
  exceptions: MasterExceptions;
  bookings: MasterDayBooking[];
  today: string;
  snap: DaySnapshot;
}

type Phase = { kind: "loading" } | { kind: "ready"; data: Ready } | { kind: "error"; err: unknown };

type Mode =
  | { kind: "edit" }
  | { kind: "saving" }
  | { kind: "conflict"; rows: ConflictRow[] }
  | { kind: "success"; snap: DaySnapshot }
  | { kind: "pending" }
  | { kind: "partial" }
  | { kind: "reopen" }
  | { kind: "forbidden" };

interface SheetState {
  /** Ключ правимой строки на экране; `null` — новая. */
  key: string | null;
  /** Сохранённая строка — её номер; новая или ещё не сохранённая — `null`. */
  id: string | null;
  from: string;
  to: string;
  reason: string;
  error: string | null;
}

/** Через сколько после входа в «Проверяем результат» проверять самим (кадр 9). */
const AUTO_RECHECK_MS = 1500;

async function readDay(masterId: string, ymd: string, signal?: AbortSignal) {
  // Сутки по краям: отгул у полуночи салона каталог отдаёт по датам в СВОЁМ
  // поясе и иначе мог бы выпасть из ответа.
  const [schedule, exceptions] = await Promise.all([
    getMasterSchedule(masterId, { signal }),
    getMasterExceptions(masterId, { from: addDaysSafe(ymd, -1), to: addDaysSafe(ymd, 1) }, { signal }),
  ]);
  return { schedule, exceptions, snap: snapshotOf(ymd, schedule, exceptions) };
}

function addDaysSafe(ymd: string, days: number): string {
  const [y, m, d] = ymd.split("-").map(Number);
  const t = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1) + days * 86_400_000);
  return `${t.getUTCFullYear()}-${`${t.getUTCMonth() + 1}`.padStart(2, "0")}-${`${t.getUTCDate()}`.padStart(2, "0")}`;
}

async function readBookings(masterId: string, ymd: string, signal?: AbortSignal) {
  const frame = await getMasterDaySchedule(masterId, { from: ymd, to: ymd }, { signal });
  return { bookings: frame.days.find((d) => d.date === ymd)?.bookings ?? [], tz: frame.tenant_tz };
}

function newKey(): string {
  return `new-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function SalonPilotWorkingTimeDayScreen() {
  const { masterId = "", date = "" } = useParams<{ masterId: string; date: string }>();
  const navigate = useNavigate();
  const online = useOnline();
  const ymd = isYmd(date) ? date : "";
  const weekHref = ymd
    ? `${workingTimeWeekPath(masterId)}?week=${weekStart(ymd)}`
    : workingTimeWeekPath(masterId);

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [reload, setReload] = useState(0);
  const [mode, setMode] = useState<Mode>({ kind: "edit" });
  const [draft, setDraft] = useState<DayDraft | null>(null);
  const [restored, setRestored] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [sheet, setSheet] = useState<SheetState | null>(null);
  const [checking, setChecking] = useState(false);
  /** «Проверить снова» — не чаще раза в 10 с (общий троттл DRF-2938). */
  const [cooldown, setCooldown] = useState(false);
  const cooldownTimer = useRef<number | null>(null);
  /** Снимок даты перед записью: по нему видно, легло ли хоть что-то. */
  const beforeWrite = useRef<DaySnapshot | null>(null);
  const addRef = useRef<HTMLButtonElement | null>(null);
  const ready = useRef(false);

  useEffect(
    () => () => {
      if (cooldownTimer.current !== null) window.clearTimeout(cooldownTimer.current);
    },
    [],
  );

  // Такой даты нет — неделя мастера, а не вечная ошибка загрузки.
  useEffect(() => {
    if (!ymd) navigate(workingTimeWeekPath(masterId), { replace: true });
  }, [ymd, masterId, navigate]);

  /** Правка человека: черновик и его копия на устройстве (срок — от неё). */
  const editDraft = useCallback(
    (next: DayDraft) => {
      setDraft(next);
      if (hasIntent(next)) saveDraft(masterId, ymd, next);
      else clearDraft(masterId, ymd);
    },
    [masterId, ymd],
  );

  const toWeek = useCallback(
    (focus?: string) => navigate(weekHref, focus ? { state: { focus } } : undefined),
    [navigate, weekHref],
  );

  const leave = useCallback(() => {
    clearDraft(masterId, ymd);
    toWeek();
  }, [masterId, ymd, toWeek]);

  const onBack = useCallback(() => {
    // Посреди записи не уходим: сбой шага без экрана никто бы не увидел.
    if (mode.kind === "saving") return;
    if (sheet) {
      setSheet(null);
      return;
    }
    if (mode.kind === "conflict" || mode.kind === "forbidden") {
      setMode({ kind: "edit" });
      return;
    }
    // Исход не полный или неизвестен — черновик остаётся: вернувшись, человек
    // увидит, что легло, и сохранит оставшееся.
    if (mode.kind !== "edit") {
      toWeek();
      return;
    }
    leave();
  }, [sheet, mode.kind, toWeek, leave]);
  const back = useScreenBack(backByAction(onBack));

  useEffect(() => {
    if (!ymd) return;
    const controller = new AbortController();
    const signal = controller.signal;
    setPhase({ kind: "loading" });
    void (async () => {
      try {
        const [detail, day, book] = await Promise.all([
          getMasterDetail(masterId, { signal }),
          readDay(masterId, ymd, signal),
          readBookings(masterId, ymd, signal),
        ]);
        if (signal.aborted) return;
        let next = freshDraft(day.snap, fallbackInterval(day.schedule));
        let wasRestored = false;
        let wasStale = false;
        const stored = loadDraft(masterId, ymd);
        if (stored) {
          // Черновик сделан на другом снимке — переносим намерения на живое и
          // говорим об этом; исполненный или пустой — выбрасываем.
          const rebased = rebaseDraft(day.snap, stored);
          if (hasIntent(rebased)) {
            next = rebased;
            wasRestored = true;
            wasStale = stored.basis !== snapshotKey(day.snap);
          } else {
            clearDraft(masterId, ymd);
          }
        }
        setDraft(next);
        setRestored(wasRestored);
        setStale(wasStale);
        setPhase({
          kind: "ready",
          data: {
            detail,
            schedule: day.schedule,
            exceptions: day.exceptions,
            bookings: book.bookings,
            today: salonToday(book.tz),
            snap: day.snap,
          },
        });
      } catch (err) {
        if (signal.aborted) return;
        setPhase({ kind: "error", err });
      } finally {
        if (!ready.current && !signal.aborted) {
          ready.current = true;
          signalReady();
        }
      }
    })();
    return () => controller.abort();
  }, [masterId, ymd, reload]);

  const data = phase.kind === "ready" ? phase.data : null;
  const closure = data ? closureOn(data.exceptions.closures.rows, ymd) : null;
  const unreadable = data ? listsUnreadable(data.exceptions) : false;
  const past = data ? ymd < data.today : false;
  const readOnly = !data || past || !data.exceptions.writable || unreadable;
  const visible: VisibleTimeOff[] = data && draft ? visibleTimeOff(data.snap, draft) : [];

  const replaceData = useCallback(
    (next: { schedule: MasterSchedule; exceptions: MasterExceptions; snap: DaySnapshot }) => {
      setPhase((prev) =>
        prev.kind === "ready" ? { kind: "ready", data: { ...prev.data, ...next } } : prev,
      );
    },
    [],
  );

  /**
   * Перечитать и решить исход: намерения исполнены — успех; ничего не легло
   * (снимок как до записи) — правка цела, можно повторить; легла часть —
   * «Сохранено не всё», и черновик переносится на живое.
   */
  const settle = useCallback(async () => {
    if (!draft) return;
    setChecking(true);
    try {
      const after = await readDay(masterId, ymd);
      replaceData(after);
      if (draftApplied(after.snap, draft)) {
        clearDraft(masterId, ymd);
        setDraft(freshDraft(after.snap, fallbackInterval(after.schedule)));
        setRestored(false);
        setStale(false);
        setMode({ kind: "success", snap: after.snap });
        return;
      }
      editDraft(rebaseDraft(after.snap, draft));
      const before = beforeWrite.current;
      if (before && !snapshotsDiffer(before, after.snap)) {
        setMessage(WT_COPY.day.saveFailed);
        setMode({ kind: "edit" });
      } else {
        setMode({ kind: "partial" });
      }
    } catch {
      setMode({ kind: "pending" });
    } finally {
      setChecking(false);
      setCooldown(true);
      if (cooldownTimer.current !== null) window.clearTimeout(cooldownTimer.current);
      cooldownTimer.current = window.setTimeout(() => setCooldown(false), RECHECK_MIN_INTERVAL_MS);
    }
  }, [draft, masterId, ymd, replaceData, editDraft]);

  // Кадр 9 обещает проверить самим: один раз — сразу после входа. Черновик в
  // «проверяем» не меняется, поэтому `settle` здесь стабилен.
  const pending = mode.kind === "pending";
  useEffect(() => {
    if (!pending) return;
    const timer = window.setTimeout(() => void settle(), AUTO_RECHECK_MS);
    return () => window.clearTimeout(timer);
  }, [pending, settle]);

  /** Шаг записи; у новой недоступности — номер строки, которую сервер вернул. */
  const runStep = useCallback(
    async (step: Step): Promise<string | null> => {
      switch (step.kind) {
        case "time_off_create": {
          const res = await createTimeOff(masterId, {
            date: ymd,
            from: step.item.from,
            to: step.item.to,
            reason: step.item.reason.trim(),
          });
          return res.row.id || null;
        }
        case "time_off_delete":
          await deleteTimeOff(masterId, step.item.id);
          return null;
        case "date_put":
          await putDateException(
            masterId,
            step.layer.working
              ? {
                  date: ymd,
                  is_working_day: true,
                  start_time: step.layer.start ?? undefined,
                  end_time: step.layer.end ?? undefined,
                }
              : { date: ymd, is_working_day: false },
          );
          return null;
        case "date_delete":
          await deleteDateException(masterId, ymd);
          return null;
      }
    },
    [masterId, ymd],
  );

  const save = useCallback(async () => {
    if (!draft || !data) return;
    setMessage(null);
    if (
      draft.hoursTouched &&
      draft.working &&
      (!draft.start || !draft.end || toMinutes(draft.start) >= toMinutes(draft.end))
    ) {
      setMessage(WT_COPY.day.invalidInterval);
      return;
    }
    if (!data.exceptions.writable) {
      setMode({ kind: "forbidden" });
      return;
    }
    if (writeWindowClosed(appStartedAtMs())) {
      saveDraft(masterId, ymd, draft);
      setMode({ kind: "reopen" });
      return;
    }
    setMode({ kind: "saving" });
    try {
      const live = await readDay(masterId, ymd);
      if (snapshotKey(live.snap) !== draft.basis) {
        // Дату меняли, пока правили: показать новое, а не писать вслепую.
        replaceData(live);
        editDraft(rebaseDraft(live.snap, draft));
        setStale(true);
        setMode({ kind: "edit" });
        return;
      }
      setStale(false);

      const steps = stepsFor(live.snap, draft);
      if (steps.length === 0) {
        setMessage(WT_COPY.day.nothingToSave);
        setMode({ kind: "edit" });
        return;
      }

      const windows = closingWindows(live.snap, draft);
      if (windows.length > 0) {
        let rows: ConflictRow[] | null;
        try {
          const [impacts, book] = await Promise.all([
            Promise.all(
              windows.map((w) => getScheduleImpact(masterId, { date: ymd, from: w.from, to: w.to })),
            ),
            readBookings(masterId, ymd),
          ]);
          rows = conflictRows(impacts, book.bookings);
        } catch {
          rows = null;
        }
        if (rows === null) {
          setMessage(WT_COPY.day.impactFailed);
          setMode({ kind: "edit" });
          return;
        }
        if (rows.length > 0) {
          setMode({ kind: "conflict", rows });
          return;
        }
      }

      beforeWrite.current = live.snap;
      // Номера строк, которые сервер вернул на «добавить» (и «такая уже есть»):
      // их не снимаем в этом же прогоне.
      const created = new Set<string>();
      let done = 0;
      for (const step of steps) {
        if (step.kind === "time_off_delete" && created.has(step.item.id)) continue;
        try {
          const id = await runStep(step);
          if (id) created.add(id);
          done += 1;
        } catch (err) {
          const failure = classifyWriteError(err, step.kind);
          if (failure.kind === "gone") {
            done += 1;
            continue;
          }
          if (failure.kind === "stale") {
            saveDraft(masterId, ymd, draft);
            setMode({ kind: "reopen" });
            return;
          }
          if (failure.kind === "forbidden") {
            setMode({ kind: "forbidden" });
            return;
          }
          if (failure.kind === "unknown") {
            setMode({ kind: "pending" });
            return;
          }
          if (failure.kind === "break_crosses") {
            setMessage(WT_COPY.day.breakCrosses(failure.from, failure.to));
          }
          if (done === 0) {
            if (failure.kind === "conflict") {
              // Предпросмотр сказал «записей нет», а запись отказала: гонка
              // или другой пояс. Ничего не записано — кадр 7 без строк.
              setMode({ kind: "conflict", rows: [] });
              return;
            }
            if (failure.kind !== "break_crosses") setMessage(WT_COPY.day.saveFailed);
            setMode({ kind: "edit" });
            return;
          }
          // Что-то уже легло: только перечитывание скажет, что именно.
          await settle();
          return;
        }
      }
      await settle();
    } catch {
      setMessage(WT_COPY.day.saveFailed);
      setMode({ kind: "edit" });
    }
  }, [draft, data, masterId, ymd, replaceData, editDraft, runStep, settle]);

  const openSheet = (row: VisibleTimeOff | null) => {
    if (!draft) return;
    setSheet(
      row
        ? { key: row.key, id: row.id, from: row.from, to: row.to, reason: row.reason, error: null }
        : { key: null, id: null, from: draft.start || "", to: draft.end || "", reason: "", error: null },
    );
  };

  const applySheet = () => {
    if (!sheet || !draft) return;
    if (!sheet.from || !sheet.to || toMinutes(sheet.from) >= toMinutes(sheet.to)) {
      setSheet({ ...sheet, error: WT_COPY.day.invalidInterval });
      return;
    }
    if (!sheet.reason.trim()) {
      setSheet({ ...sheet, error: WT_COPY.unavailable.reasonRequired });
      return;
    }
    const entry = { from: sheet.from, to: sheet.to, reason: sheet.reason.trim() };
    let next: DayDraft;
    if (sheet.key === null) {
      next = { ...draft, added: [...draft.added, { key: newKey(), ...entry }] };
    } else if (sheet.id === null) {
      next = { ...draft, added: draft.added.map((a) => (a.key === sheet.key ? { ...a, ...entry } : a)) };
    } else {
      const old = visible.find((v) => v.id === sheet.id);
      if (old && old.from === entry.from && old.to === entry.to && old.reason.trim() === entry.reason) {
        setSheet(null);
        return;
      }
      next = {
        ...draft,
        removedIds: [...draft.removedIds, sheet.id],
        added: [...draft.added, { key: newKey(), ...entry }],
      };
    }
    editDraft(next);
    setSheet(null);
  };

  const removeRow = (row: VisibleTimeOff) => {
    if (!draft) return;
    editDraft(
      row.id === null
        ? { ...draft, added: draft.added.filter((a) => a.key !== row.key) }
        : { ...draft, removedIds: [...draft.removedIds, row.id] },
    );
  };

  const setHours = (patch: Partial<Pick<DayDraft, "working" | "start" | "end">>) => {
    if (!draft) return;
    setMessage(null);
    editDraft({ ...draft, ...patch, hoursTouched: true });
  };

  const resetDraft = () => {
    if (!data) return;
    clearDraft(masterId, ymd);
    setDraft(freshDraft(data.snap, fallbackInterval(data.schedule)));
    setRestored(false);
    setStale(false);
    setMessage(null);
  };

  const title = ymd ? dayTitle(ymd) : WT_COPY.title;
  const master = data?.detail ?? null;
  const saving = mode.kind === "saving";

  return (
    <div className="screen wt-screen wt-screen--nested">
      <WtHeader title={title} subtitle={master?.name ?? null} onBack={back} />
      <div className="wt-body">
        {!online ? <SystemState kind="offline" /> : null}
        {phase.kind === "loading" ? <SystemState kind="loading" lines={4} /> : null}
        {phase.kind === "error" ? (
          <WtLoadError err={phase.err} onRetry={() => setReload((n) => n + 1)} />
        ) : null}

        {data && draft && mode.kind === "conflict" ? (
          <ConflictView
            rows={mode.rows}
            onOpen={(id) => {
              saveDraft(masterId, ymd, draft);
              navigate(`/admin/bookings/${encodeURIComponent(id)}`, {
                state: { from: workingTimeDayPath(masterId, ymd) },
              });
            }}
            onCancel={() => setMode({ kind: "edit" })}
          />
        ) : null}

        {mode.kind === "success" ? (
          <SuccessView
            snap={mode.snap}
            onHours={() => setMode({ kind: "edit" })}
            onWeek={() => toWeek()}
            onAnother={() => toWeek(addDaysSafe(ymd, 1))}
          />
        ) : null}

        {mode.kind === "pending" ? (
          <WtStateCard
            tone="accent"
            icon={<IconHourglass />}
            title={SYSTEM_STATE_COPY.pending.title}
            body={SYSTEM_STATE_COPY.pending.body}
          >
            <p className="wt-info">
              <span className="wt-info__icon" aria-hidden="true">
                <IconInfo />
              </span>
              {WT_COPY.pending.note}
            </p>
            <button
              type="button"
              className="btn-primary wt-btn"
              disabled={checking || cooldown}
              onClick={() => void settle()}
            >
              {SYSTEM_STATE_COPY.pending.recheck}
            </button>
            <button type="button" className="btn-secondary wt-btn" onClick={() => toWeek()}>
              {WT_COPY.pending.leave}
            </button>
          </WtStateCard>
        ) : null}

        {mode.kind === "partial" ? (
          <WtStateCard
            tone="warning"
            role="alert"
            icon={<IconWarning />}
            title={WT_COPY.partial.title}
            body={WT_COPY.partial.body}
          >
            {message ? <p className="wt-message">{message}</p> : null}
            <button type="button" className="btn-primary wt-btn" onClick={() => void save()}>
              {WT_COPY.partial.retry}
            </button>
            <button type="button" className="btn-secondary wt-btn" onClick={() => toWeek()}>
              {WT_COPY.partial.toWeek}
            </button>
          </WtStateCard>
        ) : null}

        {mode.kind === "reopen" ? (
          <WtStateCard
            tone="warning"
            role="alert"
            icon={<IconWarning />}
            title={WT_COPY.reopen.title}
            body={WT_COPY.reopen.body}
          />
        ) : null}

        {mode.kind === "forbidden" ? (
          <>
            <SystemState kind="forbidden" />
            <button
              type="button"
              className="btn-secondary wt-btn"
              onClick={() => setMode({ kind: "edit" })}
            >
              {WT_COPY.day.cancel}
            </button>
          </>
        ) : null}

        {data && draft && (mode.kind === "edit" || saving) ? (
          <section className="wt-editor" aria-label={title}>
            {stale ? <SystemState kind="stale" /> : null}
            {restored ? (
              <p className="wt-note">
                {WT_COPY.day.restored}{" "}
                <button type="button" className="wt-link" onClick={resetDraft}>
                  {WT_COPY.day.reset}
                </button>
              </p>
            ) : null}
            {unreadable ? <p className="wt-note wt-note--warn">{WT_COPY.day.unreadable}</p> : null}
            {closure ? (
              <p className="wt-note wt-note--warn">
                {closure.start && closure.end
                  ? `${WT_COPY.day.closed}: ${closure.start}–${closure.end}`
                  : WT_COPY.day.closed}
              </p>
            ) : null}
            {past ? <p className="wt-note">{WT_COPY.day.past}</p> : null}
            {!data.exceptions.writable && !past ? <SystemState kind="forbidden" /> : null}

            <div className="wt-card wt-row">
              <span className="wt-row__label">{WT_COPY.day.working}</span>
              <ToggleSwitch
                checked={draft.working}
                ariaLabel={WT_COPY.day.working}
                disabled={readOnly || saving}
                onChange={(working) => setHours({ working })}
              />
            </div>
            <p className="wt-scope">{WT_COPY.day.scope}</p>

            {draft.working ? (
              <>
                <section className="wt-section" aria-labelledby="wt-hours-title">
                  <h2 className="wt-section__title" id="wt-hours-title">
                    {WT_COPY.day.hours}
                  </h2>
                  <TimeField
                    id="wt-start"
                    label={WT_COPY.day.start}
                    value={draft.start}
                    disabled={readOnly || saving}
                    onChange={(start) => setHours({ start })}
                  />
                  <TimeField
                    id="wt-end"
                    label={WT_COPY.day.end}
                    value={draft.end}
                    disabled={readOnly || saving}
                    onChange={(end) => setHours({ end })}
                  />
                  <div className="wt-row wt-row--plain">
                    <span className="wt-row__label wt-row__label--muted">{WT_COPY.day.duration}</span>
                    <span className="wt-row__value">{durationLabel(draft.start, draft.end) ?? "—"}</span>
                  </div>
                </section>

                <section className="wt-section" aria-labelledby="wt-off-title">
                  <h2 className="wt-section__title" id="wt-off-title">
                    {WT_COPY.day.unavailability}
                  </h2>
                  <ul className="wt-offs">
                    {visible.map((item) => (
                      <li key={item.key} className="wt-off">
                        <span className="wt-off__text">
                          <span className="wt-off__time">{`${item.from} – ${item.to}`}</span>
                          <span className="wt-off__reason">{item.reason}</span>
                        </span>
                        {readOnly ? null : (
                          <>
                            <button
                              type="button"
                              className="wt-icon-btn"
                              aria-label={`${WT_COPY.day.edit}: ${item.from} – ${item.to}`}
                              disabled={saving}
                              onClick={() => openSheet(item)}
                            >
                              <IconPencil />
                            </button>
                            <button
                              type="button"
                              className="wt-icon-btn wt-icon-btn--danger"
                              aria-label={`${WT_COPY.day.remove}: ${item.from} – ${item.to}`}
                              disabled={saving}
                              onClick={() => removeRow(item)}
                            >
                              <IconTrash />
                            </button>
                          </>
                        )}
                      </li>
                    ))}
                    {data.snap.timeOff
                      .filter((o) => !o.editable)
                      .map((item) => (
                        <li key={`ro-${item.id}`} className="wt-off wt-off--readonly">
                          <span className="wt-off__text">
                            <span className="wt-off__time">{`${item.from} – ${item.to}`}</span>
                            <span className="wt-off__reason">
                              {[
                                item.reason,
                                item.startYmd === item.endYmd
                                  ? null
                                  : `${dayMonth(item.startYmd)} – ${dayMonth(item.endYmd)}`,
                                WT_COPY.day.readOnly,
                              ]
                                .filter(Boolean)
                                .join(" · ")}
                            </span>
                          </span>
                        </li>
                      ))}
                  </ul>
                  {readOnly ? null : (
                    <button
                      type="button"
                      ref={addRef}
                      className="wt-add"
                      disabled={saving}
                      onClick={() => openSheet(null)}
                    >
                      <span className="wt-add__icon" aria-hidden="true">
                        <IconPlus />
                      </span>
                      {WT_COPY.day.addUnavailability}
                    </button>
                  )}
                </section>
              </>
            ) : (
              <WtStateCard
                tone="neutral"
                icon={<IconCalendar />}
                title={WT_COPY.day.dayOffTitle}
                body={WT_COPY.day.dayOffBody}
              />
            )}

            {message ? (
              <p className="wt-message" role="alert">
                {message}
              </p>
            ) : null}

            {readOnly ? null : (
              <div className="wt-actions">
                <button
                  type="button"
                  className="btn-primary wt-btn"
                  disabled={saving}
                  aria-busy={saving ? "true" : undefined}
                  onClick={() => void save()}
                >
                  {WT_COPY.day.save}
                </button>
                <button type="button" className="btn-secondary wt-btn" disabled={saving} onClick={leave}>
                  {WT_COPY.day.cancel}
                </button>
              </div>
            )}
          </section>
        ) : null}
      </div>

      {sheet ? (
        <SheetChrome
          headlineId="wt-unavailable-headline"
          headline={WT_COPY.unavailable.title}
          closeDisabled={false}
          triggerRef={addRef as React.RefObject<HTMLElement>}
          onClose={() => setSheet(null)}
        >
          <TimeField
            id="wt-off-from"
            label={WT_COPY.unavailable.from}
            value={sheet.from}
            onChange={(from) => setSheet({ ...sheet, from, error: null })}
          />
          <TimeField
            id="wt-off-to"
            label={WT_COPY.unavailable.to}
            value={sheet.to}
            allowEndOfDay
            onChange={(to) => setSheet({ ...sheet, to, error: null })}
          />
          <label className="wt-field" htmlFor="wt-off-reason">
            <span className="wt-field__label">{WT_COPY.unavailable.reason}</span>
            <input
              id="wt-off-reason"
              type="text"
              className="wt-input"
              maxLength={120}
              placeholder={WT_COPY.unavailable.reasonPlaceholder}
              value={sheet.reason}
              onChange={(e) => setSheet({ ...sheet, reason: e.target.value, error: null })}
            />
          </label>
          {sheet.error ? (
            <p className="wt-message" role="alert">
              {sheet.error}
            </p>
          ) : null}
          <button type="button" className="btn-primary wt-btn" onClick={applySheet}>
            {sheet.key === null ? WT_COPY.unavailable.add : WT_COPY.unavailable.save}
          </button>
        </SheetChrome>
      ) : null}
    </div>
  );
}

function TimeField({
  id,
  label,
  value,
  allowEndOfDay = false,
  disabled = false,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  allowEndOfDay?: boolean;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const options = useMemo(() => timeOptions(value, { allowEndOfDay }), [value, allowEndOfDay]);
  return (
    <label className="wt-row wt-row--field" htmlFor={id}>
      <span className="wt-row__label">{label}</span>
      <span className="wt-select">
        <select
          id={id}
          className="wt-select__input"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          {value === "" ? <option value="">—</option> : null}
          {options.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <span className="wt-select__icon" aria-hidden="true">
          <IconUpDown />
        </span>
      </span>
    </label>
  );
}

function ConflictView({
  rows,
  onOpen,
  onCancel,
}: {
  rows: ConflictRow[];
  onOpen: (appointmentId: string) => void;
  onCancel: () => void;
}) {
  const first = rows.find((r) => r.linkable) ?? null;
  return (
    <WtStateCard
      tone="warning"
      role="alert"
      icon={<IconWarning />}
      title={WT_COPY.conflict.title}
      body={WT_COPY.conflict.body}
    >
      {rows.length > 0 ? (
        <ul className="wt-conflicts" aria-label={WT_COPY.conflict.title}>
          {rows.map((row) => {
            const text = (
              <>
                <span className="wt-conflict__time">{`${row.from} – ${row.to}`}</span>
                <WtAvatar name={row.client ?? row.service ?? ""} photoUrl={null} size="sm" />
                <span className="wt-conflict__who">
                  {row.client ? <span className="wt-conflict__name">{row.client}</span> : null}
                  {row.service ? <span className="wt-conflict__service">{row.service}</span> : null}
                </span>
              </>
            );
            return (
              <li key={row.appointmentId}>
                {row.linkable ? (
                  <button type="button" className="wt-conflict" onClick={() => onOpen(row.appointmentId)}>
                    {text}
                    <span className="wt-conflict__chevron" aria-hidden="true">
                      <IconChevron />
                    </span>
                  </button>
                ) : (
                  <span className="wt-conflict">{text}</span>
                )}
              </li>
            );
          })}
        </ul>
      ) : null}
      {first ? (
        <button type="button" className="btn-primary wt-btn" onClick={() => onOpen(first.appointmentId)}>
          {WT_COPY.conflict.open}
        </button>
      ) : null}
      <button type="button" className="btn-secondary wt-btn" onClick={onCancel}>
        {WT_COPY.conflict.cancel}
      </button>
    </WtStateCard>
  );
}

function SuccessView({
  snap,
  onHours,
  onWeek,
  onAnother,
}: {
  /** Перечитанная дата — экран говорит то, что прочёл, а не то, что отправил. */
  snap: DaySnapshot;
  onHours: () => void;
  onWeek: () => void;
  onAnother: () => void;
}) {
  const layer = snapshotLayer(snap);
  const duration = layer.working && layer.start && layer.end ? durationLabel(layer.start, layer.end) : null;
  const offs = snap.timeOff;
  return (
    <WtStateCard
      tone="success"
      icon={<IconCheck />}
      title={WT_COPY.success.title}
      body={WT_COPY.success.body}
    >
      <div className="wt-summary">
        <button type="button" className="wt-summary__row" onClick={onHours}>
          <span className="wt-summary__text">
            <span className="wt-summary__label">{WT_COPY.success.hours}</span>
            <span className="wt-summary__value">
              {layer.working
                ? `${layer.start} – ${layer.end}${duration ? `   (${duration})` : ""}`
                : WT_COPY.success.dayOff}
            </span>
          </span>
          <span className="wt-summary__chevron" aria-hidden="true">
            <IconChevron />
          </span>
        </button>
        <div className="wt-summary__row wt-summary__row--static">
          <span className="wt-summary__text">
            <span className="wt-summary__label">{WT_COPY.success.unavailability}</span>
            <span className="wt-summary__value">
              {layer.working && offs.length > 0
                ? offs.map((o) => `${o.from} – ${o.to}`).join(", ")
                : WT_COPY.success.none}
            </span>
          </span>
        </div>
      </div>
      <button type="button" className="btn-primary wt-btn" onClick={onWeek}>
        {WT_COPY.success.toWeek}
      </button>
      <button type="button" className="btn-secondary wt-btn" onClick={onAnother}>
        {WT_COPY.success.anotherDay}
      </button>
    </WtStateCard>
  );
}
