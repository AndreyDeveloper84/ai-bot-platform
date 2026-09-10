/**
 * «Расписание» — второй раздел пилотной салонной админки (DRF-1235, DRF-1237).
 *
 * Адрес: `/admin/schedule`. Два режима:
 *
 * * **один мастер** (срез A1) — выбрали человека, видите его рабочий день;
 * * **все** (срез A2) — хронология салона: кто когда занят и кто когда в смене.
 *
 * В режиме одного мастера рядом с днём читается его недельный график и
 * состояние подтверждения (срез B1). Это не украшение: §29.5 запрещает
 * считать неизвестное расписание свободным или занятым, а показанные часы и
 * поручительство салона за них — разные утверждения. Подтверждать отсюда
 * нельзя, только смотреть: подтверждение — решение владелицы (§83) и
 * отдельная поверхность.
 *
 * # Что здесь появилось и почему только сейчас
 *
 * До этого экран честно писал «показывать нечего», и это было верно: ручки,
 * отдающей рамку доступности, в `apps/admin_api/` не было. День салона
 * (`GET /api/v1/admin/day/`) отдаёт визиты и только визиты — ни смены, ни
 * исключений по датам, ни отгулов в нём нет, и «рабочий день мастера» из
 * него не строится.
 *
 * Теперь есть две: `GET /api/v1/admin/masters/<id>/day-schedule/` — тонкий
 * вид поверх `master_api.services.schedule.build_schedule`, и
 * `GET /api/v1/admin/day/frame/` — кадр всего салона одним вызовом Ayla.
 *
 * # Экран НЕ считает доступность
 *
 * Ни рабочие окна, ни занятость. Всё приходит посчитанным с сервера, из
 * авторитетного источника. Взять смену, вычесть визиты и нарисовать окна
 * было бы быстро и выглядело бы верно — и было бы «клиент выдумывает
 * доступность» (§17). В продукте уже три вычислителя «свободного времени», и
 * они между собой расходятся (DRF-1637); четвёртый здесь был бы худшим из
 * возможных решений.
 *
 * Поэтому в режиме «Все» свободных окон нет вовсе. Хронология показывает
 * занятое и смену — то, что посчитано не нами; вычесть одно из другого и
 * назвать остаток свободным значило бы завести тот самый четвёртый счёт.
 *
 * # Два ответа, а не два дня
 *
 * Режим «Все» складывает ДВА источника: визиты из `getSalonDay()` (зеркало
 * `RemoteBookingProxy`) и кадр из `getSalonDayFrame()` (Ayla). Разделение
 * не случайное: визиты обязаны совпадать с тем, что видит мастер у себя, а
 * это зеркало; смены и перерывы зеркало не несёт вовсе.
 *
 * # Известная слепота, о которой экран не молчит
 *
 * **Режим одного мастера.** Обеденный перерыв в свободные окна протекает:
 * провод Ayla его несёт, разбор кадра роняет (DRF-1638). Экран **не чинит
 * это своими руками** — он подписывает свободные окна так, чтобы
 * администратор не принял их за гарантию. Формулировка соответствует канону:
 * «свободный интервал — это диапазон доступности, а не готовый слот», и
 * окончательную проверку делает создание записи (409 при конфликте).
 *
 * **Кабинета в карточке нет, и это предел данных, а не решение.**
 * Замороженный UX (DRF-1237) просит показывать «мастер и кабинет как
 * операционный контекст». Мастера показываем, кабинет — нет: его не несёт ни
 * день салона, ни кадр. Придумать его нечем, а тихо промолчать значило бы
 * выдать неполную карточку за полную.
 *
 * **«Найти время для записи» здесь тоже нет, и это не забывчивость.**
 * Заморозка называет такое действие, но многомастерного поиска доступности
 * не существует структурно: `specialist_id` — скаляр и в DTO, и в протоколе
 * провайдера (амендмент DRF-1237 от 23.08). Кнопка, зовущая несуществующий
 * контракт, — худший вид обещания.
 *
 * **Режим «Все».** Форма непустой строки `breaks` не проверена ничем —
 * перерывов не завёл никто, и на проводе они пусты у всех. Поэтому сервер
 * отвечает состоянием (`parsed` / `none` / `absent` / `unreadable`), а экран
 * обязан сказать словами, когда список не разобран: молчаливая пустота
 * прочиталась бы как «перерывов нет», и первый салон с обедом получил бы его
 * показанным рабочим временем.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { StateError } from "../../components/StateError";
import {
  getMasterDaySchedule,
  getMasterSchedule,
  getSalonDay,
  getSalonDayFrame,
  RELEASED_VISIT_STATUSES,
  type MasterDay,
  type MasterSchedule,
  type MeResponse,
  type SalonDayFrame,
  type SalonDayResponse,
  type SalonDayVisit,
} from "../../lib/admin-api";
import {
  confirmationLabel,
  confirmationState,
} from "../../lib/schedule-confirmation-state";
import { SalonPilotFrame } from "./SalonPilotFrame";

/** Сегодня в местном исчислении браузера — та же дата, что подставит сервер. */
function today(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** «10:00» из ISO-времени визита. */
function hhmm(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${`${d.getHours()}`.padStart(2, "0")}:${`${d.getMinutes()}`.padStart(2, "0")}`;
}

const BLOCK_REASON: Record<string, string> = {
  lunch: "перерыв",
  vacation: "отпуск",
  sick: "больничный",
  personal: "личное",
  other: "недоступна",
};

/**
 * Отметка «сейчас идёт по плану» — утверждённая семантика DRF-1237.
 *
 * Правило заморожено дословно: `scheduled_start <= now < scheduled_end`, и
 * сервер считает его сам (`salon_day.py`: тот же предикат плюс отсев
 * released). Клиент не пересчитывает — он бы завёл своё «сейчас».
 *
 * Формулировка осторожная НАРОЧНО. Поле называется `is_in_progress`, и имя
 * подталкивает написать «идёт», но заморозка это прямо запрещает: индикатор
 * «не создаёт новый Appointment status и не означает приход клиента / оплату
 * / in_progress». Он говорит ровно одно — плановый интервал накрывает
 * текущее время. Клиент мог не прийти, и экран этого не знает.
 */
const NOW_MARKER = " · сейчас по плану";

const WEEKDAYS_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

const LIST_TITLE: Record<string, string> = {
  working_intervals: "смены",
  breaks: "перерывы",
  absences: "отсутствия",
};

type Mode = "one" | "all";

interface TimelineEntry {
  masterName: string;
  visit: SalonDayVisit;
}

/**
 * Визиты всего салона, сгруппированные по времени начала.
 *
 * Это перегруппировка показанного, а не расчёт: ни одна минута здесь не
 * складывается и не вычитается. Ради «в 14:00 заняты трое» администратору
 * иначе пришлось бы читать четыре колонки одновременно.
 */
function groupByStart(salon: SalonDayResponse): Array<[string, TimelineEntry[]]> {
  const buckets = new Map<string, TimelineEntry[]>();
  for (const master of salon.masters) {
    for (const visit of master.visits) {
      const key = visit.start_at ? hhmm(visit.start_at) : "—";
      const list = buckets.get(key) ?? [];
      list.push({ masterName: master.name, visit });
      buckets.set(key, list);
    }
  }
  return [...buckets.entries()].sort(([a], [b]) => a.localeCompare(b));
}

function shiftLine(master: SalonDayFrame["masters"][number]): string {
  if (!master.is_working_day) return "не работает";
  const wi = master.working_intervals;
  if (wi.state === "parsed") {
    return wi.rows.map((r) => `${r.start}–${r.end}`).join(", ");
  }
  // Остальные три состояния не выдаём за смену: «пусто» и «не разобрал» тут
  // одинаково означают, что часов мы не знаем, и говорить это надо словами.
  return "часы неизвестны";
}

export function SalonPilotScheduleScreen({ me }: { me: MeResponse }) {
  const [mode, setMode] = useState<Mode>("one");
  const [salon, setSalon] = useState<SalonDayResponse | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [day, setDay] = useState<MasterDay | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [err, setErr] = useState<unknown>(null);
  const [frame, setFrame] = useState<SalonDayFrame | null>(null);
  const [frameErr, setFrameErr] = useState<unknown>(null);
  const [week, setWeek] = useState<MasterSchedule | null>(null);
  const [weekErr, setWeekErr] = useState<unknown>(null);

  const masters = salon?.masters ?? null;

  // Список мастеров берётся из дня салона: там ВСЕ неархивные мастера, а не
  // только занятые (`salon_day.py` фильтрует по `archived_at IS NULL`).
  // Свободный мастер обязан быть в выборе — иначе именно его расписание и
  // нельзя открыть.
  useEffect(() => {
    const ctrl = new AbortController();
    getSalonDay(undefined, { signal: ctrl.signal })
      .then((res) => {
        if (ctrl.signal.aborted) return;
        setSalon(res);
        const first = res.masters[0];
        if (first) setSelected(first.master_id);
      })
      .catch((e) => {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setErr(e);
      });
    return () => ctrl.abort();
  }, []);

  const load = useCallback(
    async (masterId: string, signal?: AbortSignal) => {
      if (!masterId) return;
      setLoading(true);
      setErr(null);
      try {
        const date = today();
        const res = await getMasterDaySchedule(masterId, { from: date, to: date }, { signal });
        if (signal?.aborted) return;
        setDay(res.days[0] ?? null);
      } catch (e) {
        if ((e as DOMException | undefined)?.name === "AbortError") return;
        setErr(e);
        setDay(null);
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [],
  );

  const loadFrame = useCallback(async (signal?: AbortSignal) => {
    setFrameErr(null);
    try {
      const res = await getSalonDayFrame(undefined, { signal });
      if (signal?.aborted) return;
      setFrame(res);
    } catch (e) {
      if ((e as DOMException | undefined)?.name === "AbortError") return;
      setFrameErr(e);
      // Тот же приём, что в режиме одного мастера: устаревший кадр не
      // переживает отказ. Иначе администратор увидит вчерашние смены под
      // сегодняшней датой.
      setFrame(null);
    }
  }, []);

  const loadWeek = useCallback(async (masterId: string, signal?: AbortSignal) => {
    if (!masterId) return;
    setWeekErr(null);
    try {
      const res = await getMasterSchedule(masterId, { signal });
      if (signal?.aborted) return;
      setWeek(res);
    } catch (e) {
      if ((e as DOMException | undefined)?.name === "AbortError") return;
      setWeekErr(e);
      // Чужой график не переживает переключение мастера — то же правило, что
      // для дня и для кадра.
      setWeek(null);
    }
  }, []);

  useEffect(() => {
    if (mode !== "one") return;
    const ctrl = new AbortController();
    void load(selected, ctrl.signal);
    void loadWeek(selected, ctrl.signal);
    return () => ctrl.abort();
  }, [mode, selected, load, loadWeek]);

  useEffect(() => {
    if (mode !== "all") return;
    const ctrl = new AbortController();
    void loadFrame(ctrl.signal);
    return () => ctrl.abort();
  }, [mode, loadFrame]);

  const timeline = useMemo(() => (salon ? groupByStart(salon) : []), [salon]);

  return (
    <SalonPilotFrame me={me} title="Расписание">
      {masters !== null && masters.length === 0 && (
        <div className="callout" role="status">
          <p style={{ margin: 0 }}>В салоне нет мастеров — расписание показывать некому.</p>
        </div>
      )}

      {masters !== null && masters.length > 0 && (
        <div style={{ marginBottom: "var(--s-3)" }}>
          <div role="tablist" aria-label="Режим" style={{ marginBottom: "var(--s-2)" }}>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "one"}
              onClick={() => setMode("one")}
            >
              Один мастер
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "all"}
              onClick={() => setMode("all")}
            >
              Все
            </button>
          </div>

          {mode === "one" && (
            <>
              <label
                htmlFor="salon-schedule-master"
                style={{ display: "block", marginBottom: "var(--s-1)" }}
              >
                Мастер
              </label>
              <select
                id="salon-schedule-master"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
              >
                {masters.map((m) => (
                  <option key={m.master_id} value={m.master_id}>
                    {m.name}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      )}

      {mode === "one" && loading && !day && <p>Загружаю день…</p>}

      {mode === "one" && err != null && (
        // Названная причина, а не пустой день: пустой день читался бы как
        // «мастер свободен весь день», и администратор предложил бы клиенту
        // время, которого нет.
        <StateError err={err} onRetry={() => void load(selected)} />
      )}

      {mode === "one" && day && err == null && (
        <>
          <h2 style={{ fontSize: "var(--text-h3-size, 18px)", margin: "0 0 var(--s-2)" }}>
            {day.is_off_day || !day.working_hours
              ? "Сегодня не работает"
              : `Смена ${day.working_hours.start}–${day.working_hours.end}`}
          </h2>

          {/*
            Часы выше и поручительство за них — РАЗНЫЕ утверждения (§29.5).
            Показать смену молча значило бы выдать её за заверенную, а
            «заглушка 10:00–19:00 подтверждением не является».

            Состояний четыре, и ни одно не сворачивается в остальные:
            подтверждено для этих часов, подтверждено для других (часы
            изменились), не подтверждено никогда — и НЕ ПРОЧИТАНО, когда
            запрос не дошёл. Последнее не то же самое, что «не подтверждено»:
            неизвестное нельзя подставлять значением по умолчанию ни в одну
            сторону.
          */}
          {weekErr != null ? (
            <p style={{ margin: "0 0 var(--s-2)", color: "var(--c-text-secondary)" }}>
              Состояние подтверждения не прочитано — это не значит «не подтверждено».
            </p>
          ) : (
            week != null && (
              <p
                style={{ margin: "0 0 var(--s-2)", color: "var(--c-text-secondary)" }}
                data-state={confirmationState(week.confirmation)}
              >
                {confirmationLabel(week.confirmation)}
              </p>
            )
          )}

          <section style={{ marginBottom: "var(--s-3)" }}>
            <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
              Записи
            </h3>
            {day.bookings.length === 0 ? (
              <p style={{ margin: 0, color: "var(--c-text-secondary)" }}>Записей нет.</p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {day.bookings.map((b) => (
                  <li key={b.booking_id} style={{ padding: "2px 0" }}>
                    {`${hhmm(b.visit_at)} · ${b.service_name} · ${b.client_first_name} ${b.client_last_initial}`}
                    {b.is_in_progress && NOW_MARKER}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {day.blocks.length > 0 && (
            <section style={{ marginBottom: "var(--s-3)" }}>
              <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
                Недоступность
              </h3>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {day.blocks.map((bl) => (
                  <li key={bl.exception_id} style={{ padding: "2px 0" }}>
                    {`${hhmm(bl.start)}–${hhmm(bl.end)} · ${BLOCK_REASON[bl.reason] ?? bl.reason}`}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {day.conflicts.length > 0 && (
            // Конфликт показывается, а не прячется: запись вне часов или
            // поверх недоступности существует законно (салонная и уличная
            // запись создаются вне рамки намеренно), и администратор должен
            // видеть её, а не гадать, почему день выглядит странно.
            <section style={{ marginBottom: "var(--s-3)" }}>
              <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
                Требует внимания
              </h3>
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {day.conflicts.map((c) => (
                  <li key={`${c.type}-${c.booking_id}`} style={{ padding: "2px 0" }}>
                    {c.description}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section>
            <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
              Свободное время
            </h3>
            {day.free_windows.length === 0 ? (
              <p style={{ margin: 0, color: "var(--c-text-secondary)" }}>
                Свободных промежутков нет.
              </p>
            ) : (
              <>
                <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                  {day.free_windows.map((w) => (
                    <li key={`${w.start}-${w.end}`} style={{ padding: "2px 0" }}>
                      {`${w.start}–${w.end} · ${w.duration_min} мин`}
                    </li>
                  ))}
                </ul>
                <p style={{ margin: "var(--s-1) 0 0", color: "var(--c-text-secondary)" }}>
                  Это диапазон доступности, а не готовый слот: свободное время
                  проверяется ещё раз при создании записи.
                </p>
              </>
            )}
          </section>
        </>
      )}

      {/*
        График на неделю — чтение (срез B1). Именно та поверхность, на
        которой видно, что расписание мастера никто не заверял: сегодня это
        число живёт в комментарии к задаче (4 из 31 на 07.09.2026), и салон
        его не видит вовсе. Подтверждать отсюда нельзя — это решение
        владелицы и отдельный срез; здесь только показ.
      */}
      {mode === "one" && (week != null || weekErr != null) && (
        <section style={{ marginTop: "var(--s-3)" }}>
          <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
            График на неделю
          </h3>
          {weekErr != null ? (
            <StateError err={weekErr} onRetry={() => void loadWeek(selected)} />
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {week!.days.map((d) => (
                <li key={d.day_of_week} style={{ padding: "2px 0" }}>
                  <span style={{ display: "inline-block", minWidth: "2.5em" }}>
                    {WEEKDAYS_SHORT[d.day_of_week] ?? d.day_of_week}
                  </span>
                  {d.is_working_day && d.start_time && d.end_time
                    ? `${d.start_time}–${d.end_time}${
                        d.break_start && d.break_end
                          ? ` · перерыв ${d.break_start}–${d.break_end}`
                          : ""
                      }`
                    : "выходной"}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {mode === "all" && frameErr != null && (
        <StateError err={frameErr} onRetry={() => void loadFrame()} />
      )}

      {mode === "all" && frame != null && frameErr == null && (
        <>
          {frame.unreadable_lists.length > 0 && (
            // Названный пробел вместо молчаливой пустоты. Без этой строки
            // неразобранный перерыв выглядел бы как его отсутствие — и обед
            // был бы показан рабочим временем.
            <div className="callout" role="status" style={{ marginBottom: "var(--s-3)" }}>
              <p style={{ margin: 0 }}>
                {`День показан не полностью: не удалось разобрать ${frame.unreadable_lists
                  .map((k) => LIST_TITLE[k] ?? k)
                  .join(", ")}. Показанное верно, но не всё.`}
              </p>
            </div>
          )}

          <section style={{ marginBottom: "var(--s-3)" }}>
            <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
              Смены
            </h3>
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {frame.masters.map((m) => (
                <li key={m.specialist_id} style={{ padding: "2px 0" }}>
                  {`${m.display_name} · ${shiftLine(m)}`}
                  {m.breaks.state === "parsed" &&
                    ` · перерыв ${m.breaks.rows.map((r) => `${r.start}–${r.end}`).join(", ")}`}
                  {m.absences.state === "parsed" && m.absences.rows.length > 0 &&
                    ` · нет ${m.absences.rows.map((r) => `${r.start}–${r.end}`).join(", ")}`}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 style={{ fontSize: "var(--text-body-size, 15px)", margin: "0 0 var(--s-1)" }}>
              Хронология
            </h3>
            {timeline.length === 0 ? (
              <p style={{ margin: 0, color: "var(--c-text-secondary)" }}>
                Записей на сегодня нет.
              </p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {timeline.map(([time, entries]) => (
                  <li key={time} style={{ padding: "2px 0" }}>
                    <strong>{time}</strong>
                    <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                      {entries.map(({ masterName, visit }) => (
                        <li
                          key={visit.id}
                          style={{
                            padding: "2px 0",
                            textDecoration: RELEASED_VISIT_STATUSES.has(visit.status)
                              ? "line-through"
                              : undefined,
                          }}
                        >
                          {`${masterName} · ${visit.service_name} · ${visit.client_first_name} ${visit.client_last_initial}`}
                          {visit.is_in_progress && NOW_MARKER}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </SalonPilotFrame>
  );
}
