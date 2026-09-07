/**
 * «Сегодня» — главный экран пилотной салонной админки (DRF-1236).
 *
 * Адрес: `/admin/today`. Каркас поставлен DRF-1235, здесь содержимое.
 *
 * # Что построено с макета
 *
 * * `+ Новая запись` — главное действие, ведёт в живой
 *   `/admin/booking/new`. Показывается по РОЛИ зрителя, см. ниже.
 * * `Сейчас` — записи, чей интервал накрывает текущий момент. Зелёная
 *   полоса слева означает ровно `start_at <= now < end_at` и ничего
 *   больше (DRF-1236): это не статус, не «клиент пришёл» и не приход
 *   мастера. Признак считает сервер (`is_in_progress`).
 * * `Дальше` — ближайшие записи, превью на три строки, остальные
 *   раскрываются на месте.
 * * `Мастера сегодня` — кто из команды сегодня в деле и сколько у кого
 *   записей. Выключенные карточки без единой записи не показываются;
 *   выключенная карточка С записями показывается — спрятать строку, за
 *   которой стоят живые записи, значило бы спрятать день.
 *
 * # Экран не молчит, когда показывать нечего
 *
 * Пусто бывает по двум разным причинам. День без записей — одно; день,
 * где всё уже прошло, — другое, и к вечеру салон живёт именно в нём.
 * Сказать про второе «записей нет» было бы неправдой, а не сказать
 * ничего — оставить экран, неотличимый от несостоявшейся отрисовки.
 *
 * # День перечитывается при возврате на экран
 *
 * «Сейчас» сервер считает на момент ЗАПРОСА, а отсечка «Дальше»
 * заморожена на момент ответа. Без обновления оставленная вкладка через
 * два часа рисует «идёт» на том, что давно кончилось. Поэтому возврат на
 * экран (`visibilitychange`) перезапрашивает день; опроса по таймеру нет
 * — экран не стучится в ручку, пока на него никто не смотрит.
 *
 * # Чего на экране нет и почему
 *
 * **`Ayla · Изменения` и `Требует внимания` — backend-blocked.**
 * Оба блока на макете есть, и оба стоят на проекции операционных
 * событий, которой не существует: read-API событий нет ни одного во
 * всём URL-дереве, а изменения графика не порождают событий вовсе
 * (DRF-1236, комментарий 23.08.2026, `BACKEND-BLOCKED`). Владелец там же
 * запретил реконструировать «что изменилось» сравнением снимков
 * моделью. Значит, показать эти блоки нечем: любая их отрисовка сегодня
 * была бы либо выдумкой, либо пустой рамкой, обещающей ленту, которой
 * не будет. Вернутся вместе с event source.
 *
 * **`Кабинет` в строке записи.** Макет обещает его у каждой записи и в
 * карточке мастера; в `DayVisit` (`apps/admin_api/services/salon_day.py`)
 * такого поля нет.
 *
 * **Рабочие часы, фотография и «Недоступна 13:00 – 15:00» у мастера.**
 * `DayMaster` несёт `master_id`, `name`, `is_active`, `visits` — и всё.
 * `is_active` не пересказывается как «работает сегодня»: это признак
 * карточки в каталоге, а не смена.
 *
 * **Стрелка `>` в детали записи.** На макете каждая строка ведёт в
 * запись; экрана записи в пилоте нет ни по одному адресу. Строка,
 * которая выглядит нажимаемой и никуда не ведёт, хуже строки, которая
 * так не выглядит.
 *
 * **Телефон клиента.** Его нет в полезной нагрузке вовсе (DRF-1039).
 *
 * **Освобождённые слоты.** Отменённые и неявки на «Сегодня» не
 * показываются и не считаются: слот свободен, ждать на него некого.
 * Служба дня отдаёт их намеренно — «отсутствующая строка и отменённая
 * выглядят одинаково», — и для журнала дня это верно; для экрана
 * ближайших часов верно обратное. Решение экрана, а не побочный эффект
 * фильтров.
 *
 * Всё перечисленное вынесено в отчёт по задаче отдельным списком — это
 * работа по данным, а не решение этого экрана.
 *
 * # `Все записи на сегодня` ведут не туда, куда обещает макет
 *
 * По макету ссылка ведёт в `Расписание` на текущую дату. `Расписание`
 * (`SalonPilotScheduleScreen`) сегодня честно говорит «показывать
 * нечего»: ручки расписания салона не существует, она придёт с
 * DRF-1237. Ссылка со списка записей на пустой экран прячет записи, а
 * не показывает их.
 *
 * Поэтому остаток дня раскрывается ЗДЕСЬ: данные для него уже пришли —
 * `GET /api/v1/admin/day/` отдаёт день целиком, превью обрезает его
 * только на экране. Ссылка в `Расписание` вернётся, когда там будет что
 * показать; расхождение с макетом вынесено в отчёт.
 *
 * # Дата и время — в поясе салона
 *
 * `response.date` и `response.timezone` приходят с сервера. Посчитать
 * «сегодня» на телефоне администратора, который летит в другом часовом
 * поясе, значило бы показать чужой день; напечатать 10:30 в поясе
 * устройства — чужое время. Заголовок даты не рисуется, пока ответа
 * нет.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { StateError } from "../../components/StateError";
import {
  getSalonDay,
  type MeResponse,
  type SalonDayResponse,
} from "../../lib/admin-api";
import { canCreateBooking } from "../../lib/salon-pilot";
import {
  clientLabel,
  formatRange,
  formatTime,
  masterInitial,
  mastersToday,
  visitCountLabel,
  visitsNext,
  visitsNow,
  NEXT_PREVIEW_LIMIT,
  type DayRow,
} from "../../lib/salon-today";
import { SalonPilotFrame } from "./SalonPilotFrame";

/** `2026-09-07` → `Понедельник, 7 сентября` — как в шапке макета. */
function formatDayLine(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  // Полдень, а не полночь: на переходе летнего времени полночь ± сдвиг
  // попадает на соседние сутки, и подпись разъезжается с датой.
  const dt = new Date(y, m - 1, d, 12);
  const formatted = new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    weekday: "long",
  }).format(dt);
  return formatted.charAt(0).toUpperCase() + formatted.slice(1);
}

