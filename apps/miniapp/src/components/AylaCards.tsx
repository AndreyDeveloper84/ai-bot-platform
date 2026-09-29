/**
 * Карточки Ayla для мастера — макет DRF-1187 (DRF-2153, М-5).
 *
 * Структура вместо абзаца: окна свободного времени со строками и
 * «Создать запись», день с записями, «Нашла двух клиентов…» с вариантами
 * без телефона, выбор услуги, «Это время занято» с вариантами рядом,
 * «Последние известные данные» + «Проверить снова». Данные приходят от
 * сервера уже в форме карточки (`AylaCard`); здесь — только рисование.
 *
 * Тексты — с картинки макета дословно (см. `COPY`); длительность окна —
 * «90 мин», не «1 ч 30 мин» (ruling (ж), §61).
 */
import { Link } from "react-router-dom";
import type {
  AylaBookingDetails,
  AylaCard,
  AylaSelect,
} from "../lib/master-api";

export const CARD_COPY = {
  free: "Свободно:",
  createBooking: "Создать запись",
  windowHint:
    "Нажмите на интервал, чтобы создать запись с этим окном. Или нажмите «Создать запись», чтобы выбрать время позже.",
  staleTitle: "Не удалось проверить актуальное расписание.",
  staleLast: "Последние известные данные:",
  staleFooter: "Расписание могло измениться.",
  recheck: "Проверить снова",
  dayOff: "Выходной",
  noVisits: "Записей нет",
  slotTakenTitle: "Это время занято",
  slotTakenGone: (range: string) => `${range} больше недоступно.`,
  slotTakenNear: "Свободно рядом:",
  pickAnother: "Выбрать другое время",
  showAllServices: "Показать все услуги",
  reviewTitle: "Проверьте запись",
  reviewHint: "После подтверждения запись будет создана в расписании.",
  createdTitle: "Запись создана",
  createdHint: "Сервер подтвердил результат.",
  minutes: (n: number) => `${n} мин`,
} as const;

const MONTHS_RU = [
  "января",
  "февраля",
  "марта",
  "апреля",
  "мая",
  "июня",
  "июля",
  "августа",
  "сентября",
  "октября",
  "ноября",
  "декабря",
];

/** «Завтра, 21 августа» / «Сегодня, 20 августа» / «21 августа». */
export function dayTitle(iso: string, today: Date = new Date()): string {
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  const date = `${d} ${MONTHS_RU[m - 1]}`;
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const target = new Date(y, m - 1, d);
  const diff = Math.round((target.getTime() - t.getTime()) / 86_400_000);
  if (diff === 0) return `Сегодня, ${date}`;
  if (diff === 1) return `Завтра, ${date}`;
  return date;
}

/** «10:30»–«12:00» → 90 (минут). */
export function windowMinutes(start: string, end: string): number {
  const [sh, sm] = start.split(":").map(Number);
  const [eh, em] = end.split(":").map(Number);
  return (eh ?? 0) * 60 + (em ?? 0) - ((sh ?? 0) * 60 + (sm ?? 0));
}

function InfoHint({ text }: { text: string }) {
  return (
    <p className="ayla-card__hint">
      <span aria-hidden="true">ⓘ</span> {text}
    </p>
  );
}

export interface AylaCardsProps {
  cards: AylaCard[];
  /** Выбор из карточки — новая фраза + уточнение (`select`). */
  onSelect: (text: string, select: AylaSelect) => void;
  /** Повторить последний вопрос («Проверить снова»). */
  onRecheck: () => void;
}

export function AylaCards({ cards, onSelect, onRecheck }: AylaCardsProps) {
  return (
    <>
      {cards.map((card, idx) => (
        <AylaCardView
          key={idx}
          card={card}
          onSelect={onSelect}
          onRecheck={onRecheck}
        />
      ))}
    </>
  );
}

