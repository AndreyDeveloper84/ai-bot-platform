/**
 * Разбор дня салона для экрана «Сегодня» (DRF-1236).
 *
 * # Почему отдельный модуль, а не внутри экрана
 *
 * Здесь три решения, каждое из которых легко испортить незаметно:
 * какие записи считаются идущими сейчас, какие — ближайшими, и в каком
 * часовом поясе печатается время. Ошибка в любом из них выглядит как
 * работающий экран: список не пуст, время нарисовано, ничего не падает.
 * Поэтому логика вынесена из компонента и проверяется на данных, а не
 * через рендер.
 *
 * # «Сейчас» считает сервер, а не устройство
 *
 * `is_in_progress` приходит в полезной нагрузке
 * (`apps/admin_api/services/salon_day.py`, `_build_visit`) и означает
 * ровно то, что зафиксировано в DRF-1236: `start_at <= now < end_at`.
 * Это НЕ статус жизненного цикла, не «клиент пришёл» и не
 * `in_progress` — на макете это зелёная полоса слева, и других смыслов
 * у неё нет.
 *
 * Пересчитывать это на телефоне было бы хуже вдвойне: часы устройства
 * могут врать, а «сегодня» у салона своё (см. `timezone` в ответе).
 *
 * # А «Дальше» — сравнение мгновений, и это не то же самое
 *
 * У «ближайших» серверного признака нет, и здесь без часов не обойтись.
 * Сравниваются АБСОЛЮТНЫЕ мгновения (`Date.parse` от ISO с зоной), а не
 * календарные даты, поэтому пояс устройства на результат не влияет:
 * 10:30 MSK — одно и то же мгновение, из какого пояса на него ни смотри.
 * Опасность, от которой предостерегает `SalonPilotFrame`, — считать на
 * устройстве, КАКОЙ сегодня день; границу суток здесь никто не считает,
 * её уже посчитал сервер, отдав день целиком.
 *
 * Момент отсчёта передаётся аргументом, а не берётся из `Date.now()`
 * внутри: иначе это не проверить.
 */

import {
  RELEASED_VISIT_STATUSES,
  type SalonDayResponse,
  type SalonDayVisit,
} from "./admin-api";

/**
 * Запись вместе с мастером, у которого она стоит.
 *
 * В ответе записи разложены по мастерам, а «Сейчас» и «Дальше» — плоские
 * списки по времени: на макете в строке записи стоит имя мастера, а не
 * колонка. `masterName` пуст у «ничьих» записей (`orphan_visits` —
 * специалист не сошёлся ни с одним мастером каталога). Пустая строка
 * здесь честнее выдуманного имени: экран просто не печатает мастера.
 */
export interface DayRow {
  visit: SalonDayVisit;
  masterName: string;
}

/** Мастер в блоке «Мастера сегодня» — ровно то, что есть в ответе. */
export interface MasterRow {
  masterId: string;
  name: string;
  /**
   * Сколько записей у него на этот день, БЕЗ освобождённых.
   *
   * Отменённая запись и неявка — это освободившийся слот: мастер на них
   * не занят. Считать их значило бы поставить над списком число, которое
   * ни с чем на экране не сходится и которое администратору нечем
   * проверить: экран освобождённых слотов не показывает, а пилотный
   * «Расписание» до DRF-1237 пуст.
   *
   * Закрытые записи считаются: они СОСТОЯЛИСЬ, это часть дня мастера.
   */
  visitCount: number;
}

/** Записи «Дальше», которые экран показывает до нажатия «ещё». */
export const NEXT_PREVIEW_LIMIT = 3;

/** Завершённая запись: состоялась. Не «Дальше» и не «Сейчас». */
const COMPLETED_STATUS = "completed";

/**
 * Пояс на случай, когда браузер не знает присланного сервером.
 *
 * Тот же, что у сервера (`DEFAULT_TZ`, `apps/admin_api/services/salon_day.py`)
 * — не пояс устройства.
 */
const FALLBACK_TZ = "Europe/Moscow";

function allRows(day: SalonDayResponse): DayRow[] {
  const rows: DayRow[] = [];
  for (const master of day.masters) {
    for (const visit of master.visits) {
      rows.push({ visit, masterName: master.name });
    }
  }
  // Записи без мастера не выбрасываются: запись, которую никто не видит,
  // — ровно тот отказ, ради которого салонная поверхность и строилась
  // (`SalonDay.orphan_visits`).
  for (const visit of day.orphan_visits) {
    rows.push({ visit, masterName: "" });
  }
  return rows;
}

function startMs(visit: SalonDayVisit): number {
  if (!visit.start_at) return Number.NaN;
  return Date.parse(visit.start_at);
}

function byStart(a: DayRow, b: DayRow): number {
  const left = startMs(a.visit);
  const right = startMs(b.visit);
  if (Number.isNaN(left)) return Number.isNaN(right) ? 0 : 1;
  if (Number.isNaN(right)) return -1;
  return left - right;
}

/**
 * Что показывать в «Сейчас» — записи, идущие в этот момент.
 *
 * # Почему статуса мало и признака тоже мало
 *
 * Отменённые сюда попасть не могут: сервер снимает `is_in_progress` с
 * освобождённых слотов. Проверка всё равно стоит — признак и статус
 * приходят двумя полями, и разъехаться они могут только здесь.
 *
 * А вот ЗАКРЫТЫЕ записи сервер не снимает, и это не мелочь.
 * `is_in_progress` считается как «статус не в `RELEASED_STATUSES` и
 * время накрывает сейчас» (`apps/admin_api/services/salon_day.py`,
 * `_build_visit`), а `RELEASED_STATUSES` — это ровно `("cancelled",
 * "no_show")` (`apps/master_api/services/visit_source.py`). `completed`
 * туда не входит. Значит, визит, закрытый администратором до конца
 * интервала — а закрывают его именно так, «когда клиент уходит», —
 * продолжает приходить с `is_in_progress: true`.
 *
 * Показать его в «Сейчас» с зелёной полосой значило бы сказать «идёт»
 * про то, что уже состоялось. Поэтому закрытые отсекаются здесь — так
 * же, как в `visitsNext`.
 */
