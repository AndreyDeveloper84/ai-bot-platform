/**
 * Карточка записи клиента. Макет «Записи.png» (набор владельца от 06.10.2026,
 * DRF-2952), кадры 3, 4, 8, 9, 10, 13.
 *
 * Адрес: `/customer/records/:bookingId`.
 *
 * # Что на карточке и откуда
 *
 * Только то, что отдаёт `GET /bookings/<id>`: услуга, мастер (имя, фото,
 * специализация), салон и адрес, день и время, длительность, цена, оплата,
 * оценка. Чего макет рисует, а провод не несёт, здесь нет: расстояния до
 * салона и «подготовки к процедуре». Время визита, наоборот, добавлено — на
 * кадрах 3–4 оно потеряно (часы стоят у цены), а без него карточка бесполезна.
 *
 * Фото-шапка — фото мастера: у услуг картинок нет. Нет фото — шапки нет.
 *
 * # Отмена — три разных ответа сервера, три разных продолжения
 *
 * Подтверждение — кадр 8 на весь экран. Дальше экран ничего не утверждает
 * сам, а читает ответ:
 *
 *   - `cancelled` — кадр 9 «Запись отменена». Только с доказательством
 *     (`verifyClaim`, DRF-2347), причём свежим: чтение новее нажатия;
 *   - `cancel_requested` (локальный путь, окно возврата) — карточка и
 *     сообщение о ХОДЕ дела с возвратом (`CANCEL_STARTED_COPY`, DRF-2346);
 *     кадр 9 — после того, как подтверждение вернуло `cancelled`;
 *   - прежний статус (путь Ayla: отмена принята, а зеркало запись переведёт
 *     событием позже) — «Отменяю запись…» и перечитывание записи; кадр 9 —
 *     когда прочитано `cancelled`; не дождались — честное «ещё
 *     обрабатывается», а не «отменена». До DRF-2952 здесь стояло «Запись
 *     отменена» при статусе `confirmed` — тот же дефект, что DRF-2346.
 *
 * Сбой запроса — это неизвестный исход: запрос мог дойти. Запись
 * перечитывается: отменена — кадр 9; «отмена запрошена» — отмена идёт, ждём
 * её; сервер отказал определённо (4xx) — кадр 10; сеть или 5xx — сначала
 * перечитываем несколько раз и только потом кадр 10. «Попробовать снова»
 * возвращает к кадру 8: повторную отмену человек отправляет сам.
 *
 * # Чего здесь нет
 *
 * Кадра 11 «Запись отменена салоном»: ни один из двух проводов не хранит, кто
 * отменил запись. Нижней панели: карточка — вложенный экран с возвратом
 * (DRF-2201 — панель рисуют только корни). Блока «было → стало» после
 * переноса: он переехал на экран результата переноса (кадр 7).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CustomerAvatarEntry } from "../components/CustomerAvatarEntry";
import { PaymentStatusBadge } from "../components/PaymentStatusBadge";
import { MasterThumb, RecordSummary } from "../components/records/RecordCard";
import { RecordIcon } from "../components/records/RecordIcons";
import { RECORDS_ERROR_COPY, RecordsLoadError } from "../components/records/RecordsLoadError";
import { RecordButton, RecordsState } from "../components/records/RecordsState";
import { Snackbar } from "../components/Snackbar";
import { useMasterPhoto } from "../hooks/useMasterPhoto";
import { masterPhotoPath } from "../lib/master-photo";
import { useOnline } from "../hooks/useOnline";
import { useScreenBack } from "../hooks/useScreenBack";
import { useSheetKeyNav } from "../hooks/useSheetKeyNav";
import {
  ApiError,
  cancelBookingConfirm,
  cancelBookingRequest,
  cancelBookingUndo,
  type BookingItem,
  type CancelReasonClass,
} from "../lib/api";
import { lastReadSeq, readOf, verifyClaim, type Claim } from "../lib/claims";
import { SUPPORT_DEEPLINK } from "../lib/customer-profile";
import {
  displayStatusFor,
  getBookingDetail,
  isOver,
  progressLabel,
  renderStatus,
} from "../lib/customer-records";
import {
  formatDayWeekday,
  formatDuration,
  formatMoney,
  formatRecordWhen,
  formatSlotTime,
  priceFromLabel,
} from "../lib/format";
import { backTo } from "../lib/screen-back";
import { visitAddressText } from "../lib/visit-address";

type Load =
  | { kind: "loading" }
  | { kind: "ok"; booking: BookingItem }
  | { kind: "error"; err: unknown };

/** Что показано поверх карточки. */
type View =
  | { kind: "card" }
  | { kind: "confirm" }
  | { kind: "cancelling" }
  | { kind: "cancelled"; claim: Claim<unknown> }
  | { kind: "pending" }
  | { kind: "failed" };

