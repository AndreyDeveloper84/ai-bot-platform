/**
 * Части экрана «Расписание» салона v2 (DRF-2933, макет DRF-2854 FROZEN v1.0).
 *
 * Не экраны: полоса недели, чипы, сетка кадра 1, лента по времени, день
 * мастера списком (кадр 12) и шторка фильтров (кадр 2). Ни один компонент
 * здесь не ходит на сервер и ничего не считает сам — расчёты в
 * `lib/salon-schedule.ts`, запросы у экранов.
 *
 * Свободное окно на этих компонентах — только то, что экран получил от
 * сервера; ни сетка, ни лента его не выводят.
 */
import { useMemo, useState } from "react";
import type { CSSProperties, MouseEvent, ReactNode, RefObject } from "react";

import { SystemState } from "../../components/OperationalSystemState";
import { SheetChrome } from "../../components/PersonalDataSheets";
import type { CatalogServiceLite, MasterDay } from "../../lib/admin-api";
import {
  MASTER_SEARCH_FROM,
  SS_COPY,
  addMonths,
  axisFor,
  axisHours,
  conflictItems,
  dayAria,
  dayNumber,
  feedHasVisits,
  masterDayOff,
  masterDayRows,
  matchesService,
  minuteAt,
  monthGrid,
  monthStart,
  monthTitle,
  offHours,
  placeVisits,
  shiftLabel,
  spanOf,
  untimedVisits,
  visitsByService,
  weekdayShort,
  type Axis,
  type ColumnVisit,
  type FeedGroup,
  type FreeWindow,
  type MasterColumn,
  type ScheduleFilters,
  type Span,
} from "../../lib/salon-schedule";
import { addDays, weekDates, weekRangeLabel, weekStart } from "../../lib/salon-working-time";
import { IconChevron, IconCross, IconPlus, IconSearch, WtAvatar } from "./WorkingTimeParts";

// ---------------------------------------------------------------------------
// Значки
// ---------------------------------------------------------------------------

