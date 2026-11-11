/**
 * Перенос записи клиентом — три шага на одном адресе. Макет «Записи.png»
 * (набор владельца от 06.10.2026, DRF-2952), кадры 5, 6, 7.
 *
 *   1. Выбор: неделя с днями и «Доступное время» (кадр 5).
 *   2. Подтверждение: что переносим, «Сейчас» и «Новое» (кадр 6).
 *   3. Результат: «Перенесла запись», «Было» и «Стало» (кадр 7).
 *
 * # Слова — владельца, раскладка — макета
 *
 * Макет подписывает шаги «Старое время / Новое время» и «Запись перенесена».
 * Слова владельца от 28.09 (п.8, DRF-2585) старше для текста: «Перенесла
 * запись», «Было», «Стало»; на шаге подтверждения — прежние «Сейчас» и
 * «Новое». Решение пользователя 11.11.2026: слова прежние, с макета — вид.
 * Блок «было → стало» раньше рисовала карточка записи после возврата с этого
 * экрана; теперь он здесь, на шаге результата, и там не повторяется.
 *
 * # Два ключа услуги
 *
 * Свободное время ищется по ключу услуги в каталоге бота
 * (`catalog_service_id`), а перенос отправляет `service_id` записи. На пути
 * Ayla это РАЗНЫЕ значения: экран слал в `/slots` второе и получал «service
 * not found» — перенос на этом пути не открывался вовсе.
 *
 * # Результат — только по ответу сервера
 *
 * «Перенесла запись» показывается после `verifyClaim` по `new_booking`
 * (DRF-2347), а «Стало» — время из ответа, не выбранное на шаге 1: на пути
 * Ayla время записи назначает канон.
 *
 * # Адреса
 *
 * Канонический — `/customer/records/:id/reschedule` (DRF-1481); старый
 * `/my-visits/:id/reschedule` — алиас на этот же компонент. Все выходы ведут
 * в `/customer/*` (DRF-1480): после переноса — карточка НОВОЙ записи.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { MasterThumb, RecordSummary } from "../components/records/RecordCard";
import { RecordIcon } from "../components/records/RecordIcons";
import { RecordsLoadError } from "../components/records/RecordsLoadError";
import { RecordButton, RecordsState } from "../components/records/RecordsState";
import { DelayedSkeleton, Skeleton, SlotGridSkeleton } from "../components/Skeleton";
import { Snackbar } from "../components/Snackbar";
import { useHaptics } from "../hooks/useHaptics";
import { useOnline } from "../hooks/useOnline";
import { useScreenBack } from "../hooks/useScreenBack";
import {
  ApiError,
  fetchBooking,
  fetchSlots,
  rescheduleBookingConfirm,
  rescheduleBookingRequest,
  type BookingItem,
  type FreeSlot,
} from "../lib/api";
import { lastReadSeq, readOf, verifyClaim, type Claim } from "../lib/claims";
import {
  formatDateLabel,
  formatDayMonthTime,
  formatMonthYear,
  formatRecordWhen,
  formatSlotTime,
  priceFromLabel,
} from "../lib/format";
import { CLIENT_RESCHEDULE_REFUSAL } from "../lib/refusal-canon";
import { backByAction, backTo } from "../lib/screen-back";
import { visitAddressText } from "../lib/visit-address";
import { resetBooking } from "../state/booking";

/** На сколько дней вперёд спрашиваем свободное время — предел одной выборки. */
const HORIZON_DAYS = 14;
const WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

export const RESCHEDULE_COPY = {
  title: "Выбери новую дату и время",
  slotsHeading: "Доступное время",
  next: "Продолжить",
  backToRecord: "Вернуться к записи",
  noSlots: "На ближайшие две недели свободного времени нет.",
  pickDay: "Выбери дату.",
  confirmTitle: "Проверь перенос",
  now: "Сейчас",
  fresh: "Новое",
  confirm: "Подтвердить перенос",
  confirmBusy: "Переношу…",
  cancel: "Отмена",
  // Слова владельца 28.09, п.8 — дословно.
  doneTitle: "Перенесла запись",
  was: "Было:",
  became: "Стало:",
  toRecord: "Перейти к записи",
  loadFailed: "Не удалось загрузить запись",
  orphan:
    "Эту услугу нельзя перенести — она больше не предлагается в текущем каталоге. Запишись заново.",
  catalog: "Каталог",
} as const;

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function isoDateNDaysAhead(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return isoDate(d);
}