/**
 * Одна строка записи.
 *
 * `variant` решает только вид времени: у идущей записи виден интервал —
 * когда мастер освободится, — у будущей достаточно начала. Зелёная
 * полоса слева ставится классом, но смысл её несёт заголовок блока
 * «Сейчас»: цвет здесь вспомогательный, как и требует DRF-1236.
 */
function VisitRow({
  row,
  timeZone,
  variant,
}: {
  row: DayRow;
  timeZone: string;
  variant: "now" | "next";
}) {
  const { visit, masterName } = row;
  const time =
    variant === "now"
      ? formatRange(visit, timeZone)
      : formatTime(visit.start_at, timeZone);
  return (
    <li
      className={
        variant === "now"
          ? "salon-today__visit salon-today__visit--now"
          : "salon-today__visit"
      }
    >
      <span className="salon-today__visit-time">
        <span className="salon-today__visit-clock">{time}</span>
        {visit.duration_min > 0 ? (
          <span className="salon-today__visit-duration">
            {`${visit.duration_min} мин`}
          </span>
        ) : null}
      </span>
      <span className="salon-today__visit-body">
        <span className="salon-today__visit-client">{clientLabel(visit)}</span>
        {visit.service_name ? (
          <span className="salon-today__visit-service">
            {visit.service_name}
          </span>
        ) : null}
        {masterName ? (
          <span className="salon-today__visit-master">{masterName}</span>
        ) : (
          /* «Ничья» запись — специалист не сошёлся ни с одним мастером
             каталога. Показывается, а не прячется: запись, которую никто
             не видит, — тот самый отказ, ради которого эта поверхность и
             строилась (`SalonDay.orphan_visits`). */
          <span className="salon-today__visit-orphan">Мастер не определён</span>
        )}
      </span>
    </li>
  );
}