const REASON_CHIPS: { value: CancelReasonClass; label: string }[] = [
  { value: "timing", label: "Не успеваю" },
  { value: "plans_changed", label: "Изменились планы" },
  { value: "not_needed", label: "Не нужна услуга сейчас" },
  { value: "other", label: "Другое" },
];

/** Как часто и сколько раз перечитывать запись, пока отмена доходит до зеркала. */
const READBACK_EVERY_MS = 1500;
const READBACK_TRIES = 6;

/**
 * DRF-2346 — текст ЖДЁТ СЛОВА ВЛАДЕЛЬЦА (вопрос задан 23.09).
 *
 * Смысл, который он обязан нести: отмена ЗАПУЩЕНА и через несколько секунд
 * станет окончательной; завершится сама, даже если закрыть приложение;
 * вернуть пока можно, кнопка рядом. Чего в нём быть не должно — слова,
 * утверждающего выполненное: сервер в этот момент отвечает «отмена запрошена».
 */
export const CANCEL_STARTED_COPY =
  "Отменяю запись — через несколько секунд станет окончательно. Пока можно вернуть.";

export const DETAIL_COPY = {
  reschedule: "Перенести запись",
  cancel: "Отменить запись",
  repeat: "Повторить запись",
  rate: "Оценить визит",
  // Кадр 4. На макете «отмените… выберите другой слот»: обращение на «ты» —
  // канон клиентских экранов (DRF-2712), а «слот» человеку не показывается
  // (DRF-2593).
  noReschedule:
    "Перенос этой записи недоступен. Если нужно изменить время — отмени запись и выбери другое время.",
  finished: "Запись завершена",
  confirmTitle: "Отменить запись?",
  // «Без возможности восстановления» — правда только там, где окна возврата
  // нет; на локальном пути пять секунд на возврат есть, и фраза туда не идёт.
  confirmFinal: "Запись будет отменена без возможности восстановления.",
  confirmAgain: "При необходимости ты сможешь записаться снова.",
  confirmYes: "Да, отменить запись",
  confirmBusy: "Отменяю…",
  confirmNo: "Не отменять",
  reasons: "Что повлияло? (необязательно)",
  cancelledTitle: "Запись отменена",
  cancelledBody: "Ты можешь записаться снова в любое время.",
  findMaster: "Найти мастера",
  toRecords: "Вернуться к записям",
  failedTitle: "Не удалось отменить запись",
  failedBody:
    "Сейчас не получилось отменить запись. Попробуй ещё раз. Если проблема повторится, напиши в поддержку.",
  // Двух фраз ниже на макете нет: это путь, где сервер принял отмену, а
  // запись ещё не перевёл. Слова рабочие, о ходе дела — владельцу на сверку.
  cancellingTitle: "Отменяю запись…",
  cancellingBody: "Это займёт несколько секунд.",
  pendingTitle: "Отмена ещё обрабатывается",
  pendingBody: "Запрос принят, но запись пока не обновилась. Проверь её чуть позже.",
  pendingRefresh: "Проверить ещё раз",
  goneTitle: "Этой записи больше нет",
  goneBody: "Если отменял её не ты — напиши в поддержку из профиля.",
  loadFailed: "Не удалось загрузить запись",
  undoElapsed: "Окно отмены истекло.",
} as const;

/** Утверждение «запись отменена» по прочитанной записи — или `null`. */
function cancelledClaim(booking: BookingItem, after: number): Claim<unknown> | null {
  const from = readOf(booking);
  if (!from) return null;
  const claim: Claim<unknown> = { outcome: "booking_cancelled", from, after };
  return verifyClaim(claim).ok ? claim : null;
}

/** Строка статуса в карточке (кадр 13); обычная предстоящая — без строки. */
function statusLine(booking: BookingItem): string | null {
  // «В пути» — словами о ходе: общий словарь назвал бы `cancel_requested`
  // «Отменена» при живой записи (DRF-2346).
  const inProgress = progressLabel(booking.status);
  if (inProgress) return inProgress;
  const { status, rendering } = renderStatus(displayStatusFor(booking));
  if (status === "confirmed") return null;
  if (status === "completed") return DETAIL_COPY.finished;
  return rendering.label;
}