/** «ГГГГ-ММ-ДД» плюс дни — арифметика в UTC, чтобы пояс устройства не сдвигал дату. */
function addDays(iso: string, days: number): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(Date.UTC(y ?? 0, (m ?? 1) - 1, (d ?? 1) + days));
  return date.toISOString().slice(0, 10);
}

/** Понедельник недели, в которую попадает дата. */
function mondayOf(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const weekday = new Date(Date.UTC(y ?? 0, (m ?? 1) - 1, d ?? 1)).getUTCDay();
  return addDays(iso, -((weekday + 6) % 7));
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; booking: BookingItem; slots: FreeSlot[] }
  | { kind: "orphan"; booking: BookingItem }
  | { kind: "error"; err: unknown };

type Step =
  | { kind: "pick" }
  | { kind: "confirm" }
  | { kind: "done"; claim: Claim<unknown>; newBooking: BookingItem; oldVisit: string };

export function RescheduleScreen() {
  const navigate = useNavigate();
  const haptics = useHaptics();
  const online = useOnline();
  const { bookingId } = useParams<{ bookingId: string }>();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [step, setStep] = useState<Step>({ kind: "pick" });
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [weekStart, setWeekStart] = useState<string>(() => mondayOf(isoDateNDaysAhead(0)));
  const [pickedSlot, setPickedSlot] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Возврат (DRF-1493). С выбора — к записи, которую переносят: адрес несёт
  // её id, родитель известен и при входе по ссылке. С подтверждения — на шаг
  // назад, выбранное время остаётся. С результата — к НОВОЙ записи: старая на
  // локальном пути уже в статусе «перенесена».
  const recordPath = bookingId ? `/customer/records/${bookingId}` : "/customer/records";
  const onBack = useScreenBack(
    step.kind === "confirm"
      ? backByAction(() => {
          // Пока перенос отправлен, назад нельзя: человек выбрал бы другое
          // время, а завершился бы запрос на прежнее.
          if (!confirming) setStep({ kind: "pick" });
        })
      : step.kind === "done"
        ? backTo(`/customer/records/${step.newBooking.id}`)
        : backTo(recordPath),
  );

  const load = useCallback(() => {
    if (!bookingId) return;
    setState({ kind: "loading" });
    let cancelled = false;
    fetchBooking(bookingId)
      .then(({ booking }) => {
        // «Сирота»: услуги или мастера записи больше нет в каталоге (связь
        // обнулена при пересеве). Без них свободное время не спросить —
        // честная панель «запишись заново» вместо стены ошибки.
        // `null` — сервер сказал «в каталоге услуги нет»; подменять его
        // `service_id` нельзя (на пути Ayla это чужой ключ). Запасной путь —
        // только когда поля нет вовсе: сервер старше.
        const serviceKey =
          booking.catalog_service_id !== undefined ? booking.catalog_service_id : booking.service_id;
        if (!serviceKey || !booking.master_id) {
          if (!cancelled) setState({ kind: "orphan", booking });
          return null;
        }
        return Promise.all([
          Promise.resolve(booking),
          fetchSlots({
            serviceId: serviceKey,
            masterId: booking.master_id,
            dateFrom: isoDateNDaysAhead(0),
            dateTo: isoDateNDaysAhead(HORIZON_DAYS),
          }).catch((err: unknown) => {
            // 404 от поиска времени — не «запись не загрузилась» (она
            // загрузилась): мастер больше не принимает, услугу у него сняли
            // или запись сделана в другом салоне. Повтор тут не поможет —
            // это та же «сирота».
            if (err instanceof ApiError && err.status === 404) return null;
            throw err;
          }),
        ]);
      })
      .then((pair) => {
        if (cancelled || pair === null) return;
        const [booking, found] = pair;
        if (found === null) {
          setState({ kind: "orphan", booking });
          return;
        }
        const { slots } = found;
        setState({ kind: "ok", booking, slots });
        const first = slots[0]?.date;
        if (first) {
          setSelectedDate(first);
          setWeekStart(mondayOf(first));
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ kind: "error", err });
      });
    return () => {
      cancelled = true;
    };
  }, [bookingId]);

  useEffect(() => load(), [load]);

  const slotsByDate = useMemo(() => {
    const m = new Map<string, FreeSlot[]>();
    if (state.kind !== "ok") return m;
    for (const s of state.slots) {
      const arr = m.get(s.date) ?? [];
      arr.push(s);
      m.set(s.date, arr);
    }
    return m;
  }, [state]);

  const week = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));
  // Время показывается только у дня показанной недели: иначе после смены
  // недели под ней осталось бы время дня, которого на экране уже нет.
  const dayShown = selectedDate !== null && week.includes(selectedDate);
  const slotsForDay = dayShown && selectedDate ? (slotsByDate.get(selectedDate) ?? []) : [];
  // Листать можно туда, где есть свободное время, — по самим данным, а не по
  // дате устройства: у салона уже может быть следующий день.
  const freeDates = Array.from(slotsByDate.keys());
  const hasEarlier = freeDates.some((d) => d < weekStart);
  const hasLater = freeDates.some((d) => d > addDays(weekStart, 6));

  /** Перейти к неделе: выбран её первый свободный день, время сброшено. */
  const showWeek = (monday: string) => {
    setWeekStart(monday);
    setPickedSlot(null);
    const days = Array.from({ length: 7 }, (_, i) => addDays(monday, i));
    setSelectedDate(days.find((d) => slotsByDate.has(d)) ?? null);
  };

  async function onConfirm() {
    if (!bookingId || !pickedSlot || state.kind !== "ok") return;
    const b = state.booking;
    if (!b.service_id || !b.master_id) return;
    setConfirming(true);
    setError(null);
    const startedAt = lastReadSeq();
    try {
      const candidate = {
        new_master_id: b.master_id,
        new_service_id: b.service_id,
        new_visit_at: pickedSlot,
      };
      // Заявка: локальный путь откладывает кандидата, путь Ayla — проверяет.
      await rescheduleBookingRequest(bookingId, candidate);
      // Подтверждение. DRF-2561: на пути Ayla отложить кандидата некуда,
      // поэтому подтверждение несёт его само.
      const { new_booking } = await rescheduleBookingConfirm(bookingId, candidate);
      resetBooking();
      const from = readOf(new_booking);
      const claim: Claim<unknown> | null = from
        ? { outcome: "booking_rescheduled", from, after: startedAt }
        : null;
      if (claim && verifyClaim(claim).ok) {
        setStep({ kind: "done", claim, newBooking: new_booking, oldVisit: b.visit_at });
      } else {
        // Ответ не подтверждает перенос словами — тогда без слов: открываем
        // запись, она сама скажет своё время.
        navigate(`/customer/records/${new_booking.id}`, { replace: true });
      }
    } catch (err) {
      if (err instanceof ApiError && err.slug === "slot_unavailable") {
        setError(CLIENT_RESCHEDULE_REFUSAL.slotTaken);
        // Выбранного времени больше нет: назад к выбору, со свежим списком.
        setPickedSlot(null);
        setStep({ kind: "pick" });
        load();
      } else {
        // Решение владельца 28.09, п.10: «перенос не удался по другой
        // причине» — одна фраза клиентского регистра, в том числе без сети.
        setError(CLIENT_RESCHEDULE_REFUSAL.failed);
      }
    } finally {
      setConfirming(false);
    }
  }

  const topbar = (
    <header className="cr-topbar">
      <button type="button" className="cr-back" aria-label="Назад" onClick={onBack}>
        <RecordIcon name="back" size={24} />
      </button>
    </header>
  );

  const snackbar = (
    <Snackbar
      visible={error !== null}
      message={error ?? ""}
      durationMs={4000}
      onTimeout={() => setError(null)}
      onDismiss={() => setError(null)}
    />
  );

  if (state.kind === "loading") {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <main className="cr-flow">
          <h1 className="cr-flow__title">{RESCHEDULE_COPY.title}</h1>
          <DelayedSkeleton loading>
            <Skeleton width="100%" height="112px" radius="var(--r-lg)" />
            <SlotGridSkeleton />
          </DelayedSkeleton>
        </main>
        {snackbar}
      </div>
    );
  }

  if (state.kind === "error") {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <h1 className="visually-hidden">{RESCHEDULE_COPY.title}</h1>
        <RecordsLoadError
          err={state.err}
          online={online}
          title={RESCHEDULE_COPY.loadFailed}
          onRetry={load}
        />
      </div>
    );
  }

  if (state.kind === "orphan") {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <main className="cr-flow">
          <h1 className="cr-flow__title">{RESCHEDULE_COPY.title}</h1>
          <div className="cr-note" role="status">
            <p>{RESCHEDULE_COPY.orphan}</p>
            <RecordButton variant="soft" onClick={() => navigate("/customer/catalog")}>
              {RESCHEDULE_COPY.catalog}
            </RecordButton>
          </div>
        </main>
      </div>
    );
  }

  const b = state.booking;
  const priceLabel = b.price ? priceFromLabel(b.price) : "";

  // --- шаг 3, кадр 7 -------------------------------------------------------

  if (step.kind === "done") {
    const done = step.newBooking;
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <RecordsState
          icon="check"
          tone="success"
          title={RESCHEDULE_COPY.doneTitle}
          claim={step.claim}
          actions={
            <RecordButton
              variant="primary"
              onClick={() => navigate(`/customer/records/${done.id}`, { replace: true })}
            >
              {RESCHEDULE_COPY.toRecord}
            </RecordButton>
          }
        >
          <RecordSummary
            photoUrl={done.master_photo_url ?? b.master_photo_url}
            masterName={done.master_name || b.master_name}
            serviceName={done.service_name || b.service_name}
            rows={[
              { icon: "calendar", label: RESCHEDULE_COPY.was, text: formatDayMonthTime(step.oldVisit) },
              // «Стало» — время самой записи из ответа, не выбранное на экране.
              { icon: "calendar", label: RESCHEDULE_COPY.became, text: formatDayMonthTime(done.visit_at) },
              ...(priceLabel ? [{ icon: "price" as const, text: priceLabel }] : []),
            ]}
          />
        </RecordsState>
      </div>
    );
  }

  // --- шаг 2, кадр 6 -------------------------------------------------------

  if (step.kind === "confirm" && pickedSlot) {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <main className="cr-flow">
          <h1 className="visually-hidden">{RESCHEDULE_COPY.confirmTitle}</h1>
          <MasterThumb photoUrl={b.master_photo_url} name={b.master_name} size="tile" />
          <p className="cr-flow__service">{b.service_name}</p>
          {b.master_name && (
            <div className="cr-master">
              <MasterThumb photoUrl={b.master_photo_url} name={b.master_name} size="avatar" />
              <div>
                <div className="cr-master__name">{b.master_name}</div>
                {b.master_specialization && (
                  <div className="cr-master__role">{b.master_specialization}</div>
                )}
              </div>
            </div>
          )}
          <p className="cr-flow__row">
            <RecordIcon name="pin" />
            <span>
              {b.salon_name ? `${b.salon_name} · ` : ""}
              {visitAddressText(b.address)}
            </span>
          </p>

          <div className="cr-when">
            <div className="cr-when__label">{RESCHEDULE_COPY.now}</div>
            <div className="cr-when__value">
              <RecordIcon name="calendar" />
              {formatRecordWhen(b.visit_at)}
            </div>
          </div>
          <div className="cr-when cr-when--new">
            <div className="cr-when__label">{RESCHEDULE_COPY.fresh}</div>
            <div className="cr-when__value">
              <RecordIcon name="calendar" />
              {formatRecordWhen(pickedSlot)}
            </div>
          </div>
          {priceLabel && (
            <p className="cr-flow__row cr-flow__row--strong">
              <RecordIcon name="price" />
              <span>{priceLabel}</span>
            </p>
          )}

          <div className="cr-actions">
            <RecordButton variant="primary" onClick={() => void onConfirm()} disabled={confirming}>
              {confirming ? RESCHEDULE_COPY.confirmBusy : RESCHEDULE_COPY.confirm}
            </RecordButton>
            <RecordButton
              variant="text"
              onClick={() => setStep({ kind: "pick" })}
              disabled={confirming}
            >
              {RESCHEDULE_COPY.cancel}
            </RecordButton>
          </div>
        </main>
        {snackbar}
      </div>
    );
  }

  // --- шаг 1, кадр 5 -------------------------------------------------------

  // Месяц в заголовке — у выбранного дня, если он на этой неделе; иначе у
  // первого дня недели со свободным временем; иначе у понедельника.
  const titleDay =
    (dayShown && selectedDate ? selectedDate : undefined) ??
    week.find((d) => slotsByDate.has(d)) ??
    weekStart;

  return (
    <div className="cr-screen cr-screen--nested">
      {topbar}
      <main className="cr-flow">
        <h1 className="cr-flow__title">{RESCHEDULE_COPY.title}</h1>
        {/* На макете этой строки нет. Она отвечает на «какую запись я
            переношу»: человек приходит сюда и по ссылке из чата. */}
        <p className="cr-flow__sub">
          {[b.service_name, formatRecordWhen(b.visit_at)].filter(Boolean).join(" · ")}
        </p>

        {state.slots.length === 0 ? (
          <div className="cr-note" role="status">
            <p>{RESCHEDULE_COPY.noSlots}</p>
          </div>
        ) : (
          <>
            <div className="cr-week">
              <div className="cr-week__head">
                <span className="cr-week__month">{formatMonthYear(titleDay)}</span>
                {hasEarlier && (
                  <button
                    type="button"
                    className="cr-week__nav"
                    aria-label="Предыдущая неделя"
                    onClick={() => showWeek(addDays(weekStart, -7))}
                  >
                    <RecordIcon name="back" />
                  </button>
                )}
                {hasLater && (
                  <button
                    type="button"
                    className="cr-week__nav"
                    aria-label="Следующая неделя"
                    onClick={() => showWeek(addDays(weekStart, 7))}
                  >
                    <RecordIcon name="chevron" />
                  </button>
                )}
              </div>
              <div className="cr-week__days" role="radiogroup" aria-label="Дата визита">
                {week.map((d, i) => {
                  const free = slotsByDate.has(d);
                  const active = selectedDate === d;
                  return (
                    <button
                      type="button"
                      key={d}
                      role="radio"
                      aria-checked={active}
                      aria-label={formatDateLabel(d)}
                      disabled={!free}
                      className={`cr-week__day${active ? " cr-week__day--on" : ""}`}
                      onClick={() => {
                        haptics.selection();
                        setSelectedDate(d);
                        setPickedSlot(null);
                      }}
                    >
                      <span className="cr-week__name">{WEEKDAY_NAMES[i]}</span>
                      <span className="cr-week__num">{Number(d.slice(8))}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <h2 className="cr-heading">{RESCHEDULE_COPY.slotsHeading}</h2>
            {slotsForDay.length === 0 ? (
              <div className="cr-note" role="status">
                <p>
                  {dayShown && selectedDate
                    ? `На ${formatDateLabel(selectedDate)} свободного времени нет.`
                    : RESCHEDULE_COPY.pickDay}
                </p>
              </div>
            ) : (
              <div className="cr-slots" role="radiogroup" aria-label="Свободное время">
                {slotsForDay.map((s) => {
                  const active = pickedSlot === s.start;
                  return (
                    <button
                      type="button"
                      key={s.start}
                      role="radio"
                      aria-checked={active}
                      className={`cr-slots__cell${active ? " cr-slots__cell--on" : ""}`}
                      onClick={() => {
                        haptics.selection();
                        setPickedSlot(s.start);
                      }}
                    >
                      {formatSlotTime(s.start)}
                    </button>
                  );
                })}
              </div>
            )}
          </>
        )}

        <div className="cr-actions">
          <RecordButton
            variant="primary"
            onClick={() => setStep({ kind: "confirm" })}
            disabled={!pickedSlot}
          >
            {RESCHEDULE_COPY.next}
          </RecordButton>
          <RecordButton variant="text" onClick={() => navigate(recordPath)}>
            {RESCHEDULE_COPY.backToRecord}
          </RecordButton>
        </div>
      </main>
      {snackbar}
    </div>
  );
}