export function SalonPilotTodayScreen({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const [day, setDay] = useState<SalonDayResponse | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [showAllNext, setShowAllNext] = useState(false);
  /**
   * Момент, на который посчитано «Дальше».
   *
   * Берётся один раз на ответ, а не на каждый рендер: иначе раскрытие
   * списка пересчитывало бы отсечку, и запись, начавшаяся между двумя
   * нажатиями, исчезала бы под пальцем.
   *
   * Обновляется вместе с данными — то есть при каждой загрузке, включая
   * ту, что делает возврат на экран (см. `visibilitychange` ниже).
   */
  const [loadedAtMs, setLoadedAtMs] = useState(() => Date.now());

  useEffect(() => {
    const ctrl = new AbortController();
    let alive = true;
    setLoading(true);
    setErr(null);
    setShowAllNext(false);
    getSalonDay(undefined, { signal: ctrl.signal })
      .then((data) => {
        if (!alive) return;
        setDay(data);
        setLoadedAtMs(Date.now());
        setLoading(false);
      })
      .catch((e: unknown) => {
        if (!alive || ctrl.signal.aborted) return;
        setErr(e);
        setLoading(false);
      });
    return () => {
      alive = false;
      ctrl.abort();
    };
  }, [attempt]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  /**
   * Возврат на экран перезапрашивает день.
   *
   * Без этого экран показывает момент, в который его открыли, и ничего
   * больше: «Сейчас» считает сервер на время ЗАПРОСА, а отсечка «Дальше»
   * заморожена в `loadedAtMs`. Оставленная на обед вкладка через два часа
   * рисует зелёные полосы «идёт» на визитах, которые давно кончились, — и
   * ни одной кнопки обновить на успешном пути нет: «Попробовать снова»
   * живёт внутри `StateError`, то есть только в отказе.
   *
   * Самый дешёвый честный ответ — перечитать день, когда человек к нему
   * вернулся. Опросов по таймеру здесь нет намеренно: экран не обязан
   * стучаться в ручку, пока на него никто не смотрит.
   */
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") retry();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [retry]);

  const nowRows = useMemo(() => (day ? visitsNow(day) : []), [day]);
  const nextRows = useMemo(
    () => (day ? visitsNext(day, loadedAtMs) : []),
    [day, loadedAtMs],
  );
  const masters = useMemo(() => (day ? mastersToday(day) : []), [day]);

  const visibleNext = showAllNext
    ? nextRows
    : nextRows.slice(0, NEXT_PREVIEW_LIMIT);
  const hiddenNextCount = nextRows.length - visibleNext.length;

  const subtitle = day ? formatDayLine(day.date) : null;

  /**
   * Строка состояния — одна и та же на всё время жизни экрана.
   *
   * Живой участок, вставленный в DOM уже с текстом, части читалок не
   * объявляет вовсе, и переход «загружаем → загрузилось» не объявляет
   * никто. Поэтому область постоянная, а меняется её ТЕКСТ.
   */
  let statusLine = "";
  if (loading) statusLine = "Загружаем день салона…";
  else if (!err && day) {
    if (nowRows.length > 0) statusLine = `Сейчас идёт записей: ${nowRows.length}.`;
    else if (nextRows.length > 0)
      statusLine = `Ближайших записей: ${nextRows.length}.`;
  }

  return (
    <SalonPilotFrame me={me} title="Сегодня" subtitle={subtitle}>
      {canCreateBooking(me) ? (
        <button
          type="button"
          className="salon-today__cta"
          /* `?return=today` — чтобы экран создания вернул сюда, а не на
             мост. Без параметра его возврат ведёт на `/admin/day`, и
             главное действие пилота стало бы дверью в один конец: с
             пятивкладочной поверхности назад в пилот не ведёт ничего.
             Ровно та ловушка, от которой предостерегает
             `SalonPilotFrame`. */
          onClick={() => navigate("/admin/booking/new?return=today")}
        >
          + Новая запись
        </button>
      ) : null}

      <p className="salon-pilot__note" role="status">
        {statusLine}
      </p>

      {!loading && err ? <StateError err={err} onRetry={retry} /> : null}

      {!loading && !err && day ? (
        <>
          {/* Пусто на экране бывает по двум разным причинам, и молчать
              нельзя ни в одной. День без записей — это одно; день, где
              всё уже прошло, — совсем другое, и к вечеру салон живёт
              именно в нём. Отличать их по `summary.total` можно честно:
              он считает записи дня целиком, включая закрытые. */}
          {nowRows.length === 0 && nextRows.length === 0 ? (
            <div className="callout">
              <p style={{ margin: 0 }}>
                {day.summary.total === 0
                  ? "Записей на сегодня нет."
                  : "Записи на сегодня закончились — впереди ничего не осталось."}
              </p>
            </div>
          ) : null}

          {nowRows.length > 0 ? (
            <section
              className="salon-today__section"
              aria-labelledby="salon-today-now"
            >
              <h2 className="salon-today__heading" id="salon-today-now">
                Сейчас
                <span className="salon-today__count">{nowRows.length}</span>
              </h2>
              <ul className="salon-today__list">
                {nowRows.map((row) => (
                  <VisitRow
                    key={row.visit.id}
                    row={row}
                    timeZone={day.timezone}
                    variant="now"
                  />
                ))}
              </ul>
            </section>
          ) : null}

          {nextRows.length > 0 ? (
            <section
              className="salon-today__section"
              aria-labelledby="salon-today-next"
            >
              <h2 className="salon-today__heading" id="salon-today-next">
                Дальше
              </h2>
              <ul className="salon-today__list">
                {visibleNext.map((row) => (
                  <VisitRow
                    key={row.visit.id}
                    row={row}
                    timeZone={day.timezone}
                    variant="next"
                  />
                ))}
              </ul>
              {/* Переключатель, а не одноразовая кнопка. Кнопка, которая
                  исчезает под пальцем, роняет фокус в `<body>`: тот, кто
                  ходит с клавиатуры или читалкой, теряет место, и никто
                  не объявляет, что строк стало больше. И развернуть было
                  можно, а свернуть — уже нет. */}
              {nextRows.length > NEXT_PREVIEW_LIMIT ? (
                <button
                  type="button"
                  className="salon-today__more"
                  aria-expanded={showAllNext}
                  onClick={() => setShowAllNext((on) => !on)}
                >
                  {showAllNext
                    ? "Свернуть"
                    : `Показать ещё ${visitCountLabel(hiddenNextCount).toLowerCase()}`}
                </button>
              ) : null}
            </section>
          ) : null}

          {masters.length > 0 ? (
            <section
              className="salon-today__section"
              aria-labelledby="salon-today-masters"
            >
              <h2 className="salon-today__heading" id="salon-today-masters">
                Мастера сегодня
              </h2>
              {/* Не «работают N мастеров»: рабочих часов и смен в ответе
                  нет, и утверждать выход мастера на смену нечем. Сказано
                  ровно то, что известно, — кто есть в команде. */}
              <p className="salon-today__section-note">
                {`В команде: ${masters.length}`}
              </p>
              <ul className="salon-today__masters">
                {masters.map((master) => (
                  <li className="salon-today__master" key={master.masterId}>
                    <span className="salon-today__avatar" aria-hidden="true">
                      {masterInitial(master.name)}
                    </span>
                    <span className="salon-today__master-body">
                      <span className="salon-today__master-name">
                        {master.name}
                      </span>
                      <span className="salon-today__master-count">
                        {visitCountLabel(master.visitCount)}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
    </SalonPilotFrame>
  );
}
