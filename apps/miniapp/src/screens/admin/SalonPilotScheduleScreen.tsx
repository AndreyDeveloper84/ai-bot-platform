/**
 * «Расписание» — второй раздел салонной админки (DRF-1235, DRF-1237), вид v2
 * по макету DRF-2854 FROZEN v1.0 (DRF-2933).
 *
 * Адрес: `/admin/schedule?date=YYYY-MM-DD`. Экран ТОЛЬКО читает: дата,
 * мастера, записи, свободные окна, блоки и выходные — и ведёт в соседние
 * экраны. Ничего отсюда не создаётся и не меняется (заморозка: «Schedule
 * never silently creates/changes appointments or availability»):
 *
 * * запись → карточка записи (`/admin/bookings/:id`);
 * * свободное окно и «+» → ручная запись с контекстом (мастер, дата, окно);
 * * блок, перерыв, «⋯» → «Рабочее время» (DRF-2936);
 * * мастер → его день (`/admin/schedule/master/:id`, кадр 12).
 *
 * Недельный график с подтверждением, «Назначено» и предпросмотр закрытия,
 * которые жили здесь до v2, переехали: график — в карточку мастера
 * (`/admin/team/:id`), остальное — в «Рабочее время».
 *
 * # Три раскладки — числом показанных мастеров
 *
 * Заморозка запрещает три и более колонок мастеров как обязательную
 * мобильную раскладку и горизонтальную сетку для большой команды (MUST FIX
 * 1 и 6), оставляя колонки «компактным обзором, пока он читается». Поэтому:
 *
 * * **один мастер** — его день списком, со свободными окнами (кадр 12);
 * * **от двух до четырёх** — сетка кадра 1: колонки, ось времени, записи и
 *   блоки; свободные окна — по нажатию на колонку (кадр 3);
 * * **больше четырёх** — лента по времени (DRF-1237, «Режим Все»), без
 *   свободных окон: многомастерного поиска доступности не существует.
 *
 * # Экран НЕ считает доступность
 *
 * Свободное окно — строка `free_windows` из `day-schedule`, которую считает
 * сервер. Экран её только отсекает там, где известно, что она не продаётся
 * (прошлое, закрытие салона — `liveWindows`), и не делает действием, когда
 * она не свежая. Окончательную проверку делает создание записи.
 *
 * # Два ответа, а не два дня
 *
 * Записи — из дня салона (зеркало), смены, перерывы, отсутствия и закрытия
 * — из кадра каталога, по `master_id` строки. Кадр не прочитан — записи
 * показаны, а про смены сказано словами; строка кадра без пары — «часы
 * неизвестны», не «свободен» и не «выходной» (§29.5).
 *
 * # Дата и фильтры
 *
 * Дата — в адресе; нет параметра — «сегодня салона», и дату называет сервер
 * (устройство в другом поясе показало бы чужой день). Вход без даты — свежий:
 * фильтры прошлого захода не поднимаются. Переходы в карточку записи, к
 * мастеру и в ручную запись уносят дату с собой, поэтому возврат приходит с
 * датой и с прежними фильтрами (они живут в `sessionStorage` салона).
 *
 * # Чего на экране нет и почему
 *
 * **Кабинета.** Заморозка DRF-1237 просит «мастер и кабинет»; кабинета нет
 * ни в дне салона, ни в кадре. **Разноцветных блоков.** Палитра Mini App
 * заперта линтером, а цвета блоков на макете семантики не несут — здесь
 * один цвет записи и зелёная полоса «сейчас по плану». **Недельного и
 * месячного вида.** «День | Неделя | Месяц» в фильтрах — три способа выбрать
 * день; таких видов расписания нет ни на одном кадре.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { SystemState } from "../../components/OperationalSystemState";
import { useOnline } from "../../hooks/useOnline";
import { useMasterDay, useSalonDay } from "../../hooks/useSalonSchedule";
import {
  getCatalogServicesForAdmin,
  getMasterDaySchedule,
  listMasters,
  type CatalogServiceLite,
  type MasterListItem,
  type MeResponse,
} from "../../lib/admin-api";
import { canCreateBooking } from "../../lib/salon-pilot";
import {
  NO_FILTERS,
  SS_COPY,
  buildColumns,
  closureNotices,
  closureWindowsNote,
  columnVisit,
  dateLine,
  dateParam,
  effectiveFilters,
  feedGroups,
  frameListTitles,
  freeWindowsOf,
  layoutFor,
  liveWindows,
  loadFilters,
  mastersChip,
  newBookingPath,
  salonClosure,
  salonNow,
  saveFilters,
  scheduleMasterPath,
  schedulePath,
  servicesChip,
  shownColumns,
  visitsByService,
  windowAt,
  windowsFresh,
  type FreeWindow,
  type ScheduleFilters,
} from "../../lib/salon-schedule";
import { WORKING_TIME_ROOT, weekStart, workingTimeDayPath } from "../../lib/salon-working-time";
import { SalonPilotFrame } from "./SalonPilotFrame";
import { MasterDayPanel } from "./ScheduleMasterDay";
import {
  FilterChips,
  FiltersSheet,
  ScheduleFeed,
  ScheduleGrid,
  VisitCard,
  WeekStrip,
  type ColumnFree,
  type FiltersResult,
  type SheetFocus,
} from "./ScheduleParts";
import {
  IconCalendar,
  IconChevron,
  IconPeople,
  IconPlus,
  WorkingTimeMenu,
  WtAvatar,
  WtLoadError,
  WtStateCard,
} from "./WorkingTimeParts";

/** Сколько страниц мастеров читать: салон пилота — десятки человек, не тысячи. */
const ROSTER_MAX_PAGES = 5;

