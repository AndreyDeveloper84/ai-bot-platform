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
 * «15:30–16:30» + «1 ч» · услуга + «1 ч». Контекстный блок, один из:
 *   upcoming  → «Следующая запись» · «Сегодня в 15:30» · «До визита 1 ч 20 мин»
 *   now       → «Сейчас по расписанию» · «15:30–16:30»
 *   after     → «Запись закончилась по расписанию» ·
 *                «Если всё прошло как запланировано, ничего делать не нужно.»
 *   completed → «Завершено»
 *   unknown   → «Проверяем результат» · «Не удалось получить актуальное
 *                состояние записи.» · [Проверить снова] (троттл 10 с, как у
 *                readiness; кнопка заблокирована на время запроса)
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
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useParams } from "react-router-dom";

import { MasterTabBar } from "../components/MasterTabBar";
import { useScreenBack } from "../hooks/useScreenBack";
import {
  getMasterBooking,
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

/** Не чаще раза в 10 с — как у readiness (решение главного окна 20.09). */
export const RECHECK_MIN_INTERVAL_MS = 10_000;

const COPY = {
  // Тексты макета DRF-1185 — дословно.
  stateRegion: "Состояние записи",
  upcoming: {
    label: "Следующая запись",
    until: (min: number) => `До визита ${formatDurationRu(min)}`,
  },
  now: "Сейчас по расписанию",
  after: {
    title: "Запись закончилась по расписанию",
    body: "Если всё прошло как запланировано, ничего делать не нужно.",
  },
  completed: "Завершено",
  unknown: {
    title: "Проверяем результат",
    body: "Не удалось получить актуальное состояние записи.",
    recheck: "Проверить снова",
  },
  // Вне макета — решение владельца §61.
  cancelled: "Запись отменена",
  loading: "Загружаем запись…",
  loadError: "Не удалось загрузить запись",
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
  const [cooldown, setCooldown] = useState(false);
  const alive = useRef(true);
  const inflight = useRef<AbortController | null>(null);
  const cooldownTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const armCooldown = useCallback(() => {
    if (cooldownTimer.current) clearTimeout(cooldownTimer.current);
    setCooldown(true);
    cooldownTimer.current = setTimeout(() => {
      if (alive.current) setCooldown(false);
    }, RECHECK_MIN_INTERVAL_MS);
  }, []);

  const load = useCallback(
    async (opts: { throttle: boolean } = { throttle: false }) => {
      inflight.current?.abort();
      const ctrl = new AbortController();
      inflight.current = ctrl;
      setBusy(true);
      if (opts.throttle) armCooldown();
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
    },
    [id, armCooldown],
  );

  useEffect(() => {
    alive.current = true;
    signalReady();
    void load();
    return () => {
      alive.current = false;
      inflight.current?.abort();
      if (cooldownTimer.current) clearTimeout(cooldownTimer.current);
    };
  }, [load]);

  // «Проверить снова» при unknown — с троттлом и блокировкой на время запроса.
  const recheck = useCallback(() => {
    if (busy || cooldown) return;
    void load({ throttle: true });
  }, [busy, cooldown, load]);

  // Повтор после сбоя загрузки — без троттла: беречь нечего.
  const retryAfterError = useCallback(() => {
    if (busy) return;
    void load({ throttle: false });
  }, [busy, load]);

  return (
    <div className="master-dashboard">
      {phase.kind === "loading" ? (
        <LoadingBody />
      ) : phase.kind === "error" ? (
        <ErrorBody onRetry={retryAfterError} busy={busy} />
      ) : (
        <DetailBody
          data={phase.data}
          recheckDisabled={busy || cooldown}
          onRecheck={recheck}
        />
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
  recheckDisabled,
  onRecheck,
}: {
  data: MasterBookingDetail;
  recheckDisabled: boolean;
  onRecheck: () => void;
}) {
  const duration = formatDurationRu(data.duration_min);
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
          recheckDisabled={recheckDisabled}
          onRecheck={onRecheck}
        />
      )}
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
  recheckDisabled,
  onRecheck,
}: {
  state: BookingTemporalState;
  data: MasterBookingDetail;
  range: string;
  recheckDisabled: boolean;
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
            {COPY.upcoming.until(data.minutes_until ?? 0)}
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
    case "unknown":
      tone += " booking-detail__state--accent";
      body = (
        <>
          <p className="booking-detail__state-title">
            <IconRefresh /> {COPY.unknown.title}
          </p>
          <p className="booking-detail__state-line">{COPY.unknown.body}</p>
          <button
            type="button"
            className="btn-secondary booking-detail__recheck"
            onClick={onRecheck}
            disabled={recheckDisabled}
          >
            {COPY.unknown.recheck}
          </button>
        </>
      );
      break;
    case "after":
    default:
      // Неизвестное значение с сервера читаем как «после»: спокойный текст,
      // никаких действий — это безопаснее любого другого предположения.
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
  }
  return (
    <section className={tone} aria-label={COPY.stateRegion}>
      {body}
    </section>
  );
}

// ----------------------------------------------------------------------------
// Loading / error
// ----------------------------------------------------------------------------

function LoadingBody() {
  return (
    <div className="master-dashboard__skeleton-wrap" aria-busy="true">
      <p className="master-dashboard__loading-label">{COPY.loading}</p>
      <div className="m-card m-card--skel" style={{ height: 56 }} />
      <div className="m-card m-card--skel" style={{ height: 140 }} />
      <div className="m-card m-card--skel" style={{ height: 96 }} />
    </div>
  );
}

function ErrorBody({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <div className="master-dashboard__section">
      <div className="callout callout--danger" role="alert">
        <p style={{ margin: 0 }}>{COPY.loadError}</p>
        <div style={{ marginTop: "var(--s-3)" }}>
          <button type="button" className="btn-secondary" onClick={onRetry} disabled={busy}>
            {COPY.unknown.recheck}
          </button>
        </div>
      </div>
    </div>
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

function IconRefresh() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M20 12a8 8 0 1 1-2.3-5.7" />
      <path d="M20 4v5h-5" />
    </svg>
  );
}
