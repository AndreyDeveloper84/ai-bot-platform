/**
 * Куда возвращают вложенные экраны салона — закрытым списком (DRF-2933).
 *
 * Адрес возврата приходит снаружи: из состояния маршрута (`state.from`) или
 * из параметров ссылки (`?return=…`). Подставляемый адрес — это чужая
 * строка, решающая, куда уйдёт человек, поэтому ни карточка записи, ни
 * форма новой записи не идут «куда сказано»: они узнают один из известных
 * видов адреса и собирают его заново из проверенных частей.
 *
 * С v2 «Расписания» адрес несёт дату (`/admin/schedule?date=…`) и бывает
 * днём мастера (`/admin/schedule/master/<id>?date=…`): человек ушёл в
 * запись со вторника — и вернуться должен во вторник, а не в «сегодня».
 */
import { dateParam, scheduleMasterPath, schedulePath } from "./salon-schedule";

/** Id мастера в адресе — то, что бывает первичным ключом, и ничего больше. */
const MASTER_ID = /^[0-9A-Za-z_-]{1,64}$/;

const SCHEDULE = /^\/admin\/schedule(?:\?date=(\d{4}-\d{2}-\d{2}))?$/;
const SCHEDULE_MASTER = /^\/admin\/schedule\/master\/([0-9A-Za-z_-]{1,64})\?date=(\d{4}-\d{2}-\d{2})$/;
/**
 * День «Рабочего времени» (DRF-2936): из конфликта с записями человек уходит
 * в карточку записи и возвращается к своему дню, где ждёт черновик.
 */
const WORKING_TIME_DAY = /^\/admin\/working-time\/[0-9A-Za-z%_-]+\/\d{4}-\d{2}-\d{2}$/;

/**
 * Возврат из карточки записи. Неизвестный или испорченный адрес —
 * «Расписание» без даты, как было до v2.
 */
export function bookingDetailBack(state: unknown): string {
  const from = (state as { from?: unknown } | null | undefined)?.from;
  if (typeof from !== "string") return schedulePath(null);
  if (from === "/admin/today" || WORKING_TIME_DAY.test(from)) return from;

  const master = SCHEDULE_MASTER.exec(from);
  if (master) {
    const date = dateParam(master[2] ?? null);
    if (date && master[1]) return scheduleMasterPath(master[1], date);
  }
  const schedule = SCHEDULE.exec(from);
  if (schedule) return schedulePath(dateParam(schedule[1] ?? null));
  return schedulePath(null);
}

/**
 * Возврат из формы новой записи, открытой с «Расписания»
 * (`?return=schedule|schedule-master&date=…&master_id=…`). `null` — это не
 * возврат в расписание, решает вызывающий.
 *
 * Дата и мастер — те же параметры, которыми форма предзаполнена: отдельного
 * «адреса возврата» в ссылке нет, подменять нечего.
 */
export function scheduleReturnPath(params: URLSearchParams): string | null {
  const target = params.get("return");
  if (target !== "schedule" && target !== "schedule-master") return null;
  const date = dateParam(params.get("date"));
  const masterId = params.get("master_id") ?? "";
  if (target === "schedule-master" && date && MASTER_ID.test(masterId)) {
    return scheduleMasterPath(masterId, date);
  }
  return schedulePath(date);
}
