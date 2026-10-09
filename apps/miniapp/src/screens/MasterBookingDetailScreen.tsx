/**
 * «Детали записи» мастера — макет DRF-1185 (DRF-2156, М-4 эпика DRF-2150).
 *
 * Один экран на все моменты жизни записи; меняется ТОЛЬКО контекстный блок,
 * и его состояние приходит с сервера (`temporal_state`, контракт М-2
 * DRF-2154). Экран ничего не считает по часам устройства: «Завершено» —
 * только когда так сказал сервер; «Сегодня/Завтра» — относительно
 * `checked_at` (часы сервера), а не `new Date()`.
 *
 * Постоянная часть (всегда): имя клиента с инициалом · «20 августа · среда» ·
 * «15:30–16:30» + «60 мин» · услуга + «60 мин» (длительность — всегда
 * минутами, ruling §61 М-6 ж). Контекстный блок, один из:
 *   upcoming  → «Следующая запись» · «Сегодня в 15:30» · «До визита 1 ч 20 мин»
 *   now       → «Сейчас по расписанию» · «15:30–16:30»
 *   after     → «Запись закончилась по расписанию» ·
 *                «Если всё прошло как запланировано, ничего делать не нужно.»
 *   completed → «Завершено»
 *   unknown   → «Проверяем результат» · «Не удалось получить актуальное
 *                состояние записи.» · [Проверить снова] — общий SystemState
 *                (DRF-2157); троттл 10 с и блок на время запроса — внутри него
 *
 * Отменённая (status cancelled/no_show) проверяется ДО temporal_state:
 * временного блока нет, одна строка «Запись отменена», без кнопок (решение
 * владельца §61; recovery-flow — отдельная задача).
 *
 * Не рисуем (DRF-1185 «Что не нужно добавлять», §61): «…»-меню, телефон,
 * оплату, историю клиента, заметки, «Начать визит», «Завершить», «Клиент не
 * пришёл», «В процессе», таймеры/прогресс. `last_visit_date` из контракта на
 * этот экран не выходит.
 *
 * Возврат — заданное место (DRF-1493): откуда пришли (`state.from` с
 * «Сегодня»/«Расписания»), иначе «Расписание» своей поверхности.
 *
 * Оговорка про часы: состояние и «Сегодня/Завтра» — по серверу; но дата,
 * день недели и HH:MM выводятся в часовом поясе УСТРОЙСТВА (как formatTimeHM
 * на «Сегодня» и в «Расписании»). Мастер с телефоном в другом поясе увидит
 * сдвиг — общий для всех мастерских экранов, здесь не решается.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import { DestructiveConfirmation, SystemState } from "../components/master/SystemState";
import { MasterTabBar } from "../components/MasterTabBar";
import { useScreenBack } from "../hooks/useScreenBack";
import {
  actOnMasterBooking,
  getMasterBooking,
  getMasterBookingSlots,
  type BookingTemporalState,
  type MasterBookingDetail,
} from "../lib/master-api";
import {
  formatDateDotWeekdayRu,
  formatDurationRu,
  formatTimeHM,
  formatUpcomingAtRu,
} from "../lib/masterDateFormat";
import { signalReady } from "../lib/max-sdk";
import { backTo } from "../lib/screen-back";

const COPY = {
  // Тексты макета DRF-1185 — дословно.
  stateRegion: "Состояние записи",
  upcoming: {
    label: "Следующая запись",
    // То же правило, что на «Сегодня»: 0 или нет — «Уже сейчас», не «0 мин».
    until: (min: number | null) =>
      min !== null && min > 0 ? `До визита ${formatDurationRu(min)}` : "Уже сейчас",
  },
  now: "Сейчас по расписанию",
  after: {
    title: "Запись закончилась по расписанию",
    body: "Если всё прошло как запланировано, ничего делать не нужно.",
  },
  completed: "Завершено",
  // unknown: заголовок и кнопка — словарь SystemState (DRF-1181 п.10); тело — DRF-1185.
  unknownBody: "Не удалось получить актуальное состояние записи.",
  // Вне макета — решение владельца §61.
  cancelled: "Запись отменена",
  range: (start: string, end: string) => `${start}–${end}`,
};

type Phase =
  | { kind: "loading" }
  | { kind: "ready"; data: MasterBookingDetail }
  | { kind: "error"; err: unknown };

const CANCELLED_STATUSES: ReadonlySet<string> = new Set(["cancelled", "no_show"]);

function isCancelled(d: MasterBookingDetail): boolean {
  return CANCELLED_STATUSES.has(d.status);
}

/** `state.from` — только адрес своей поверхности; чужое или мусор → нет. */
function backAddressFrom(state: unknown, fallback: string): string {
  if (state && typeof state === "object" && "from" in state) {
    const from = (state as { from?: unknown }).from;
    if (typeof from === "string" && (from.startsWith("/master/") || from.startsWith("/solo/"))) {
      return from;
    }
  }
  return fallback;
}