function AylaCardView({
  card,
  onSelect,
  onRecheck,
}: {
  card: AylaCard;
  onSelect: AylaCardsProps["onSelect"];
  onRecheck: AylaCardsProps["onRecheck"];
}) {
  switch (card.kind) {
    case "free_windows":
      return <FreeWindowsCard card={card} onRecheck={onRecheck} />;
    case "day":
      return (
        <section className="ayla-card" aria-label="Записи дня">
          <p className="ayla-card__title">{dayTitle(card.date)}</p>
          {card.visits.length === 0 ? (
            <p className="ayla-card__row">{CARD_COPY.noVisits}</p>
          ) : (
            <ul className="ayla-card__list">
              {card.visits.map((v, i) => (
                <li key={i} className="ayla-card__row">
                  <span className="ayla-card__row-main">
                    {v.time} · {v.client}
                  </span>
                  <span className="ayla-card__row-meta">
                    {v.service}
                    {v.duration_min
                      ? ` · ${CARD_COPY.minutes(v.duration_min)}`
                      : ""}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </section>
      );
    case "clarify_client":
      return (
        <section className="ayla-card" aria-label="Уточнение клиента">
          <ul className="ayla-card__list">
            {card.options.map((o) => (
              <li key={o.client_id}>
                <button
                  type="button"
                  className="ayla-card__option"
                  onClick={() => onSelect(o.label, { client_id: o.client_id })}
                >
                  {o.label} <span aria-hidden="true">›</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      );
    case "choose_service":
      return (
        <section className="ayla-card" aria-label="Выбор услуги">
          <ul className="ayla-card__list">
            {card.options.map((o) => (
              <li key={o.service_id}>
                <button
                  type="button"
                  className="ayla-card__option"
                  onClick={() => onSelect(o.name, {})}
                >
                  {o.name}
                  {o.duration_min
                    ? ` · ${CARD_COPY.minutes(o.duration_min)}`
                    : ""}
                </button>
              </li>
            ))}
          </ul>
        </section>
      );
    case "slot_taken":
      return (
        <section
          className="ayla-card ayla-card--danger"
          role="alert"
          aria-label={CARD_COPY.slotTakenTitle}
        >
          <p className="ayla-card__title">
            <span aria-hidden="true">⚠</span> {CARD_COPY.slotTakenTitle}
          </p>
          {card.range ? (
            <p className="ayla-card__row">
              {CARD_COPY.slotTakenGone(card.range)}
            </p>
          ) : null}
          {card.alternatives.length > 0 ? (
            <>
              <p className="ayla-card__row">{CARD_COPY.slotTakenNear}</p>
              <ul className="ayla-card__list">
                {card.alternatives.map((a) => (
                  <li key={a.time}>
                    <button
                      type="button"
                      className="ayla-card__option"
                      onClick={() =>
                        onSelect(
                          `Запиши на ${a.time}`,
                          a.start_at ? { start_at: a.start_at } : {},
                        )
                      }
                    >
                      {a.time} <span aria-hidden="true">›</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {card.book_url ? (
            <Link
              to={card.book_url}
              className="ayla-btn ayla-btn--secondary ayla-card__cta"
            >
              {CARD_COPY.pickAnother}
            </Link>
          ) : null}
        </section>
      );
    case "open":
      return (
        <Link
          to={card.url}
          className="ayla-btn ayla-btn--secondary ayla-card__cta"
        >
          {card.label}
        </Link>
      );
    default:
      return null;
  }
}

function FreeWindowsCard({
  card,
  onRecheck,
}: {
  card: Extract<AylaCard, { kind: "free_windows" }>;
  onRecheck: () => void;
}) {
  const rows = (
    <ul className="ayla-card__list">
      {card.windows.map((w) => (
        <li key={`${w.start}-${w.end}`}>
          <Link to={w.book_url} className="ayla-card__option">
            <span className="ayla-card__row-main">
              {w.start}–{w.end}
            </span>
            <span className="ayla-card__row-meta">
              {CARD_COPY.minutes(windowMinutes(w.start, w.end))}
            </span>
            <span aria-hidden="true">›</span>
          </Link>
        </li>
      ))}
    </ul>
  );
  if (card.stale) {
    // Макет: «Не удалось проверить актуальное расписание.» / «Последние
    // известные данные:» / строки / «Расписание могло измениться.» /
    // «Проверить снова». Уверенного «свободно» здесь нет.
    return (
      <section
        className="ayla-card ayla-card--warning"
        role="status"
        aria-label={CARD_COPY.staleTitle}
      >
        <p className="ayla-card__title">
          <span aria-hidden="true">⚠</span> {CARD_COPY.staleTitle}
        </p>
        <p className="ayla-card__row">{CARD_COPY.staleLast}</p>
        {rows}
        <p className="ayla-card__row ayla-card__row--muted">
          {CARD_COPY.staleFooter}
        </p>
        <button
          type="button"
          className="ayla-btn ayla-btn--secondary ayla-card__cta"
          onClick={onRecheck}
        >
          {CARD_COPY.recheck}
        </button>
      </section>
    );
  }
  return (
    <section className="ayla-card" aria-label="Свободное время">
      <p className="ayla-card__title">{dayTitle(card.date)}</p>
      {card.day_off ? (
        <p className="ayla-card__row">{CARD_COPY.dayOff}</p>
      ) : (
        <>
          <p className="ayla-card__row">{CARD_COPY.free}</p>
          {rows}
        </>
      )}
      {card.book_url ? (
        <Link
          to={card.book_url}
          className="ayla-btn ayla-btn--secondary ayla-card__cta"
        >
          {CARD_COPY.createBooking}
        </Link>
      ) : null}
      <InfoHint text={CARD_COPY.windowHint} />
    </section>
  );
}

/** Карточка предложения записи (3A) — строки макета с иконками. */
export function BookingReviewRows({
  details,
}: {
  details: AylaBookingDetails;
}) {
  return (
    <ul className="ayla-card__list ayla-review">
      <li className="ayla-review__row">
        <span aria-hidden="true">👤</span> {details.client}
      </li>
      <li className="ayla-review__row">
        <span aria-hidden="true">✂</span> {details.service} ·{" "}
        {CARD_COPY.minutes(details.duration_min)}
      </li>
      <li className="ayla-review__row">
        <span aria-hidden="true">📅</span> {details.date}
      </li>
      <li className="ayla-review__row">
        <span aria-hidden="true">🕒</span> {details.time_range ?? details.time}
      </li>
    </ul>
  );
}

/** Карточка результата (3B): ✓ «Запись создана» + три строки + дверь. */
export function BookingCreatedCard({
  details,
  open,
}: {
  details: AylaBookingDetails | null | undefined;
  open: { url: string; label: string } | null | undefined;
}) {
  const day = details ? details.date.split(",")[0] : "";
  return (
    <section
      className="ayla-card ayla-card--success"
      role="status"
      aria-label={CARD_COPY.createdTitle}
    >
      <p className="ayla-card__title">
        <span aria-hidden="true">✓</span> {CARD_COPY.createdTitle}
      </p>
      {details ? (
        <ul className="ayla-card__list">
          <li className="ayla-card__row">{details.client}</li>
          <li className="ayla-card__row">
            {day} · {details.time_range ?? details.time}
          </li>
          <li className="ayla-card__row">
            {details.service} · {CARD_COPY.minutes(details.duration_min)}
          </li>
        </ul>
      ) : null}
      {open ? (
        <Link
          to={open.url}
          className="ayla-btn ayla-btn--secondary ayla-card__cta"
        >
          {open.label}
        </Link>
      ) : null}
      <InfoHint text={CARD_COPY.createdHint} />
    </section>
  );
}

export { InfoHint };
