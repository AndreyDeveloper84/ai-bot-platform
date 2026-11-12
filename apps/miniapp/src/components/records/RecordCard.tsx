/**
 * Карточка записи в списке и мини-карточка «о какой записи речь».
 * Макет «Записи.png» (DRF-2952), кадры 1, 7, 8, 12.
 *
 * Карточка списка — одна кнопка целиком: фото мастера, услуга, мастер и
 * салон, время, цена, адрес, шеврон. Действий на ней нет — они живут в
 * карточке записи; блок действий у самой свежей прошедшей записи рисует
 * экран отдельно, под карточкой (кнопку в кнопку вкладывать нельзя).
 *
 * Чего на карточке нет намеренно: обычного статуса. Предстоящая
 * подтверждённая и прошедшая состоявшаяся — просто карточка, как на макете;
 * значок статуса появляется только когда статус — новость (отменена,
 * перенесена, не пришла, незнакомый). Значок оплаты — когда его прислал
 * сервер.
 */
import {
  isOrdinaryStatus,
  initialsOf,
  progressLabel,
  renderStatus,
} from "../../lib/customer-records";
import type { BookingSection, RecordItem } from "../../lib/customer-records";
import { priceFromLabel } from "../../lib/format";
import { visitAddressText } from "../../lib/visit-address";
import { MasterPhoto } from "../MasterPhoto";
import { PaymentStatusBadge } from "../PaymentStatusBadge";
import { StatusBadge } from "../StatusBadge";
import { RecordIcon } from "./RecordIcons";

/** Фото мастера или плитка с инициалами — фото нет, не загрузилось, отказано. */
export function MasterThumb({
  photoUrl,
  name,
  size,
}: {
  photoUrl: string | null | undefined;
  name: string;
  size: "card" | "avatar" | "tile";
}) {
  const fallback = (
    <span className="cr-thumb__initials" aria-hidden="true">
      {initialsOf(name)}
    </span>
  );
  return (
    <span className={`cr-thumb cr-thumb--${size}`}>
      {/* Фото декоративно: имя мастера стоит текстом рядом. */}
      <MasterPhoto src={photoUrl} alt="" className="cr-thumb__img" fallback={fallback} />
    </span>
  );
}

interface CardProps {
  item: RecordItem;
  section: BookingSection;
  onOpen: () => void;
}

export function RecordCard({ item, section, onOpen }: CardProps) {
  const { rendering } = renderStatus(item.status);
  const priceLabel = item.price ? priceFromLabel(item.price) : "";
  const titleId = `cr-card-${item.bookingId}`;
  const detailsId = `${titleId}-details`;
  const inProgress = progressLabel(item.status);

  return (
    // Имя кнопки — услуга, остальное — её описание: две записи на одну услугу
    // читающий с экрана различает по мастеру и времени.
    <button
      type="button"
      className="cr-card"
      onClick={onOpen}
      aria-labelledby={titleId}
      aria-describedby={detailsId}
    >
      <MasterThumb photoUrl={item.masterPhotoUrl} name={item.masterName} size="card" />
      <span className="cr-card__text">
        <span id={titleId} className="cr-card__service">
          {item.serviceName}
        </span>
        <span id={detailsId} className="cr-card__details">
          {/* DRF-2436 B / п.15: салон — в строке мастера, через «·»; «у Ольга»
              ломало падеж (слова владельца 28.09, п.2), поэтому имя без предлога. */}
          {(item.masterName || item.salonName) && (
            <span className="cr-card__master">
              {[item.masterName, item.salonName].filter(Boolean).join(" · ")}
            </span>
          )}
          <span className="cr-card__when">{item.whenLabel}</span>
          {/* DRF-2172: без цены строки нет — «0 ₽» не рисуется (§103). */}
          {priceLabel && <span className="cr-card__price">{priceLabel}</span>}
          {/* Адрес — у предстоящих: туда ещё ехать. У прошедших макет его не
              рисует, он остаётся в карточке записи. */}
          {section === "upcoming" && (
            <span className="cr-card__address">{visitAddressText(item.address)}</span>
          )}
          {(!isOrdinaryStatus(item.status, section) || item.paymentState) && (
            <span className="cr-card__badges">
              {/* «В пути» — словами о ходе, не «Отменена»/«Перенесена» (DRF-2346). */}
              {inProgress ? (
                <span className="cr-card__progress">{inProgress}</span>
              ) : (
                !isOrdinaryStatus(item.status, section) && <StatusBadge rendering={rendering} />
              )}
              <PaymentStatusBadge state={item.paymentState} />
            </span>
          )}
          {section === "history" && item.canRate && (
            <span className="cr-card__hint">
              <RecordIcon name="star" size={16} />
              Можно оставить отзыв
            </span>
          )}
        </span>
      </span>
      <span className="cr-card__chevron">
        <RecordIcon name="chevron" />
      </span>
    </button>
  );
}

/** Заглушка карточки на время загрузки; для читающего с экрана её нет. */
export function RecordCardSkeleton() {
  return (
    <div className="cr-card cr-card--skeleton" aria-hidden="true">
      <span className="cr-thumb cr-thumb--card skeleton" />
      <span className="cr-card__text">
        <span className="skeleton cr-skel cr-skel--title" />
        <span className="skeleton cr-skel cr-skel--line" />
        <span className="skeleton cr-skel cr-skel--line" />
      </span>
    </div>
  );
}

interface SummaryProps {
  photoUrl: string | null | undefined;
  masterName: string;
  serviceName: string;
  /** Строки под услугой: время, цена — что нужно этому кадру. */
  rows: { icon: "calendar" | "price"; label?: string; text: string }[];
}

/** Мини-карточка: о какой записи идёт речь на экране подтверждения/результата. */
export function RecordSummary({ photoUrl, masterName, serviceName, rows }: SummaryProps) {
  return (
    <div className="cr-summary">
      <div className="cr-summary__head">
        <MasterThumb photoUrl={photoUrl} name={masterName} size="tile" />
        <span className="cr-summary__service">{serviceName}</span>
      </div>
      {rows.map((row) => (
        <div key={`${row.icon}-${row.label ?? ""}-${row.text}`} className="cr-summary__row">
          <RecordIcon name={row.icon} />
          <span>
            {row.label && <strong>{row.label} </strong>}
            {row.text}
          </span>
        </div>
      ))}
    </div>
  );
}