/** Фото и специализации — справочник; без него экран работает, с инициалами. */
async function readRoster(signal: AbortSignal): Promise<MasterListItem[]> {
  const items: MasterListItem[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < ROSTER_MAX_PAGES; page += 1) {
    const res = await listMasters({ cursor, limit: 100 }, { signal });
    items.push(...res.items);
    if (!res.next_cursor) break;
    cursor = res.next_cursor;
  }
  return items;
}

export function SalonPilotScheduleScreen({ me }: { me: MeResponse }) {
  const navigate = useNavigate();
  const online = useOnline();
  const [params, setParams] = useSearchParams();
  const wanted = dateParam(params.get("date"));
  const tenantId = me.tenant.id;

  const [attempt, setAttempt] = useState(0);
  const phase = useSalonDay(wanted, attempt);
  const data = phase.kind === "ready" ? phase.data : null;
  const refreshErr = phase.kind === "ready" ? phase.refreshErr : null;
  const refreshing = phase.kind === "ready" && phase.refreshing;
  const loadedAt = data?.loadedAt ?? 0;

  const [roster, setRoster] = useState<MasterListItem[]>([]);
  const [services, setServices] = useState<CatalogServiceLite[]>([]);
  // Вход без даты в адресе — свежий: фильтры прошлого захода не поднимаются.
  const [filters, setFilters] = useState<ScheduleFilters>(() => {
    if (wanted) return loadFilters(tenantId);
    saveFilters(tenantId, NO_FILTERS);
    return NO_FILTERS;
  });
  const [sheet, setSheet] = useState<SheetFocus | null>(null);
  const sheetTrigger = useRef<HTMLElement | null>(null);
  /** Колонка сетки, чьи свободные окна открыты (кадр 3). */
  const [free, setFree] = useState<ColumnFree | null>(null);
  const freeCache = useRef(new Map<string, { windows: FreeWindow[]; fetchedAt: number }>());
  /** Номер запроса окон: ответ на старый запрос не должен лечь поверх нового. */
  const freeToken = useRef(0);
  const [stale, setStale] = useState(false);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    void readRoster(controller.signal).then(
      (items) => {
        if (alive) setRoster(items);
      },
      () => undefined,
    );
    void getCatalogServicesForAdmin().then(
      (items) => {
        if (alive) setServices(items);
      },
      () => undefined,
    );
    return () => {
      alive = false;
      controller.abort();
      freeToken.current += 1;
    };
  }, []);

  // Возврат на экран перечитывает день: «сейчас по плану» и окна считаются на
  // момент ответа, и оставленная вкладка показывала бы прошедший час. Опроса
  // по таймеру нет — экран не стучится в ручку, пока на него не смотрят.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") retry();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [retry]);

  // Новый ответ дня, другая дата или сбой обновления — прежние окна не про
  // это состояние: они сняты, а не оставлены «на всякий случай» (MUST FIX 7).
  // Эффект — до отрисовки: отложенный стёр бы и нажатие, сделанное в первые
  // миллисекунды после прихода данных, и экран молчал бы в ответ.
  useLayoutEffect(() => {
    freeToken.current += 1;
    freeCache.current.clear();
    setFree(null);
    setStale(false);
  }, [loadedAt, refreshErr]);

  const date = data?.day.date ?? wanted;
  const columns = useMemo(() => (data ? buildColumns(data.day, data.frame, roster) : []), [data, roster]);
  const active = useMemo(() => effectiveFilters(filters, columns, services), [filters, columns, services]);
  const shown = useMemo(() => shownColumns(columns, active), [columns, active]);
  const layout = data ? layoutFor(shown.length) : null;
  const single = layout === "single" ? (shown[0] ?? null) : null;
  const orphans = useMemo(
    () => (data ? data.day.orphan_visits.map((v) => columnVisit(v, data.day.date)) : []),
    [data],
  );
  const closures = useMemo(() => closureNotices(data?.frame ?? null), [data]);
  const closure = data ? salonClosure(data.frame, data.localSchedule) : "unknown";
  const now = data ? salonNow(data.day.timezone) : null;
  const past = Boolean(date && now && date < now.ymd);
  /** Окна на эту дату вообще бывают: не прошлое, и салон заведомо открыт. */
  const windowsPossible = Boolean(date && now) && !past && closure === "open";
  /** Записать человеку вообще разрешено — то же правило, что у «+». */
  const mayBook = canCreateBooking(me);

  const masterDay = useMasterDay(single?.master.id ?? null, single ? date : null, loadedAt);

  const markStale = useCallback(() => {
    freeToken.current += 1;
    freeCache.current.clear();
    setFree(null);
    setStale(true);
  }, []);

  const tapColumn = useCallback(
    async (masterId: string, minute: number | null) => {
      if (!data || !date || !windowsPossible) return;
      const reveal = (windows: FreeWindow[]) => {
        const live = liveWindows(windows, date, salonNow(data.day.timezone), "open");
        setFree({
          masterId,
          status: "ready",
          windows: live,
          selected: minute === null ? null : windowAt(live, minute),
          anchor: minute,
        });
        // Окна только что прочитаны — просьба «обновите» больше не про них.
        setStale(false);
      };
      // Любое новое нажатие отменяет ответ, летящий на прежнее: иначе окна
      // другой колонки легли бы поверх открытой из кэша.
      freeToken.current += 1;
      const token = freeToken.current;
      const cached = freeCache.current.get(masterId);
      if (cached && windowsFresh(cached.fetchedAt)) {
        reveal(cached.windows);
        return;
      }
      setFree({ masterId, status: "loading", windows: [], selected: null, anchor: minute });
      try {
        const res = await getMasterDaySchedule(masterId, { from: date, to: date });
        if (token !== freeToken.current) return;
        const windows = freeWindowsOf(res.days.find((d) => d.date === date) ?? null);
        freeCache.current.set(masterId, { windows, fetchedAt: Date.now() });
        reveal(windows);
      } catch {
        if (token !== freeToken.current) return;
        setFree({ masterId, status: "error", windows: [], selected: null, anchor: null });
      }
    },
    [data, date, windowsPossible],
  );

  const here = schedulePath(date);
  const openVisit = (id: string) =>
    navigate(`/admin/bookings/${encodeURIComponent(id)}`, { state: { from: here } });
  const openMaster = (masterId: string) => {
    if (date) navigate(scheduleMasterPath(masterId, date));
  };
  const openWorkingTime = (masterId: string) => {
    if (date) navigate(workingTimeDayPath(masterId, date));
  };
  const book = (masterId: string, window: FreeWindow) =>
    navigate(newBookingPath({ date, masterId, from: window.start, to: window.end }));

  const bookFromGrid = (masterId: string, window: FreeWindow) => {
    const cached = freeCache.current.get(masterId);
    if (!cached || !windowsFresh(cached.fetchedAt)) {
      markStale();
      return;
    }
    book(masterId, window);
  };

  const selectDate = (ymd: string) => setParams({ date: ymd }, { replace: true });

  const openSheet = (focus: SheetFocus, trigger: HTMLElement) => {
    sheetTrigger.current = trigger;
    setSheet(focus);
  };
  const applySheet = ({ date: picked, filters: next }: FiltersResult) => {
    setSheet(null);
    setFilters(next);
    saveFilters(tenantId, next);
    if (picked !== date) selectDate(picked);
    sheetTrigger.current?.focus();
  };

  const groups = useMemo(
    () => (layout === "feed" ? feedGroups(shown, active.masterIds.length === 0 ? orphans : [], active.serviceIds) : []),
    [layout, shown, orphans, active],
  );
  const looseOrphans =
    layout !== "feed" && active.masterIds.length === 0 ? visitsByService(orphans, active.serviceIds) : [];

  let gridStatus: string | null = null;
  if (layout === "grid" && free) {
    const name = shown.find((c) => c.master.id === free.masterId)?.master.name ?? "";
    if (free.status === "loading") gridStatus = SS_COPY.freeLoading;
    else if (free.status === "error") gridStatus = SS_COPY.freeFailed;
    else gridStatus = free.windows.length === 0 ? SS_COPY.noFreeWindowsFor(name) : SS_COPY.freeNote;
  }

  return (
    <SalonPilotFrame
      me={me}
      title={SS_COPY.title}
      // Дата — только из ответа сервера или из адреса; пока её нет, строка
      // держит место, а не показывает дату устройства.
      overline={date ? dateLine(date) : " "}
      titleActions={
        <>
          <button
            type="button"
            className="ss-iconbtn"
            aria-label={SS_COPY.calendar}
            aria-haspopup="dialog"
            disabled={!data}
            onClick={(e) => openSheet("date", e.currentTarget)}
          >
            <IconCalendar />
          </button>
          <WorkingTimeMenu
            masterId={single?.master.id ?? null}
            onOpen={(state) => navigate(WORKING_TIME_ROOT, { state })}
          />
        </>
      }
    >
      <div className="ss-screen">
        {!online ? <SystemState kind="offline" /> : null}

        {date ? (
          <WeekStrip monday={weekStart(date)} selected={date} label="Дни недели" onSelect={selectDate} />
        ) : null}

        {data ? (
          <FilterChips
            masters={mastersChip(columns, active)}
            services={servicesChip(services, active)}
            onOpen={openSheet}
          />
        ) : null}

        {phase.kind === "loading" ? <SystemState kind="loading" lines={5} /> : null}
        {phase.kind === "error" ? <WtLoadError page what="schedule" err={phase.err} onRetry={retry} /> : null}

        {data && date ? (
          <>
            {refreshErr != null ? (
              <SystemState
                kind="load_error"
                what="schedule"
                hasData
                err={refreshErr}
                busy={refreshing}
                onRetry={retry}
              />
            ) : null}

            {/* «Явная пометка актуальности» (MUST FIX 7) — всегда, а не только
                после сбоя: экран, открытый два часа, обязан это показывать. */}
            <p className="ss-asof">
              <span>{SS_COPY.asOf(salonNow(data.day.timezone, new Date(data.loadedAt)).hm)}</span>
              <button type="button" className="ss-asof__refresh" disabled={refreshing} onClick={retry}>
                {SS_COPY.refresh}
              </button>
            </p>

            {stale ? (
              <div className="ss-stale" role="alert">
                <SystemState kind="stale" />
                <p className="ss-note">{SS_COPY.staleWindows}</p>
              </div>
            ) : null}

            {closures.map((closure, i) => (
              <div key={`${closure.text}-${i}`} className="callout" role="status">
                <p className="ss-callout__text">
                  {closure.reason ? `${closure.text} · ${closure.reason}` : closure.text}
                </p>
              </div>
            ))}
            {!past && layout === "grid" && closure !== "open" ? (
              <p className="ss-note">{closureWindowsNote(closure)}</p>
            ) : null}

            {data.frameFailed ? (
              <div className="callout" role="status">
                <p className="ss-callout__text">{SS_COPY.frameMissing}</p>
              </div>
            ) : null}
            {data.frame && data.frame.unreadable_lists.length > 0 ? (
              // Названный пробел вместо тишины: неразобранный перерыв выглядел
              // бы как его отсутствие — и обед был бы показан рабочим временем.
              <div className="callout" role="status">
                <p className="ss-callout__text">
                  {SS_COPY.frameUnreadable(frameListTitles(data.frame.unreadable_lists))}
                </p>
              </div>
            ) : null}

            {layout === "none" ? (
              <WtStateCard
                page
                tone="neutral"
                icon={<IconPeople />}
                title={SS_COPY.noMastersTitle}
                body={SS_COPY.noMasters}
              />
            ) : null}

            {layout === "grid" ? (
              <>
                <ScheduleGrid
                  columns={shown}
                  serviceIds={active.serviceIds}
                  free={free}
                  tappable={windowsPossible}
                  bookable={online && refreshErr == null && mayBook}
                  onVisit={openVisit}
                  onMaster={openMaster}
                  onBlock={openWorkingTime}
                  onColumnTap={(masterId, minute) => void tapColumn(masterId, minute)}
                  onSelectWindow={(masterId, window) =>
                    setFree((prev) =>
                      prev && prev.masterId === masterId ? { ...prev, selected: window } : prev,
                    )
                  }
                  onBook={bookFromGrid}
                />
                <p className="ss-note" role="status">
                  {gridStatus}
                </p>
              </>
            ) : null}

            {layout === "feed" ? (
              <>
                <p className="ss-note">{SS_COPY.feedHint}</p>
                <ScheduleFeed
                  columns={shown}
                  groups={groups}
                  emptyText={active.serviceIds.length > 0 ? SS_COPY.noVisitsForServices : SS_COPY.noVisits}
                  onVisit={openVisit}
                  onMaster={openMaster}
                />
              </>
            ) : null}

            {single ? (
              <div className="ss-single">
                <button
                  type="button"
                  className="ss-single__master"
                  aria-label={SS_COPY.openMaster(single.master.name)}
                  onClick={() => openMaster(single.master.id)}
                >
                  <WtAvatar name={single.master.name} photoUrl={single.master.photoUrl} />
                  <span className="ss-single__text">
                    <span className="ss-single__name">{single.master.name}</span>
                    {single.master.specialization ? (
                      <span className="ss-single__spec">{single.master.specialization}</span>
                    ) : null}
                  </span>
                  <span className="ss-single__chevron" aria-hidden="true">
                    <IconChevron />
                  </span>
                </button>
                <MasterDayPanel
                  phase={masterDay}
                  date={date}
                  timezone={data.day.timezone}
                  visits={single.visits}
                  serviceIds={active.serviceIds}
                  closure={closure}
                  canBook={single.master.isActive && mayBook}
                  online={online && refreshErr == null}
                  onVisit={openVisit}
                  onBook={(window) => book(single.master.id, window)}
                  onBlock={() => openWorkingTime(single.master.id)}
                  onStale={markStale}
                  onRetry={retry}
                />
              </div>
            ) : null}

            {looseOrphans.length > 0 ? (
              // Ничья запись — специалист не сошёлся ни с одним мастером.
              // Показывается, а не прячется: запись, которую никто не видит, —
              // тот самый отказ, ради которого салонная поверхность строилась.
              <section aria-labelledby="ss-orphans-heading">
                <h2 className="ss-heading" id="ss-orphans-heading">
                  {SS_COPY.orphans}
                </h2>
                <ul className="ss-cards">
                  {looseOrphans.map((visit) => (
                    <li key={visit.id}>
                      <VisitCard visit={visit} master={null} onVisit={openVisit} />
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
          </>
        ) : null}
      </div>

      {data && mayBook ? (
        <button
          type="button"
          className="ss-fab"
          aria-label={SS_COPY.newBooking}
          onClick={() =>
            navigate(
              newBookingPath({
                // Прошедшую дату в форму не несём: записать на неё нельзя.
                date: past ? null : date,
                masterId: single?.master.isActive ? single.master.id : null,
              }),
            )
          }
        >
          <IconPlus />
        </button>
      ) : null}

      {sheet && data && date ? (
        <FiltersSheet
          date={date}
          columns={columns}
          services={services}
          filters={active}
          focus={sheet}
          triggerRef={sheetTrigger as React.RefObject<HTMLElement>}
          onApply={applySheet}
          onClose={() => setSheet(null)}
        />
      ) : null}
    </SalonPilotFrame>
  );
}
