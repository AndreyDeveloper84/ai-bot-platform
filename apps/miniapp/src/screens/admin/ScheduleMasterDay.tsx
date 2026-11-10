/**
 * День одного мастера в «Расписании» салона v2 (DRF-2933, кадр 12 макета
 * DRF-2854): записи, свободные окна, блоки, выходной, конфликты.
 *
 * Один компонент на два места — экран `/admin/schedule` с одним выбранным
 * мастером и вложенный экран «Мастер», — чтобы день читался одинаково.
 *
 * # Откуда что берётся
 *
 * Записи — из дня салона (их передаёт экран): те же, что в сетке и ленте.
 * Часы, блоки, свободные окна и конфликты — из `day-schedule` (`phase`).
 * Пока он не пришёл или не пришёл вовсе, записи всё равно показаны, а про
 * остальное сказано словами.
 *
 * # Когда окно — действие
 *
 * Окно ведёт в ручную запись, если есть сеть, ответ свежий (пять минут) и
 * мастер включён. Иначе: окно видно, но не нажимается, либо — если ответ
 * устарел к моменту нажатия — экран говорит «расписание могло измениться» и
 * просит обновить (заморозка DRF-2854, MUST FIX 7). Ни записи, ни времени
 * отсюда не создаётся: окно — контекст для формы (MUST FIX 5).
 *
 * # Подтверждение графика
 *
 * Свободные окна стоят на часах мастера, а показанные часы и поручительство
 * салона за них — разные утверждения (§29.5). Поэтому состояние
 * подтверждения остаётся строкой и здесь; слова — из общего словаря
 * `schedule-confirmation-state`.
 */
import { SYSTEM_STATE_COPY } from "../../components/OperationalSystemState";
import type { MasterDayPhase } from "../../hooks/useSalonSchedule";
import {
  SS_COPY,
  freeWindowsOf,
  liveWindows,
  masterDayOff,
  salonNow,
  windowsFresh,
  type ColumnVisit,
  type FreeWindow,
} from "../../lib/salon-schedule";
import { confirmationLabel, confirmationState } from "../../lib/schedule-confirmation-state";
import { MasterDayList } from "./ScheduleParts";

export function MasterDayPanel({
  phase,
  date,
  timezone,
  visits,
  serviceIds,
  salonClosed,
  masterActive,
  online,
  onVisit,
  onBook,
  onBlock,
  onStale,
  onRetry,
}: {
  phase: MasterDayPhase;
  date: string;
  /** Пояс салона из ответа дня — по нему считается «сейчас». */
  timezone: string;
  visits: readonly ColumnVisit[];
  serviceIds: readonly string[];
  /** В этот день есть закрытие салона — окна не показываются. */
  salonClosed: boolean;
  masterActive: boolean;
  online: boolean;
  onVisit: (id: string) => void;
  onBook: (window: FreeWindow) => void;
  onBlock: () => void;
  /** Окно устарело к моменту нажатия. */
  onStale: () => void;
  onRetry: () => void;
}) {
  const ready = phase.kind === "ready" ? phase : null;
  const day = ready?.day ?? null;
  const now = salonNow(timezone);
  const windows = ready ? liveWindows(freeWindowsOf(day), date, now, salonClosed) : [];
  const off = day ? masterDayOff(day) : false;

  let windowsNote: string | null = null;
  if (ready && day && !off) {
    if (salonClosed) windowsNote = SS_COPY.closedNoWindows;
    else if (date < now.ymd) windowsNote = null;
    else windowsNote = windows.length > 0 ? SS_COPY.freeNote : SS_COPY.noFreeWindows;
  }

  const book = (window: FreeWindow) => {
    if (!ready || !windowsFresh(ready.fetchedAt)) {
      onStale();
      return;
    }
    onBook(window);
  };

  return (
    <div className="ss-panel">
      {ready ? (
        ready.confirmationUnread ? (
          <p className="ss-confirmation">{SS_COPY.confirmationUnread}</p>
        ) : (
          <p className="ss-confirmation" data-state={confirmationState(ready.confirmation)}>
            {confirmationLabel(ready.confirmation)}
          </p>
        )
      ) : null}

      <MasterDayList
        visits={visits}
        day={day}
        windows={windows}
        windowsNote={windowsNote}
        serviceIds={serviceIds}
        bookable={online && masterActive}
        onVisit={onVisit}
        onBook={book}
        onBlock={onBlock}
      />

      {phase.kind === "loading" ? (
        <p className="ss-note" role="status">
          {SS_COPY.freeLoading}
        </p>
      ) : null}

      {/* Сервер не ответил или ответил без этого дня — одно и то же для
          человека: часов, блоков и окон нет, и это надо сказать, а не
          показать записи так, будто больше ничего и не было. */}
      {phase.kind === "error" || (ready && !day) ? (
        <div className="ss-inline-error" role="alert">
          <p className="ss-note">{SS_COPY.dayPartsFailed}</p>
          <button type="button" className="btn-secondary wt-btn" onClick={onRetry}>
            {SYSTEM_STATE_COPY.load_error.initial.retry}
          </button>
        </div>
      ) : null}
    </div>
  );
}