export function MasterBookingDetailScreen() {
  const { id = "" } = useParams<{ id: string }>();
  const location = useLocation();
  const isSolo = location.pathname.startsWith("/solo/");
  const scheduleRoot = isSolo ? "/solo/schedule" : "/master/schedule";
  useScreenBack(backTo(backAddressFrom(location.state, scheduleRoot)));

  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [busy, setBusy] = useState(false);
  const alive = useRef(true);
  const inflight = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
      inflight.current?.abort();
      const ctrl = new AbortController();
      inflight.current = ctrl;
      setBusy(true);
      try {
        // Всегда — свежий ответ сервера, никакого локального «оптимизма» (§61 п.3).
        const data = await getMasterBooking(id, { signal: ctrl.signal });
        if (!alive.current || ctrl.signal.aborted) return;
        setPhase({ kind: "ready", data });
      } catch (err) {
        if (!alive.current || ctrl.signal.aborted) return;
        setPhase({ kind: "error", err });
      } finally {
        if (alive.current && !ctrl.signal.aborted) setBusy(false);
      }
  }, [id]);

  useEffect(() => {
    alive.current = true;
    signalReady();
    // Смена :id без размонтирования (назад/вперёд между двумя записями):
    // чужие данные новой записи не принадлежат; блок unknown размонтируется —
    // с ним и остаток троттла «Проверить снова».
    setPhase({ kind: "loading" });
    setBusy(false);
    void load();
    return () => {
      alive.current = false;
      inflight.current?.abort();
    };
  }, [load]);

  // Всегда — свежий ответ сервера (§61 п.3). Троттл «Проверить снова» при
  // unknown — внутри SystemState; повтор после сбоя — без троттла.
  const reload = useCallback(() => {
    if (busy) return;
    void load();
  }, [busy, load]);

  return (
    <div className="master-dashboard">
      {phase.kind === "loading" ? (
        <SystemState kind="loading" />
      ) : phase.kind === "error" ? (
        <SystemState kind="load_error" what="booking" err={phase.err} busy={busy} onRetry={reload} />
      ) : (
        <DetailBody data={phase.data} busy={busy} onRecheck={reload} onReadback={load} />
      )}
      <MasterTabBar scheduleHasPendingChange={false} />
    </div>
  );
}

// ----------------------------------------------------------------------------
// Body
// ----------------------------------------------------------------------------

function DetailBody({
  data,
  busy,
  onRecheck,
  onReadback,
}: {
  data: MasterBookingDetail;
  busy: boolean;
  onRecheck: () => void;
  onReadback: () => Promise<void>;
}) {
  // Ruling §61 (М-6 ж): длительность всегда минутами, «1 ч» не переводим.
  const duration = Number.isFinite(data.duration_min)
    ? `${Math.max(0, Math.floor(data.duration_min))} мин`
    : undefined;
  const range = COPY.range(formatTimeHM(data.start_at), formatTimeHM(data.end_at));
  return (
    <main className="booking-detail__main" aria-labelledby="booking-detail-client">
      <header className="booking-detail__client">
        <span className="booking-detail__avatar" aria-hidden="true">
          <IconPerson />
        </span>
        <h1 className="booking-detail__name" id="booking-detail-client">
          {data.client.name_initial}
        </h1>
      </header>

      {/* Постоянная часть — одна основа во всех состояниях. */}
      <ul className="booking-detail__facts">
        <FactRow icon={<IconCalendar />} title={formatDateDotWeekdayRu(data.start_at)} />
        <FactRow icon={<IconClock />} title={range} sub={duration} />
        <FactRow icon={<IconService />} title={data.service.name} sub={duration} />
      </ul>

      {isCancelled(data) ? (
        // status — раньше temporal_state: отменённая запись временного блока не имеет.
        <p className="booking-detail__cancelled">{COPY.cancelled}</p>
      ) : (
        <StateBlock
          state={data.temporal_state}
          data={data}
          range={range}
          busy={busy}
          onRecheck={onRecheck}
        />
      )}

      <BookingActions data={data} onReadback={onReadback} />
    </main>
  );
}

function FactRow({ icon, title, sub }: { icon: React.ReactNode; title: string; sub?: string }) {
  return (
    <li className="booking-detail__fact">
      <span className="booking-detail__fact-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="booking-detail__fact-text">
        <span className="booking-detail__fact-title">{title}</span>
        {sub ? <span className="booking-detail__fact-sub">{sub}</span> : null}
      </span>
    </li>
  );
}

