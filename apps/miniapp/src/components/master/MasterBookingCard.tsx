/**
 * Карточка записи мастера — макет DRF-1181 п.5 (DRF-2157, М-6). Одна на
 * «Сегодня» (крупная) и «Расписание» (компактная); «Детали записи» — не
 * карточка, там строки фактов.
 *
 *   today:    10:30–11:30 / Анна К. / Классический массаж / ⏱ 60 мин / ›
 *   schedule: 10:30 | Анна К. / Классический массаж · 60 мин / ›
 *
 * «Показываем только кто · что · когда. Не показываем: телефон, стоимость,
 * оплату, источник, комментарии, тех. статусы» — поэтому набор пропов закрыт:
 * лишнее с сервера сюда не пролезает по типу. Красная точка «идёт сейчас»,
 * «заканчивается ≈», чип «постоянный клиент» прежней карточки Расписания —
 * тех. статусы, сняты (DRF-1182 запрещает состояние «Визит идёт»).
 *
 * Длительность — минутами, как пишут макеты («60 мин»); для нецелых часов
 * макета нет — тоже минутами («90 мин»), отступление (ж) у владельца.
 *
 * Карточка — ссылка на «Детали записи» (DRF-1183 «нажатие на запись → экран
 * деталей»), адрес возврата — текущий экран.
 */
import { Link, useLocation } from "react-router-dom";

import { formatTimeHM } from "../../lib/masterDateFormat";
import { hapticSelection } from "../../lib/max-sdk";

export interface MasterBookingCardProps {
  variant: "today" | "schedule";
  /** «Анна К.» — имя с инициалом, как отдаёт сервер. */
  clientName: string;
  serviceName: string;
  startIso: string;
  /** Нужен только крупной карточке («10:30–11:30»); компактная показывает начало. */
  endIso?: string;
  durationMin: number;
  /** Адрес «Деталей записи» своей поверхности. */
  to: string;
  /** «Следующие спокойнее» — те же поля, тише по тону. */
  quiet?: boolean;
}

/** «60 мин» — как в макете; не formatDurationRu (тот — для «До визита»). */
function durationLabel(min: number): string {
  const n = Number.isFinite(min) ? Math.max(0, Math.floor(min)) : 0;
  return `${n} мин`;
}

export function MasterBookingCard({
  variant,
  clientName,
  serviceName,
  startIso,
  endIso,
  durationMin,
  to,
  quiet = false,
}: MasterBookingCardProps) {
  const location = useLocation();
  const start = formatTimeHM(startIso);
  const duration = durationLabel(durationMin);
  const className = [
    "master-booking-card",
    `master-booking-card--${variant}`,
    quiet ? "master-booking-card--quiet" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <Link
      to={to}
      state={{ from: location.pathname }}
      className={className}
      onClick={() => hapticSelection()}
    >
      {variant === "today" ? (
        <span className="master-booking-card__main">
          <span className="master-booking-card__time">
            {endIso ? `${start}–${formatTimeHM(endIso)}` : start}
          </span>
          <span className="master-booking-card__name">{clientName}</span>
          <span className="master-booking-card__service">{serviceName}</span>
          <span className="master-booking-card__duration">
            <IconClock /> {duration}
          </span>
        </span>
      ) : (
        <>
          <span className="master-booking-card__time">{start}</span>
          <span className="master-booking-card__main">
            <span className="master-booking-card__name">{clientName}</span>
            <span className="master-booking-card__service">
              {serviceName} · {duration}
            </span>
          </span>
        </>
      )}
      <span className="master-booking-card__chevron" aria-hidden="true">
        ›
      </span>
    </Link>
  );
}

function IconClock() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}