function Svg({ size = 22, children }: { size?: number; children: ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function IconSliders() {
  return (
    <Svg>
      <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="8" cy="17" r="2" />
    </Svg>
  );
}

export function IconChevronDown() {
  return (
    <Svg size={16}>
      <path d="M6 9l6 6 6-6" />
    </Svg>
  );
}

function IconChevronLeft() {
  return (
    <Svg size={18}>
      <path d="M15 6l-6 6 6 6" />
    </Svg>
  );
}

// ---------------------------------------------------------------------------
// Полоса недели и чипы (кадр 1)
// ---------------------------------------------------------------------------

/** Семь дней недели с понедельника; выбранный закрашен. */
export function WeekStrip({
  monday,
  selected,
  label,
  onSelect,
}: {
  monday: string;
  selected: string | null;
  label: string;
  onSelect: (ymd: string) => void;
}) {
  return (
    <div className="ss-strip" role="group" aria-label={label}>
      {weekDates(monday).map((day) => {
        const on = day === selected;
        return (
          <button
            key={day}
            type="button"
            className={`ss-strip__day${on ? " ss-strip__day--on" : ""}`}
            aria-pressed={on}
            aria-label={dayAria(day)}
            onClick={() => onSelect(day)}
          >
            <span className="ss-strip__weekday" aria-hidden="true">
              {weekdayShort(day)}
            </span>
            <span className="ss-strip__number" aria-hidden="true">
              {dayNumber(day)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

export type SheetFocus = "date" | "masters" | "services";

/** «Все мастера ▾», «Все услуги ▾» и кнопка фильтров — три входа в одну шторку. */
export function FilterChips({
  masters,
  services,
  onOpen,
}: {
  masters: string;
  services: string;
  onOpen: (focus: SheetFocus, trigger: HTMLElement) => void;
}) {
  return (
    <div className="ss-chips">
      <button
        type="button"
        className="ss-chip"
        aria-haspopup="dialog"
        onClick={(e) => onOpen("masters", e.currentTarget)}
      >
        <span className="ss-chip__text">{masters}</span>
        <IconChevronDown />
      </button>
      <button
        type="button"
        className="ss-chip"
        aria-haspopup="dialog"
        onClick={(e) => onOpen("services", e.currentTarget)}
      >
        <span className="ss-chip__text">{services}</span>
        <IconChevronDown />
      </button>
      <button
        type="button"
        className="ss-iconbtn"
        aria-label={SS_COPY.filtersButton}
        aria-haspopup="dialog"
        onClick={(e) => onOpen("date", e.currentTarget)}
      >
        <IconSliders />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Общее для записи: подпись и отметка «сейчас по плану»
// ---------------------------------------------------------------------------

function visitTimes(visit: ColumnVisit): string {
  if (!visit.start) return SS_COPY.noTime;
  return visit.end ? `${visit.start}–${visit.end}` : visit.start;
}

function visitAria(visit: ColumnVisit, master: string | null): string {
  const parts = [`Запись ${visitTimes(visit)}`, visit.client];
  if (visit.service) parts.push(visit.service);
  if (master) parts.push(`мастер ${master}`);
  if (visit.now) parts.push(SS_COPY.nowMarker);
  return parts.join(", ");
}

function pos(span: Span): CSSProperties {
  return { top: `${span.top * 100}%`, height: `${span.height * 100}%` };
}

// ---------------------------------------------------------------------------
// Сетка (кадры 1 и 3) — компактный обзор для двух–четырёх мастеров
// ---------------------------------------------------------------------------

/** Свободные окна одной колонки — ответ сервера, который запросил экран. */
export interface ColumnFree {
  masterId: string;
  status: "loading" | "ready" | "error";
  windows: FreeWindow[];
  selected: FreeWindow | null;
}

function minuteFromClick(e: MouseEvent<HTMLElement>, axis: Axis): number | null {
  // Клавиатура и читалка: места нажатия нет — окна открываются без выбора.
  if (e.detail === 0) return null;
  const rect = e.currentTarget.getBoundingClientRect();
  if (rect.height <= 0) return null;
  return minuteAt(axis, (e.clientY - rect.top) / rect.height);
}

export function ScheduleGrid({
  columns,
  serviceIds,
  free,
  tappable,
  bookable,
  onVisit,
  onMaster,
  onBlock,
  onColumnTap,
  onSelectWindow,
  onBook,
}: {
  columns: readonly MasterColumn[];
  serviceIds: readonly string[];
  free: ColumnFree | null;
  /** Окна на эту дату бывают: не прошлое, салон не закрыт. Иначе колонка не нажимается. */
  tappable: boolean;
  /** Окна сейчас — действие: сеть есть, ответ свежий. */
  bookable: boolean;
  onVisit: (id: string) => void;
  onMaster: (masterId: string) => void;
  /** Блок и перерыв — вход в «Рабочее время» этой даты (заморозка, MUST FIX 4). */
  onBlock: (masterId: string) => void;
  onColumnTap: (masterId: string, minute: number | null) => void;
  onSelectWindow: (masterId: string, window: FreeWindow) => void;
  onBook: (masterId: string, window: FreeWindow) => void;
}) {
  // Ось — по всем записям колонок: фильтр услуг не должен двигать время под пальцем.
  const axis = useMemo(() => axisFor(columns), [columns]);
  const hours = axisHours(axis);
  const steps = Math.max(1, hours.length - 1);
  const untimed = columns.flatMap((col) =>
    untimedVisits(visitsByService(col.visits, serviceIds)).map((visit) => ({ visit, master: col.master.name })),
  );

  return (
    <section
      className="ss-grid"
      aria-label="Расписание по мастерам"
      style={{ "--ss-cols": columns.length, "--ss-hours": steps } as CSSProperties}
    >
      <div className="ss-grid__head">
        <span className="ss-grid__corner" aria-hidden="true" />
        {columns.map((col) => (
          <button
            key={col.master.id}
            type="button"
            className="ss-grid__master"
            aria-label={SS_COPY.openMaster(col.master.name)}
            onClick={() => onMaster(col.master.id)}
          >
            <WtAvatar name={col.master.name} photoUrl={col.master.photoUrl} />
            <span className="ss-grid__name">{col.master.name}</span>
            {col.master.specialization ? (
              <span className="ss-grid__spec">{col.master.specialization}</span>
            ) : null}
          </button>
        ))}
      </div>

      <div className="ss-grid__body">
        <ol className="ss-grid__axis" aria-hidden="true">
          {hours.map((hour, i) => (
            <li key={hour} className="ss-grid__hour" style={{ top: `${(i / steps) * 100}%` }}>
              {hour}
            </li>
          ))}
        </ol>
        {columns.map((col) => (
          <GridColumn
            key={col.master.id}
            col={col}
            axis={axis}
            serviceIds={serviceIds}
            free={free && free.masterId === col.master.id ? free : null}
            tappable={tappable}
            bookable={bookable && col.master.isActive}
            onVisit={onVisit}
            onBlock={onBlock}
            onColumnTap={onColumnTap}
            onSelectWindow={onSelectWindow}
            onBook={onBook}
          />
        ))}
      </div>

      {untimed.length > 0 ? (
        <ul className="ss-cards" aria-label={SS_COPY.noTime}>
          {untimed.map(({ visit, master }) => (
            <li key={visit.id}>
              <VisitCard visit={visit} master={master} onVisit={onVisit} />
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function GridColumn({
  col,
  axis,
  serviceIds,
  free,
  tappable,
  bookable,
  onVisit,
  onBlock,
  onColumnTap,
  onSelectWindow,
  onBook,
}: {
  col: MasterColumn;
  axis: Axis;
  serviceIds: readonly string[];
  free: ColumnFree | null;
  tappable: boolean;
  bookable: boolean;
  onVisit: (id: string) => void;
  onBlock: (masterId: string) => void;
  onColumnTap: (masterId: string, minute: number | null) => void;
  onSelectWindow: (masterId: string, window: FreeWindow) => void;
  onBook: (masterId: string, window: FreeWindow) => void;
}) {
  const { master, shift } = col;
  // Все записи остаются на оси: фильтр услуг приглушает чужие, а не убирает —
  // занятое время, исчезнувшее из колонки, выглядело бы свободным.
  const placed = placeVisits(axis, col.visits);
  const windows = free && free.status === "ready" ? free.windows : [];

  return (
    <div
      className={`ss-col${shift.kind === "off" ? " ss-col--off" : ""}`}
      role="group"
      aria-label={master.name}
      aria-busy={free?.status === "loading" ? true : undefined}
    >
      {/* Вся колонка — кнопка «свободные окна»: под пальцем это пустое время
          кадра 3, для клавиатуры и читалки — обычная кнопка с именем. */}
      <button
        type="button"
        className="ss-col__tap"
        aria-label={SS_COPY.freeWindowsFor(master.name)}
        disabled={!tappable}
        onClick={(e) => onColumnTap(master.id, minuteFromClick(e, axis))}
      />
      {offHours(axis, shift).map((span) => (
        <span key={`${span.top}-${span.height}`} className="ss-col__offhours" style={pos(span)} aria-hidden="true" />
      ))}
      {shift.kind !== "working" ? (
        <span className={`ss-col__label${shift.kind === "unknown" ? " ss-col__label--muted" : ""}`}>
          {shiftLabel(shift)}
        </span>
      ) : null}

      {col.blocks.map((block) => {
        const span = spanOf(axis, block.start, block.end);
        if (!span) return null;
        const title = block.kind === "break" ? SS_COPY.breakLabel : SS_COPY.block;
        return (
          <button
            key={`${block.kind}-${block.start}-${block.end}`}
            type="button"
            className="ss-block ss-block--gray"
            style={pos(span)}
            aria-label={`${title} ${block.start}–${block.end}, ${master.name}. ${SS_COPY.toWorkingTime}`}
            onClick={() => onBlock(master.id)}
          >
            {/* Без пробелов у тире: в колонке на четверых «14:00 – 16:00» не помещается. */}
            <span className="ss-block__time">{`${block.start}–${block.end}`}</span>
            <span className="ss-block__title">{title}</span>
          </button>
        );
      })}

      {windows.map((window) => {
        const span = spanOf(axis, window.start, window.end);
        if (!span) return null;
        const open = free?.selected?.start === window.start && free.selected.end === window.end;
        if (open) {
          return (
            <div
              key={`free-${window.start}`}
              className="ss-free ss-free--open"
              style={{ top: `${span.top * 100}%`, minHeight: `${span.height * 100}%` }}
              role="group"
              aria-label={`${SS_COPY.free} ${window.start}–${window.end}`}
            >
              <span className="ss-free__time">{`${window.start} – ${window.end}`}</span>
              <span className="ss-free__badge">{SS_COPY.free}</span>
              <button
                type="button"
                className="ss-free__cta"
                disabled={!bookable}
                onClick={() => onBook(master.id, window)}
              >
                {SS_COPY.book}
                <IconPlus />
              </button>
            </div>
          );
        }
        return (
          <button
            key={`free-${window.start}`}
            type="button"
            className="ss-free"
            style={pos(span)}
            aria-label={`${SS_COPY.free} ${window.start}–${window.end}`}
            onClick={() => onSelectWindow(master.id, window)}
          >
            <span className="ss-free__badge" aria-hidden="true">
              {SS_COPY.free}
            </span>
          </button>
        );
      })}

      {placed.map(({ visit, span, lane, lanes }) => {
        const dim = !matchesService(visit.serviceId, serviceIds);
        return (
          <button
            key={visit.id}
            type="button"
            className={`ss-block ss-block--visit${visit.released ? " ss-block--released" : ""}${visit.now ? " ss-block--now" : ""}${dim ? " ss-block--dim" : ""}`}
            style={{
              ...pos(span),
              left: `${(lane / lanes) * 100}%`,
              width: `${100 / lanes}%`,
            }}
            aria-label={visitAria(visit, master.name)}
            onClick={() => onVisit(visit.id)}
          >
            <span className="ss-block__time">{visit.start}</span>
            <span className="ss-block__title">{visit.firstName}</span>
          </button>
        );
      })}
    </div>
  );
}

/** Запись карточкой — лента, записи без времени, записи без мастера. */
export function VisitCard({
  visit,
  master,
  onVisit,
}: {
  visit: ColumnVisit;
  /** Имя мастера; `null` — запись ничья. */
  master: string | null;
  onVisit: (id: string) => void;
}) {
  return (
    <button
      type="button"
      className={`ss-card${visit.released ? " ss-card--released" : ""}${visit.now ? " ss-card--now" : ""}`}
      aria-label={visitAria(visit, master)}
      onClick={() => onVisit(visit.id)}
    >
      <span className="ss-card__title">{visit.client}</span>
      <span className="ss-card__meta">{[visitTimes(visit), visit.service].filter(Boolean).join(" · ")}</span>
      <span className={`ss-card__master${master ? "" : " ss-card__master--unknown"}`}>
        {master ?? SS_COPY.masterUnknown}
      </span>
      {visit.now ? <span className="ss-card__now">{SS_COPY.nowMarker}</span> : null}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Лента по времени — больше четырёх мастеров (DRF-1237, «Режим Все»)
// ---------------------------------------------------------------------------

/**
 * Записи всех показанных мастеров под временем начала.
 *
 * Сверху — «Мастера и смены»: свёрнутый список, а не полоса аватаров.
 * Горизонтальная лента из тридцати человек — та самая «горизонтальная
 * сетка», которой заморозка велит не быть (MUST FIX 6); выбор мастера —
 * шторка фильтров, а здесь — кто сегодня в смене и вход в его день.
 */
export function ScheduleFeed({
  columns,
  groups,
  emptyText,
  onVisit,
  onMaster,
}: {
  columns: readonly MasterColumn[];
  groups: readonly FeedGroup[];
  emptyText: string;
  onVisit: (id: string) => void;
  onMaster: (masterId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="ss-feed">
      <section className="ss-people">
        <button
          type="button"
          className="ss-people__toggle"
          aria-expanded={open}
          aria-controls="ss-people-list"
          onClick={() => setOpen((v) => !v)}
        >
          <span>{SS_COPY.shifts(columns.length)}</span>
          <IconChevronDown />
        </button>
        {open ? (
          <ul className="ss-people__list" id="ss-people-list">
            {columns.map((col) => (
              <li key={col.master.id}>
                <button
                  type="button"
                  className="ss-person"
                  aria-label={`${SS_COPY.openMaster(col.master.name)}, ${shiftLabel(col.shift)}`}
                  onClick={() => onMaster(col.master.id)}
                >
                  <WtAvatar name={col.master.name} photoUrl={col.master.photoUrl} size="sm" />
                  <span className="ss-person__name">{col.master.name}</span>
                  <span className="ss-person__shift">{shiftLabel(col.shift)}</span>
                  <span className="ss-person__chevron" aria-hidden="true">
                    <IconChevron />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section aria-labelledby="ss-timeline-heading">
        <h2 className="ss-heading" id="ss-timeline-heading">
          {SS_COPY.timeline}
        </h2>
        {!feedHasVisits(groups) ? <SystemState kind="empty" text={emptyText} /> : null}
        {groups.length > 0 ? (
          <ol className="ss-timeline">
            {groups.map((group) => (
              <li key={group.time ?? "none"} className="ss-timeline__group">
                <p className="ss-timeline__time">{group.time ?? SS_COPY.noTime}</p>
                <ul className="ss-cards">
                  {group.entries.map((entry) =>
                    entry.kind === "visit" ? (
                      <li key={`v-${entry.visit.id}`}>
                        <VisitCard visit={entry.visit} master={entry.master?.name ?? null} onVisit={onVisit} />
                      </li>
                    ) : (
                      <li key={`b-${entry.master.id}-${entry.block.start}`}>
                        <div className="ss-card ss-card--block">
                          <span className="ss-card__title">{SS_COPY.block}</span>
                          <span className="ss-card__meta">
                            {entry.block.start === "00:00" && entry.block.end === "24:00"
                              ? SS_COPY.wholeDay
                              : `${entry.block.start} – ${entry.block.end}`}
                          </span>
                          <span className="ss-card__master">{entry.master.name}</span>
                        </div>
                      </li>
                    ),
                  )}
                </ul>
              </li>
            ))}
          </ol>
        ) : null}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// День одного мастера списком (кадр 12)
// ---------------------------------------------------------------------------

/**
 * Записи — из дня салона, как в сетке и ленте; окна, блоки и конфликты — из
 * `day-schedule` (`day`). `day` ещё не пришёл или не пришёл вовсе — записи
 * показаны, а про остальное говорит экран, который звал.
 */
export function MasterDayList({
  visits,
  day,
  windows,
  windowsNote,
  serviceIds,
  bookable,
  onVisit,
  onBook,
  onBlock,
}: {
  visits: readonly ColumnVisit[];
  day: MasterDay | null;
  /** Окна, которые экран вправе показать (`liveWindows`). */
  windows: readonly FreeWindow[];
  /** Что сказать под списком про окна; `null` — ничего. */
  windowsNote: string | null;
  serviceIds: readonly string[];
  bookable: boolean;
  onVisit: (id: string) => void;
  onBook: (window: FreeWindow) => void;
  onBlock: () => void;
}) {
  const rows = masterDayRows(visits, day, windows, serviceIds);
  const untimed = untimedVisits(visits);
  const off = day ? masterDayOff(day) : false;
  const conflicts = conflictItems(day, visits);

  return (
    <div className="ss-day">
      {off ? (
        <p className="ss-dayoff" role="status">
          {SS_COPY.dayOff}
        </p>
      ) : null}

      {rows.length === 0 && untimed.length === 0 && day && !off ? (
        <SystemState kind="empty" text={SS_COPY.noRows} />
      ) : null}

      {rows.length > 0 ? (
        <ul className="ss-rows">
          {rows.map((row) => {
            if (row.kind === "booking") {
              const { visit } = row;
              return (
                <li key={`b-${visit.id}`}>
                  <button
                    type="button"
                    className={`ss-row ss-row--booking${visit.now ? " ss-row--now" : ""}${visit.released ? " ss-row--released" : ""}${row.dimmed ? " ss-row--dim" : ""}`}
                    aria-label={visitAria(visit, null)}
                    onClick={() => onVisit(visit.id)}
                  >
                    <span className="ss-row__time">
                      {visit.end ? `${visit.start} – ${visit.end}` : visit.start}
                    </span>
                    <span className="ss-row__text">
                      <span className="ss-row__title">{visit.client}</span>
                      {visit.now ? <span className="ss-row__now">{SS_COPY.nowMarker}</span> : null}
                    </span>
                  </button>
                </li>
              );
            }
            const time = `${row.start} – ${row.end}`;
            if (row.kind === "free") {
              return (
                <li key={`f-${row.start}`}>
                  <button
                    type="button"
                    className="ss-row ss-row--free"
                    disabled={!bookable}
                    aria-label={`${SS_COPY.free} ${row.start}–${row.end}, ${SS_COPY.book.toLowerCase()}`}
                    onClick={() => onBook({ start: row.start, end: row.end })}
                  >
                    <span className="ss-row__time">{time}</span>
                    <span className="ss-row__text">
                      <span className="ss-row__title">{SS_COPY.free}</span>
                    </span>
                    <span className="ss-row__plus" aria-hidden="true">
                      <IconPlus />
                    </span>
                  </button>
                </li>
              );
            }
            return (
              <li key={`x-${row.id}-${row.start}`}>
                <button
                  type="button"
                  className="ss-row ss-row--block"
                  aria-label={`${row.label} ${row.start}–${row.end}. ${SS_COPY.toWorkingTime}`}
                  onClick={onBlock}
                >
                  <span className="ss-row__time">{time}</span>
                  <span className="ss-row__text">
                    <span className="ss-row__title">{row.label}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}

      {untimed.length > 0 ? (
        <ul className="ss-cards" aria-label={SS_COPY.noTime}>
          {untimed.map((visit) => (
            <li key={visit.id}>
              <VisitCard visit={visit} master={null} onVisit={onVisit} />
            </li>
          ))}
        </ul>
      ) : null}

      {windowsNote ? <p className="ss-note">{windowsNote}</p> : null}

      {conflicts.length > 0 ? (
        // Конфликт показывается, а не прячется: салонная запись законно живёт
        // вне часов и поверх недоступности, и видеть её нужно, а не гадать.
        // Строка ведёт в запись — обойти конфликт отсюда нельзя (MUST FIX 8).
        <section className="ss-attention" aria-labelledby="ss-attention-heading">
          <h3 className="ss-heading" id="ss-attention-heading">
            {SS_COPY.attention}
          </h3>
          <ul className="ss-attention__list">
            {conflicts.map((c) => (
              <li key={`${c.label}-${c.bookingId}`}>
                <button type="button" className="ss-attention__item" onClick={() => onVisit(c.bookingId)}>
                  <span className="ss-attention__label">{c.label}</span>
                  {c.detail ? <span className="ss-attention__detail">{c.detail}</span> : null}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Шторка «Фильтры» (кадр 2)
// ---------------------------------------------------------------------------

type RangeMode = "day" | "week" | "month";

export interface FiltersResult {
  date: string;
  filters: ScheduleFilters;
}

function toggle(list: readonly string[], id: string): string[] {
  return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
}

/**
 * «День | Неделя | Месяц» — три способа выбрать ДЕНЬ: полоса текущей недели,
 * полоса со стрелками по неделям, календарь месяца. Недельного и месячного
 * вида расписания нет ни на одном кадре и ни в одном контракте — переключатель
 * меняет то, как выбирают дату, а не то, что показывает экран.
 */
export function FiltersSheet({
  date,
  columns,
  services,
  filters,
  focus,
  triggerRef,
  onApply,
  onClose,
}: {
  date: string;
  columns: readonly MasterColumn[];
  services: readonly CatalogServiceLite[];
  filters: ScheduleFilters;
  focus: SheetFocus;
  triggerRef: RefObject<HTMLElement>;
  onApply: (result: FiltersResult) => void;
  onClose: () => void;
}) {
  const [draftDate, setDraftDate] = useState(date);
  const [mode, setMode] = useState<RangeMode>("day");
  const [shownWeek, setShownWeek] = useState(() => weekStart(date));
  const [shownMonth, setShownMonth] = useState(() => monthStart(date));
  const [masterIds, setMasterIds] = useState<string[]>(() =>
    filters.masterIds.filter((id) => columns.some((c) => c.master.id === id)),
  );
  const [serviceIds, setServiceIds] = useState<string[]>(filters.serviceIds);
  const [query, setQuery] = useState("");

  const needle = query.trim().toLowerCase();
  const visibleMasters = needle
    ? columns.filter((c) => c.master.name.toLowerCase().includes(needle))
    : columns;

  const pickDate = (ymd: string) => {
    setDraftDate(ymd);
    setShownWeek(weekStart(ymd));
    setShownMonth(monthStart(ymd));
  };

  const section = (name: SheetFocus) => (focus === name ? { "data-initial-focus": true } : {});

  return (
    <SheetChrome
      headlineId="ss-filters-headline"
      headline={SS_COPY.sheet.title}
      closeDisabled={false}
      triggerRef={triggerRef}
      onClose={onClose}
    >
      <button type="button" className="ss-sheet__close" aria-label={SS_COPY.master.close} onClick={onClose}>
        <IconCross />
      </button>

      <div className="ss-sheet__scroll">
        <section className="ss-sheet__section" aria-labelledby="ss-range-heading">
          <h3 className="ss-sheet__heading" id="ss-range-heading">
            {SS_COPY.sheet.range}
          </h3>
          <div className="ss-segment" role="group" aria-label={SS_COPY.sheet.range}>
            {(["day", "week", "month"] as const).map((m, i) => (
              <button
                key={m}
                type="button"
                className={`ss-segment__item${mode === m ? " ss-segment__item--on" : ""}`}
                aria-pressed={mode === m}
                {...(i === 0 ? section("date") : {})}
                onClick={() => setMode(m)}
              >
                {SS_COPY.sheet[m]}
              </button>
            ))}
          </div>

          {mode === "day" ? (
            <WeekStrip
              monday={weekStart(draftDate)}
              selected={draftDate}
              label={SS_COPY.sheet.day}
              onSelect={pickDate}
            />
          ) : null}

          {mode === "week" ? (
            <>
              <div className="ss-pager">
                <button
                  type="button"
                  className="ss-pager__btn"
                  aria-label={SS_COPY.sheet.prevWeek}
                  onClick={() => setShownWeek((w) => addDays(w, -7))}
                >
                  <IconChevronLeft />
                </button>
                <span className="ss-pager__label" aria-live="polite">
                  {weekRangeLabel(shownWeek)}
                </span>
                <button
                  type="button"
                  className="ss-pager__btn"
                  aria-label={SS_COPY.sheet.nextWeek}
                  onClick={() => setShownWeek((w) => addDays(w, 7))}
                >
                  <IconChevron />
                </button>
              </div>
              <WeekStrip monday={shownWeek} selected={draftDate} label={SS_COPY.sheet.week} onSelect={pickDate} />
            </>
          ) : null}

          {mode === "month" ? (
            <>
              <div className="ss-pager">
                <button
                  type="button"
                  className="ss-pager__btn"
                  aria-label={SS_COPY.sheet.prevMonth}
                  onClick={() => setShownMonth((m) => addMonths(m, -1))}
                >
                  <IconChevronLeft />
                </button>
                <span className="ss-pager__label" aria-live="polite">
                  {monthTitle(shownMonth)}
                </span>
                <button
                  type="button"
                  className="ss-pager__btn"
                  aria-label={SS_COPY.sheet.nextMonth}
                  onClick={() => setShownMonth((m) => addMonths(m, 1))}
                >
                  <IconChevron />
                </button>
              </div>
              <div className="ss-month" role="group" aria-label={monthTitle(shownMonth)}>
                {monthGrid(shownMonth).map((week, w) => (
                  <div key={week.find(Boolean) ?? w} className="ss-month__week">
                    {week.map((day, i) =>
                      day ? (
                        <button
                          key={day}
                          type="button"
                          className={`ss-month__day${day === draftDate ? " ss-month__day--on" : ""}`}
                          aria-pressed={day === draftDate}
                          aria-label={dayAria(day)}
                          onClick={() => pickDate(day)}
                        >
                          {dayNumber(day)}
                        </button>
                      ) : (
                        <span key={`gap-${w}-${i}`} className="ss-month__gap" aria-hidden="true" />
                      ),
                    )}
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </section>

        <section className="ss-sheet__section" aria-labelledby="ss-masters-heading">
          <h3 className="ss-sheet__heading" id="ss-masters-heading">
            {SS_COPY.sheet.masters}
          </h3>
          {columns.length >= MASTER_SEARCH_FROM ? (
            <label className="wt-search">
              <span className="wt-search__icon" aria-hidden="true">
                <IconSearch />
              </span>
              <input
                type="search"
                className="wt-search__input"
                placeholder={SS_COPY.sheet.search}
                aria-label={SS_COPY.sheet.search}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
          ) : null}
          <ul className="ss-options">
            <li>
              <label className={`ss-option ss-option--all${masterIds.length === 0 ? " ss-option--on" : ""}`}>
                <span className="ss-option__text">
                  <span className="ss-option__title">{SS_COPY.allMasters}</span>
                </span>
                <input
                  type="checkbox"
                  className="ss-check"
                  checked={masterIds.length === 0}
                  {...section("masters")}
                  onChange={() => setMasterIds([])}
                />
              </label>
            </li>
            {visibleMasters.map((col) => (
              <li key={col.master.id}>
                <label className="ss-option">
                  <WtAvatar name={col.master.name} photoUrl={col.master.photoUrl} />
                  <span className="ss-option__text">
                    <span className="ss-option__title">{col.master.name}</span>
                    {col.master.specialization ? (
                      <span className="ss-option__sub">{col.master.specialization}</span>
                    ) : null}
                  </span>
                  <input
                    type="checkbox"
                    className="ss-check"
                    checked={masterIds.includes(col.master.id)}
                    onChange={() => setMasterIds((ids) => toggle(ids, col.master.id))}
                  />
                </label>
              </li>
            ))}
          </ul>
          {needle && visibleMasters.length === 0 ? <p className="ss-note">{SS_COPY.sheet.noMatches}</p> : null}
        </section>

        <section className="ss-sheet__section" aria-labelledby="ss-services-heading">
          <h3 className="ss-sheet__heading" id="ss-services-heading">
            {SS_COPY.sheet.services}
          </h3>
          <ul className="ss-options">
            <li>
              <label className={`ss-option ss-option--all${serviceIds.length === 0 ? " ss-option--on" : ""}`}>
                <span className="ss-option__text">
                  <span className="ss-option__title">{SS_COPY.allServices}</span>
                </span>
                <input
                  type="checkbox"
                  className="ss-check"
                  checked={serviceIds.length === 0}
                  {...section("services")}
                  onChange={() => setServiceIds([])}
                />
              </label>
            </li>
            {services.map((service) => (
              <li key={service.id}>
                <label className="ss-option">
                  <span className="ss-option__text">
                    <span className="ss-option__title">{service.name}</span>
                  </span>
                  <input
                    type="checkbox"
                    className="ss-check"
                    checked={serviceIds.includes(service.id)}
                    onChange={() => setServiceIds((ids) => toggle(ids, service.id))}
                  />
                </label>
              </li>
            ))}
          </ul>
          {services.length === 0 ? <p className="ss-note">{SS_COPY.sheet.noServices}</p> : null}
        </section>
      </div>

      <div className="ss-sheet__footer">
        <button
          type="button"
          className="btn-secondary wt-btn"
          onClick={() => {
            setMasterIds([]);
            setServiceIds([]);
            setQuery("");
          }}
        >
          {SS_COPY.sheet.reset}
        </button>
        <button
          type="button"
          className="btn-primary wt-btn"
          onClick={() => onApply({ date: draftDate, filters: { masterIds, serviceIds } })}
        >
          {SS_COPY.sheet.apply}
        </button>
      </div>
    </SheetChrome>
  );
}