/** 404 сервера — записи нет (или она не этого человека: сервер их не различает). */
function isGone(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

export function CustomerBookingDetailScreen() {
  const navigate = useNavigate();
  const online = useOnline();

  // Возврат (DRF-1493) — к списку записей. Не `-1`: карточку открывают и по
  // ссылке из бота, и после оформления, возвращаться в которое нельзя.
  const onBack = useScreenBack(backTo("/customer/records"));
  const { bookingId } = useParams<{ bookingId: string }>();

  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [view, setView] = useState<View>({ kind: "card" });
  const [reasonClass, setReasonClass] = useState<CancelReasonClass | null>(null);
  const [busy, setBusy] = useState(false);
  const [snack, setSnack] = useState<{ message: string; undo: boolean } | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  /** Куда вернуть фокус, когда подтверждение отмены закрыто без отмены. */
  const cancelTrigger = useRef<HTMLDivElement | null>(null);
  /** Номер чтения до нажатия «отменить»: исход — только из чтений новее. */
  const cancelStartedAt = useRef(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const booking = load.kind === "ok" ? load.booking : null;
  const heroPhoto = useMasterPhoto(booking?.master_photo_url);

  const closeConfirm = useCallback(() => {
    setView((v) => (v.kind === "confirm" ? { kind: "card" } : v));
    setReasonClass(null);
    // Диалог закрыт — фокус возвращается на кнопку, которая его открыла.
    window.setTimeout(() => cancelTrigger.current?.querySelector<HTMLElement>(".cr-btn--danger")?.focus(), 0);
  }, []);

  // Escape и ловушка фокуса у подтверждения отмены (#953).
  useSheetKeyNav(dialogRef, {
    onClose: () => {
      if (view.kind === "confirm") closeConfirm();
    },
    closeDisabled: busy,
  });

  const reload = useCallback(() => {
    if (!bookingId) return;
    setLoad({ kind: "loading" });
    getBookingDetail(bookingId)
      .then((fresh) => setLoad({ kind: "ok", booking: fresh }))
      .catch((err: unknown) => setLoad({ kind: "error", err }));
  }, [bookingId]);

  useEffect(() => reload(), [reload]);

  /** Прочитанная запись решает, что показать: кадр 9 или ничего. */
  const settle = useCallback((fresh: BookingItem): boolean => {
    setLoad({ kind: "ok", booking: fresh });
    const claim = cancelledClaim(fresh, cancelStartedAt.current);
    if (claim) setView({ kind: "cancelled", claim });
    return claim !== null;
  }, []);

  /**
   * Ждём, пока запись переведут в «отменена», перечитывая её.
   *
   * `otherwise` — что показать, если не дождались: «ещё обрабатывается», когда
   * сервер отмену ПРИНЯЛ, и «не удалось», когда мы не знаем, дошла ли она.
   */
  const awaitCancelled = useCallback(
    async (otherwise: "pending" | "failed" = "pending") => {
      if (!bookingId) return;
      setView({ kind: "cancelling" });
      for (let attempt = 0; attempt < READBACK_TRIES; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, READBACK_EVERY_MS));
        if (!alive.current) return;
        try {
          if (settle(await getBookingDetail(bookingId))) return;
        } catch {
          /* одно неудачное чтение — не исход: следующее скажет */
        }
        if (!alive.current) return;
      }
      setView({ kind: otherwise });
    },
    [bookingId, settle],
  );

  async function onConfirmCancel() {
    if (!bookingId) return;
    setBusy(true);
    cancelStartedAt.current = lastReadSeq();
    try {
      const { booking: answered } = await cancelBookingRequest(bookingId, {
        reason_class: reasonClass ?? undefined,
      });
      setReasonClass(null);
      if (settle(answered)) return;
      if (answered.status === "cancel_requested") {
        // Локальный двухшаговый путь: сервер сказал «запрошена», а не
        // «отменена». Отмену добьёт сервер и без этой вкладки, поэтому
        // обещание «станет окончательно» — правда.
        setView({ kind: "card" });
        setSnack({ message: CANCEL_STARTED_COPY, undo: true });
        return;
      }
      void awaitCancelled();
    } catch (err) {
      // Исход неизвестен: запрос мог дойти, а ответ — потеряться. Правду
      // говорит запись, поэтому сначала читаем её.
      let fresh: BookingItem | null = null;
      let rereadErr: unknown = null;
      try {
        fresh = await getBookingDetail(bookingId);
        if (settle(fresh)) return;
      } catch (e) {
        /* перечитать не вышло — решаем по самой ошибке ниже */
        rereadErr = e;
      }
      if (isGone(err) && isGone(rereadErr)) {
        // Записи нет ни для отмены, ни для чтения: это не «не удалось
        // отменить» с повтором, а «Этой записи больше нет» (ревью #2458).
        setView({ kind: "card" });
        setLoad({ kind: "error", err: rereadErr });
        return;
      }
      if (fresh?.status === "cancel_requested") {
        // Отмена дошла и идёт: её добьёт сервер. «Не удалось» было бы ложью.
        void awaitCancelled("pending");
      } else if (err instanceof ApiError && err.status < 500) {
        // Сервер отказал определённо — отмены нет.
        setView({ kind: "failed" });
      } else {
        // Сеть или 5xx: дошло ли — неизвестно. Даём записи время обновиться
        // и только потом говорим «не удалось»; повтор безопасен.
        void awaitCancelled("failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function onUndoWindowElapsed() {
    if (!bookingId) return;
    setSnack(null);
    try {
      const { booking: answered } = await cancelBookingConfirm(bookingId);
      if (!settle(answered) && answered.status === "cancel_requested") void awaitCancelled();
    } catch {
      // Уже подтверждено другой вкладкой или сервером — правду скажет чтение.
      // Запись всё ещё «отмена запрошена» — отмену добьёт сервер: ждём её, а
      // не оставляем человека на карточке без объяснений.
      try {
        const fresh = await getBookingDetail(bookingId);
        if (!settle(fresh) && fresh.status === "cancel_requested") void awaitCancelled();
      } catch {
        reload();
      }
    }
  }

  async function onUndo() {
    if (!bookingId) return;
    setSnack(null);
    try {
      const { booking: answered } = await cancelBookingUndo(bookingId);
      setLoad({ kind: "ok", booking: answered });
    } catch (err) {
      if (err instanceof ApiError && err.slug === "undo_window_elapsed") {
        setSnack({ message: DETAIL_COPY.undoElapsed, undo: false });
      }
      reload();
    }
  }

  const topbar = (
    <header className="cr-topbar">
      <button type="button" className="cr-back" aria-label="Назад" onClick={onBack}>
        <RecordIcon name="back" size={24} />
      </button>
    </header>
  );

  if (load.kind === "loading") {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <h1 className="visually-hidden">Запись</h1>
        <div className="cr-loading" role="status">
          Загружаю…
        </div>
      </div>
    );
  }

  if (load.kind === "error") {
    const gone = load.err instanceof ApiError && load.err.status === 404;
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <h1 className="visually-hidden">Запись</h1>
        {gone ? (
          <RecordsState
            icon="gone"
            title={DETAIL_COPY.goneTitle}
            body={<p>{DETAIL_COPY.goneBody}</p>}
            quiet
            actions={
              <RecordButton variant="primary" onClick={() => navigate("/customer/records")}>
                {DETAIL_COPY.toRecords}
              </RecordButton>
            }
          />
        ) : (
          <RecordsLoadError
            err={load.err}
            online={online}
            title={DETAIL_COPY.loadFailed}
            onRetry={reload}
          />
        )}
      </div>
    );
  }

  const b = load.booking;
  const summary = (
    <RecordSummary
      photoUrl={b.master_photo_url}
      masterName={b.master_name}
      serviceName={b.service_name || "Запись"}
      rows={[{ icon: "calendar", text: formatRecordWhen(b.visit_at) }]}
    />
  );

  // --- исходы отмены: кадры 9, 10 и два состояния ожидания ----------------

  if (view.kind === "cancelled") {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <h1 className="visually-hidden">Запись</h1>
        <RecordsState
          icon="check"
          tone="success"
          title={DETAIL_COPY.cancelledTitle}
          claim={view.claim}
          body={<p>{DETAIL_COPY.cancelledBody}</p>}
          actions={
            <>
              <RecordButton variant="primary" onClick={() => navigate("/customer/catalog")}>
                {DETAIL_COPY.findMaster}
              </RecordButton>
              <RecordButton variant="soft" onClick={() => navigate("/customer/records")}>
                {DETAIL_COPY.toRecords}
              </RecordButton>
            </>
          }
        />
      </div>
    );
  }

  if (view.kind === "cancelling" || view.kind === "pending") {
    const waiting = view.kind === "cancelling";
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <h1 className="visually-hidden">Запись</h1>
        <RecordsState
          icon="clock"
          tone="info"
          title={waiting ? DETAIL_COPY.cancellingTitle : DETAIL_COPY.pendingTitle}
          body={<p>{waiting ? DETAIL_COPY.cancellingBody : DETAIL_COPY.pendingBody}</p>}
          actions={
            !waiting && (
              <>
                <RecordButton variant="primary" onClick={() => void awaitCancelled("pending")}>
                  {DETAIL_COPY.pendingRefresh}
                </RecordButton>
                <RecordButton variant="soft" onClick={() => navigate("/customer/records")}>
                  {DETAIL_COPY.toRecords}
                </RecordButton>
              </>
            )
          }
        >
          {summary}
        </RecordsState>
      </div>
    );
  }

  if (view.kind === "failed") {
    return (
      <div className="cr-screen cr-screen--nested">
        {topbar}
        <h1 className="visually-hidden">Запись</h1>
        <RecordsState
          icon="alert"
          tone="danger"
          title={DETAIL_COPY.failedTitle}
          body={<p>{DETAIL_COPY.failedBody}</p>}
          actions={
            <>
              <RecordButton variant="primary" onClick={() => setView({ kind: "confirm" })}>
                {RECORDS_ERROR_COPY.retry}
              </RecordButton>
              <RecordButton variant="soft" href={SUPPORT_DEEPLINK}>
                {RECORDS_ERROR_COPY.support}
              </RecordButton>
            </>
          }
        />
      </div>
    );
  }

  // --- карточка: кадры 3, 4, 13 -------------------------------------------

  const status = statusLine(b);
  // DRF-2172 — цена записи (снимок); ниже 1 ₽ / null → строки нет.
  const priceLabel = b.price ? priceFromLabel(b.price) : "";
  // Запись позади (визит прошёл или запись закрыта): её можно повторить, а
  // переносить и отменять нечего. Флаги сервера времени визита не учитывают —
  // у прошедшей подтверждённой записи они по-прежнему `true`.
  const over = isOver(b);
  const canReschedule = b.reschedulable && !over;
  const canCancel = b.cancellable && !over;
  // Шапка резервируется по самому наличию фото: иначе лист прыгал бы вниз,
  // когда фото догрузится, и кнопки уезжали бы из-под пальца.
  const hasHero = masterPhotoPath(b.master_photo_url) !== null;
  const time = [formatSlotTime(b.visit_at), b.duration_min != null && formatDuration(b.duration_min)]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className={`cr-screen cr-screen--nested${hasHero ? " cr-screen--hero" : ""}`}>
      {hasHero && (
        <div className="cr-hero">
          {/* Фото декоративно: мастер назван текстом ниже. */}
          {heroPhoto && <img className="cr-hero__img" src={heroPhoto} alt="" />}
        </div>
      )}
      <header className="cr-topbar cr-topbar--over">
        <button type="button" className="cr-back" aria-label="Назад" onClick={onBack}>
          <RecordIcon name="back" size={24} />
        </button>
        <CustomerAvatarEntry />
      </header>

      <main className="cr-sheet">
        <h1 className="cr-sheet__title">{b.service_name || "Запись"}</h1>

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

        <dl className="cr-facts">
          {/* DRF-1652 — «клиент записался и не видит, куда ехать». Строка
              БЕЗУСЛОВНА: при неизвестном адресе человеку сказано, у кого
              спросить. Три состояния разбирает `lib/visit-address`. */}
          <div className="cr-fact">
            <dt>
              <RecordIcon name="pin" />
              <span className="visually-hidden">Адрес</span>
            </dt>
            <dd>
              {b.salon_name && <div className="cr-fact__lead">{b.salon_name}</div>}
              <div>{visitAddressText(b.address)}</div>
            </dd>
          </div>
          <div className="cr-fact cr-fact--strong">
            <dt>
              <RecordIcon name="calendar" />
              <span className="visually-hidden">Дата</span>
            </dt>
            <dd>{formatDayWeekday(b.visit_at)}</dd>
          </div>
          <div className="cr-fact cr-fact--strong">
            <dt>
              <RecordIcon name="clock" />
              <span className="visually-hidden">Время</span>
            </dt>
            <dd>{time}</dd>
          </div>
          {priceLabel && (
            <div className="cr-fact cr-fact--strong">
              <dt>
                <RecordIcon name="price" />
                <span className="visually-hidden">Цена</span>
              </dt>
              <dd>{priceLabel}</dd>
            </div>
          )}
          {b.payment?.amount && (
            <div className="cr-fact">
              <dt>
                <RecordIcon name="price" />
                <span className="visually-hidden">Сумма</span>
              </dt>
              <dd>Сумма: {formatMoney(b.payment.amount)}</dd>
            </div>
          )}
          {b.rating != null && (
            <div className="cr-fact">
              <dt>
                <RecordIcon name="star" />
                <span className="visually-hidden">Оценка</span>
              </dt>
              <dd>Оценка: {b.rating} из 5</dd>
            </div>
          )}
        </dl>

        {/* C7.3 — статус оплаты, когда провод его несёт. */}
        <PaymentStatusBadge state={b.payment?.capture_state} />

        {status && (
          <p className="cr-statusline">
            <RecordIcon name="status" />
            {status}
          </p>
        )}

        {canCancel && !canReschedule && (
          <p className="cr-notice">
            <RecordIcon name="info" />
            <span>{DETAIL_COPY.noReschedule}</span>
          </p>
        )}

        <div className="cr-actions" ref={cancelTrigger}>
          {canReschedule && (
            <RecordButton
              variant="primary"
              onClick={() => navigate(`/customer/records/${b.id}/reschedule`)}
            >
              {DETAIL_COPY.reschedule}
            </RecordButton>
          )}
          {over && (
            <RecordButton variant="primary" onClick={() => navigate("/customer/catalog")}>
              {DETAIL_COPY.repeat}
            </RecordButton>
          )}
          {b.can_rate && (
            <RecordButton variant="soft" icon="star" onClick={() => navigate(`/feedback/${b.id}`)}>
              {DETAIL_COPY.rate}
            </RecordButton>
          )}
          {canCancel && (
            <RecordButton variant="danger" onClick={() => setView({ kind: "confirm" })}>
              {DETAIL_COPY.cancel}
            </RecordButton>
          )}
        </div>
      </main>

      {/* Кадр 8 — подтверждение отмены на весь экран. */}
      {view.kind === "confirm" && (
        <div
          ref={dialogRef}
          role="dialog"
          aria-modal="true"
          aria-label={DETAIL_COPY.cancel}
          className="cr-dialog"
        >
          <RecordsState
            icon="alert"
            tone="danger"
            title={DETAIL_COPY.confirmTitle}
            inDialog
            body={
              <p>
                {b.undo_window_seconds === 0 && `${DETAIL_COPY.confirmFinal} `}
                {DETAIL_COPY.confirmAgain}
              </p>
            }
            actions={
              <>
                <RecordButton variant="danger" onClick={() => void onConfirmCancel()} disabled={busy}>
                  {busy ? DETAIL_COPY.confirmBusy : DETAIL_COPY.confirmYes}
                </RecordButton>
                <RecordButton variant="text" onClick={closeConfirm} disabled={busy}>
                  {DETAIL_COPY.confirmNo}
                </RecordButton>
              </>
            }
          >
            {summary}
            {/* Причину отмены читает только локальный путь; на пути Ayla тело
                запроса не разбирается, и спрашивать там было бы вводом в
                никуда. Признак локального пути — окно возврата. */}
            {b.undo_window_seconds > 0 && (
              <div className="cr-reasons">
                <p className="cr-reasons__title">{DETAIL_COPY.reasons}</p>
                <div className="chip-row">
                  {REASON_CHIPS.map((r) => (
                    <button
                      key={r.value}
                      type="button"
                      aria-pressed={reasonClass === r.value}
                      className={`chip ${reasonClass === r.value ? "chip--active" : ""}`}
                      onClick={() => setReasonClass((cur) => (cur === r.value ? null : r.value))}
                    >
                      {r.label}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </RecordsState>
        </div>
      )}

      <Snackbar
        visible={snack !== null}
        message={snack?.message ?? ""}
        actionLabel={snack?.undo ? "Отменить" : undefined}
        onAction={snack?.undo ? onUndo : undefined}
        durationMs={5000}
        onTimeout={snack?.undo ? onUndoWindowElapsed : () => setSnack(null)}
        onDismiss={() => setSnack(null)}
      />
    </div>
  );
}