export function visitsNow(day: SalonDayResponse): DayRow[] {
  return allRows(day)
    .filter(
      (row) =>
        row.visit.is_in_progress &&
        row.visit.status !== COMPLETED_STATUS &&
        !RELEASED_VISIT_STATUSES.has(row.visit.status),
    )
    .sort(byStart);
}

/**
 * Что показывать в «Дальше» — ещё не начавшиеся записи, по времени.
 *
 * Отменённые и не пришедшие исключены: слот освободился, ждать на него
 * некого. Уже закрытые — тоже: они состоялись. Начавшиеся, но не
 * закрытые, не попадают ни сюда, ни в «Сейчас», если сервер не считает
 * их идущими, — и это верно: их время прошло, а «Дальше» обещает
 * будущее.
 */
export function visitsNext(day: SalonDayResponse, nowMs: number): DayRow[] {
  return allRows(day)
    .filter((row) => {
      const { visit } = row;
      if (visit.is_in_progress) return false;
      if (RELEASED_VISIT_STATUSES.has(visit.status)) return false;
      if (visit.status === COMPLETED_STATUS) return false;
      const start = startMs(visit);
      return !Number.isNaN(start) && start > nowMs;
    })
    .sort(byStart);
}

/**
 * Мастера дня — имя и число записей.
 *
 * Рабочих часов, кабинета, фотографии и недоступности здесь нет, потому
 * что их нет в ответе: `DayMaster` несёт `master_id`, `name`,
 * `is_active` и `visits`, и всё. Макет обещает часы («09:00 – 14:00») и
 * «Недоступна 13:00 – 15:00» — это отдельная задача на данные, а не то,
 * что можно достроить здесь.
 *
 * `is_active` намеренно НЕ превращается в «работает сегодня»: это
 * признак карточки мастера в каталоге, а не смены. Мастер может быть
 * активен и не выйти, и наоборот. Пересказать одно другим значило бы
 * выдать догадку за факт.
 */
export function mastersToday(day: SalonDayResponse): MasterRow[] {
  return day.masters
    .filter(
      // Отключённая карточка без единой записи на сегодня к сегодняшнему
      // дню отношения не имеет: `build_salon_day` отдаёт всех
      // неархивированных мастеров, а `is_active: false` — это выключенная
      // карточка в каталоге. Но если у неё ЕСТЬ записи, мастер остаётся в
      // списке: скрыть строку, за которой стоят живые записи, значило бы
      // спрятать день, а не убрать шум.
      (master) => master.is_active || master.visits.length > 0,
    )
    .map((master) => ({
      masterId: master.master_id,
      name: master.name,
      visitCount: master.visits.filter(
        (visit) => !RELEASED_VISIT_STATUSES.has(visit.status),
      ).length,
    }));
}

/**
 * `09:00` в поясе САЛОНА, а не устройства.
 *
 * Пояс приходит с днём (`SalonDayResponse.timezone`). Взять пояс
 * телефона значило бы показать администратору, улетевшему в другой
 * часовой пояс, чужое время его же салона.
 */
export function formatTime(iso: string | null, timeZone: string): string {
  if (!iso) return "";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "";
  try {
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone,
    }).format(dt);
  } catch {
    // Пояс, которого не знает браузер, не должен ронять экран целиком —
    // но и печатать время в поясе УСТРОЙСТВА нельзя: получилось бы
    // правдоподобное и неверное время без единого признака подмены.
    // Поэтому запасной вариант тот же, что у сервера (`DEFAULT_TZ` в
    // `apps/admin_api/services/salon_day.py`), а не пояс телефона.
    return new Intl.DateTimeFormat("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: FALLBACK_TZ,
    }).format(dt);
  }
}

/**
 * `09:00 – 10:00`, а при неизвестном конце — просто `09:00`.
 *
 * Интервал, а не одно время: на макете у идущей записи видно, когда
 * мастер освободится, и это половина смысла блока «Сейчас».
 */
export function formatRange(visit: SalonDayVisit, timeZone: string): string {
  const from = formatTime(visit.start_at, timeZone);
  const to = formatTime(visit.end_at, timeZone);
  if (!from) return "";
  return to ? `${from} – ${to}` : from;
}

/** `Анна П.`, а без имени — `Гость`. Телефона нет нигде (DRF-1039). */
export function clientLabel(visit: SalonDayVisit): string {
  const initial = visit.client_last_initial ? ` ${visit.client_last_initial}` : "";
  return `${visit.client_first_name}${initial}`.trim() || "Гость";
}

/** Буква для кружка-аватара. Фотографий мастеров в ответе нет. */
export function masterInitial(name: string): string {
  return (name || "").trim().charAt(0).toUpperCase();
}

/** `3 записи` — с русским склонением, чтобы строка читалась, а не считалась. */
export function visitCountLabel(count: number): string {
  if (count === 0) return "Записей нет";
  const tens = count % 100;
  const ones = count % 10;
  if (tens >= 11 && tens <= 14) return `${count} записей`;
  if (ones === 1) return `${count} запись`;
  if (ones >= 2 && ones <= 4) return `${count} записи`;
  return `${count} записей`;
}