function StateBlock({
  state,
  data,
  range,
  busy,
  onRecheck,
}: {
  state: BookingTemporalState;
  data: MasterBookingDetail;
  range: string;
  busy: boolean;
  onRecheck: () => void;
}) {
  let tone = "booking-detail__state";
  let body: React.ReactNode;
  switch (state) {
    case "upcoming":
      tone += " booking-detail__state--accent";
      body = (
        <>
          <p className="booking-detail__state-title">
            <IconClock /> {COPY.upcoming.label}
          </p>
          <p className="booking-detail__state-line">
            {formatUpcomingAtRu(data.start_at, data.checked_at)}
          </p>
          <hr className="booking-detail__state-rule" />
          <p className="booking-detail__state-line">
            {COPY.upcoming.until(data.minutes_until)}
          </p>
        </>
      );
      break;
    case "now":
      tone += " booking-detail__state--accent";
      body = (
        <>
          <p className="booking-detail__state-title">
            <span className="booking-detail__dot" aria-hidden="true" /> {COPY.now}
          </p>
          <p className="booking-detail__state-line">{range}</p>
        </>
      );
      break;
    case "completed":
      tone += " booking-detail__state--success";
      body = (
        <p className="booking-detail__state-title">
          <IconCheck /> {COPY.completed}
        </p>
      );
      break;
    case "after":
      body = (
        <>
          <p className="booking-detail__state-title">
            <IconClock /> {COPY.after.title}
          </p>
          <hr className="booking-detail__state-rule" />
          <p className="booking-detail__state-line">{COPY.after.body}</p>
        </>
      );
      break;
    case "unknown":
    default:
      // Незнакомое значение с сервера — «не знаем», а не утверждение о факте:
      // общий блок «Проверяем результат» (DRF-1181 п.10) ничего не утверждает
      // и даёт выход; тело — текст DRF-1185.
      return (
        <section className="booking-detail__state--bare" aria-label={COPY.stateRegion}>
          <SystemState kind="pending" body={COPY.unknownBody} busy={busy} onRecheck={onRecheck} />
        </section>
      );
  }
  return (
    <section className={tone} aria-label={COPY.stateRegion}>
      {body}
    </section>
  );
}

type ActionMode = "idle" | "cancel" | "reschedule" | "pending" | "stale";
type PendingTarget =
  | { kind: "cancel" }
  | { kind: "reschedule"; startAt: string }
  | null;

function BookingActions({
  data,
  onReadback,
}: {
  data: MasterBookingDetail;
  onReadback: () => Promise<void>;
}) {
  const actionable = data.status === "confirmed" && data.temporal_state === "upcoming";
  const [mode, setMode] = useState<ActionMode>("idle");
  const [mutating, setMutating] = useState(false);
  const [pendingTarget, setPendingTarget] = useState<PendingTarget>(null);
  const [attemptedVersion, setAttemptedVersion] = useState<number | null>(null);
  const [date, setDate] = useState(data.start_at.slice(0, 10));
  const [slots, setSlots] = useState<Array<{ time: string; start_at: string | null }>>([]);
  const [slotsBusy, setSlotsBusy] = useState(false);
  const [slotsError, setSlotsError] = useState<unknown>(null);
  const [selectedStart, setSelectedStart] = useState<string | null>(null);
  const [actionError, setActionError] = useState("");

  useEffect(() => {
    if (pendingTarget?.kind === "cancel" && isCancelled(data)) {
      setPendingTarget(null);
      setMode("idle");
    }
    if (pendingTarget?.kind === "reschedule" && data.start_at === pendingTarget.startAt) {
      setPendingTarget(null);
      setMode("idle");
    }
    if (
      mode === "stale" &&
      (attemptedVersion === null
        ? data.appointment_version !== null
        : data.appointment_version !== attemptedVersion)
    ) {
      setMode("idle");
      setAttemptedVersion(null);
    }
  }, [attemptedVersion, data, mode, pendingTarget]);

  if (!actionable && mode === "idle") return null;

  const readback = async () => {
    await onReadback();
  };

  const handleResult = async (
    result: Awaited<ReturnType<typeof actOnMasterBooking>>,
    target: PendingTarget,
    version: number | null,
  ) => {
    if (result.outcome === "committed") {
      setPendingTarget(target);
      setMode("pending");
      await readback();
      return;
    }
    if (result.outcome === "pending") {
      setPendingTarget(target);
      setMode("pending");
      return;
    }
    if (result.outcome === "conflict") {
      setAttemptedVersion(version);
      setMode("stale");
      await readback();
      return;
    }
    setActionError("Не удалось выполнить действие. Обновите запись и попробуйте снова.");
    setMode("idle");
  };

  const cancel = async () => {
    if (mutating) return;
    setMutating(true);
    setActionError("");
    try {
      const result = await actOnMasterBooking(data.id, {
        action: "cancel",
        ...(data.appointment_version ? { expected_version: data.appointment_version } : {}),
      });
      await handleResult(result, { kind: "cancel" }, data.appointment_version);
    } catch {
      setActionError("Действие недоступно. Обновите запись.");
      setMode("idle");
    } finally {
      setMutating(false);
    }
  };

  const loadSlots = async () => {
    if (!data.service.id) return;
    setSlotsBusy(true);
    setSlotsError(null);
    setSelectedStart(null);
    try {
      const result = await getMasterBookingSlots({ serviceId: data.service.id, date });
      setSlots(result.slots);
    } catch (err) {
      setSlots([]);
      setSlotsError(err);
    } finally {
      setSlotsBusy(false);
    }
  };

  const reschedule = async () => {
    if (mutating || !selectedStart) return;
    const version = data.appointment_version;
    if (version === null) {
      setAttemptedVersion(null);
      setMode("stale");
      return;
    }
    setMutating(true);
    setActionError("");
    try {
      const result = await actOnMasterBooking(data.id, {
        action: "reschedule",
        expected_version: version,
        new_start_datetime: selectedStart,
      });
      await handleResult(result, { kind: "reschedule", startAt: selectedStart }, version);
    } catch {
      setActionError("Действие недоступно. Обновите запись.");
      setMode("idle");
    } finally {
      setMutating(false);
    }
  };

  if (mode === "pending") {
    return (
      <section className="booking-detail__actions" aria-label="Действия с записью">
        <SystemState
          kind="pending"
          body="Не удалось подтвердить результат действия. Сначала проверьте актуальное состояние записи."
          busy={mutating}
          onRecheck={() => void readback()}
        />
      </section>
    );
  }

  if (mode === "stale") {
    return (
      <section className="booking-detail__actions" aria-label="Действия с записью">
        <SystemState
          kind="pending"
          body="Запись изменилась. Обновите данные перед новой попыткой."
          busy={mutating}
          onRecheck={() => void readback()}
        />
      </section>
    );
  }

  if (!actionable) return null;

  if (mode === "cancel") {
    return (
      <section className="booking-detail__actions" aria-label="Действия с записью">
        <DestructiveConfirmation
          title="Отменить запись?"
          body="Запись клиента будет отменена. Это действие нельзя отменить."
          confirmLabel="Отменить запись"
          busy={mutating}
          onConfirm={() => void cancel()}
          onCancel={() => setMode("idle")}
        />
      </section>
    );
  }

  if (mode === "reschedule") {
    return (
      <section className="booking-detail__actions booking-detail__reschedule" aria-label="Перенос записи">
        <h2>Перенести запись</h2>
        <label>
          Дата
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <button type="button" className="btn-secondary" onClick={() => void loadSlots()} disabled={slotsBusy}>
          {slotsBusy ? "Загружаем…" : "Показать свободное время"}
        </button>
        {slotsError ? (
          <SystemState
            kind="load_error"
            err={slotsError}
            what="schedule"
            busy={slotsBusy}
            onRetry={() => void loadSlots()}
          />
        ) : null}
        {slots.length > 0 ? (
          <div className="booking-detail__slot-list" aria-label="Свободное время">
            {slots.filter((slot) => slot.start_at).map((slot) => (
              <button
                type="button"
                key={slot.start_at ?? slot.time}
                className={selectedStart === slot.start_at ? "btn-primary" : "btn-secondary"}
                onClick={() => setSelectedStart(slot.start_at)}
              >
                {slot.time}
              </button>
            ))}
          </div>
        ) : null}
        {selectedStart ? (
          <div className="booking-detail__reschedule-confirm">
            <p>Перенести запись на {formatTimeHM(selectedStart)}?</p>
            <button type="button" className="btn-primary" onClick={() => void reschedule()} disabled={mutating}>
              Подтвердить перенос
            </button>
          </div>
        ) : null}
        <button type="button" className="btn-secondary" onClick={() => setMode("idle")} disabled={mutating}>
          Назад
        </button>
      </section>
    );
  }

  return (
    <section className="booking-detail__actions" aria-label="Действия с записью">
      {actionError ? <p role="alert">{actionError}</p> : null}
      <button type="button" className="btn-primary" onClick={() => setMode("reschedule")}>
        Перенести
      </button>
      <button type="button" className="btn-secondary" onClick={() => setMode("cancel")}>
        Отменить
      </button>
    </section>
  );
}
// ----------------------------------------------------------------------------
// Icons — line icons per mockup, decorative only
// ----------------------------------------------------------------------------

function IconPerson() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-3.3 3.6-6 8-6s8 2.7 8 6" />
    </svg>
  );
}

function IconCalendar() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  );
}

function IconClock() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function IconService() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M4 15c3-1 5-4 8-4s5 3 8 2" />
      <path d="M6 19c2-2 4-3 6-3s4 1 6 3" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="9" />
      <path d="M8 12l3 3 5-6" />
    </svg>
  );
}
