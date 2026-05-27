/**
 * Customer booking detail screen — Tier 1 Priority 5 Phase B (R3).
 *
 * Spec: `docs/screens/customer-records-flow.md` §5 (R3 — booking detail,
 * the «main screen» per tech lead). Single tall scroll per Q-R-4
 * verdict + iOS-safe sticky CTA per ТЛ Q3 (upcoming only — past
 * statuses render inline).
 *
 * # Section order (Tau §5.1 founder explicit refinement)
 *
 *   1. Status header (prominent)
 *   2. Main recap (service / master / salon)
 *   3. Date/time
 *   4. Address + route
 *   5. Actions (sticky for upcoming; inline for past)
 *   6. Price / payment / refund if relevant
 *   7. Cancellation policy
 *   8. Note to master (if exists)
 *   9. Repeat booking / related actions
 *
 * Rationale: actionable concerns surface first; actions BEFORE price
 * reduce friction for quick decisions.
 *
 * # Voice locks
 *
 *   - «Сообщить по записи» (NOT «Написать») — F1 lock
 *   - «ты» canonical — F2 lock
 *   - «Отменена вами» NOT «Отменена тобой» — Records voice principle
 *   - «Не пришла» NOT «Не состоялась»
 *   - provider_cancelled: muted/warning, NOT sage-green positive
 *
 * # ТЛ Q4 — refund fields wired directly
 *
 *   refund_status / refund_amount / no_show_penalty come straight from
 *   BookingDetail. Frontend does NOT compute them from price/policy.
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { BookingMessageModal } from "../components/BookingMessageModal";
import { StatusBadge } from "../components/StatusBadge";
import { ApiError } from "../lib/api";
import { sectionFor, type CustomerVisibleStatus } from "../lib/booking-status";
import {
  BookingNotFoundError,
  getBookingDetail,
  getRepeatIntent,
  renderStatus,
  type BookingDetail,
} from "../lib/customer-records";

type Slice =
  | { kind: "loading" }
  | { kind: "ok"; data: BookingDetail }
  | { kind: "deleted" }
  | { kind: "error"; reason: "network" | "server" | "other" };

export function CustomerBookingDetailScreen() {
  const { bookingId } = useParams<{ bookingId: string }>();
  const navigate = useNavigate();
  const [slice, setSlice] = useState<Slice>({ kind: "loading" });
  const [messageOpen, setMessageOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const fetchDetail = useCallback(async () => {
    if (!bookingId) {
      setSlice({ kind: "deleted" });
      return;
    }
    setSlice({ kind: "loading" });
    try {
      const data = await getBookingDetail(bookingId);
      setSlice({ kind: "ok", data });
    } catch (e) {
      if (e instanceof BookingNotFoundError) {
        setSlice({ kind: "deleted" });
        return;
      }
      setSlice({
        kind: "error",
        reason:
          e instanceof ApiError && e.status >= 500
            ? "server"
            : e instanceof ApiError
              ? "other"
              : "network",
      });
    }
  }, [bookingId]);

  useEffect(() => {
    void fetchDetail();
  }, [fetchDetail]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  // ── handlers ─────────────────────────────────────────────────────────
  const handleBack = useCallback(() => {
    navigate("/customer/records");
  }, [navigate]);

  const handleRoute = useCallback(() => {
    if (slice.kind !== "ok") return;
    const url = slice.data.route_deeplink;
    if (url && typeof window !== "undefined") {
      // Open in new context — Mini App webview will hand off to maps.
      window.open(url, "_blank", "noopener,noreferrer");
    }
  }, [slice]);

  const handleReschedule = useCallback(() => {
    if (slice.kind !== "ok") return;
    navigate(`/my-visits/${slice.data.booking_id}/reschedule`);
  }, [navigate, slice]);

  const handleCancel = useCallback(() => {
    if (slice.kind !== "ok") return;
    // Cancel flow lives on /my-visits/:id — reuse existing modal.
    navigate(`/my-visits/${slice.data.booking_id}`);
  }, [navigate, slice]);

  const handleRepeat = useCallback(async () => {
    if (slice.kind !== "ok") return;
    try {
      const intent = await getRepeatIntent(slice.data.booking_id);
      if (intent.prefill_available && intent.booking_flow_url) {
        navigate(intent.booking_flow_url);
        return;
      }
      navigate("/customer/catalog");
    } catch {
      navigate("/customer/catalog");
    }
  }, [navigate, slice]);

  const handleReview = useCallback(() => {
    if (slice.kind !== "ok") return;
    navigate(`/feedback/${slice.data.booking_id}`);
  }, [navigate, slice]);

  // ── render branches ──────────────────────────────────────────────────
  if (slice.kind === "loading") {
    return (
      <div className="records-detail" lang="ru">
        <DetailHeader onBack={handleBack} />
        <main className="records-detail__main">
          <div
            className="skeleton"
            style={{ height: 28, width: "40%", marginBottom: "var(--s-3)" }}
            aria-hidden="true"
          />
          <div
            className="skeleton"
            style={{ height: 20, width: "70%", marginBottom: "var(--s-2)" }}
            aria-hidden="true"
          />
          <div
            className="skeleton"
            style={{ height: 16, width: "50%", marginBottom: "var(--s-4)" }}
            aria-hidden="true"
          />
          <div
            className="skeleton"
            style={{ height: 120, width: "100%" }}
            aria-hidden="true"
          />
        </main>
      </div>
    );
  }

  if (slice.kind === "deleted") {
    return (
      <div className="records-detail" lang="ru">
        <DetailHeader onBack={handleBack} />
        <main className="records-detail__main">
          <div className="records-detail__deleted">
            <p>Этой записи больше нет. Возможно, она была отменена.</p>
            <button
              type="button"
              className="btn-secondary"
              onClick={handleBack}
            >
              К списку записей
            </button>
          </div>
        </main>
      </div>
    );
  }

  if (slice.kind === "error") {
    return (
      <div className="records-detail" lang="ru">
        <DetailHeader onBack={handleBack} />
        <main className="records-detail__main">
          <div className="records-detail__error">
            <p>
              {slice.reason === "network"
                ? "Не получилось загрузить запись. Проверь интернет."
                : "Что-то пошло не так. Попробуй ещё раз через минуту."}
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => void fetchDetail()}
            >
              Обновить
            </button>
          </div>
        </main>
      </div>
    );
  }

  // OK branch.
  const d = slice.data;
  const { status: mapped, rendering } = renderStatus(d.status);
  // Status section classifier — drives sticky CTA visibility. Round-1
  // refactor: derive section from typed CustomerVisibleStatus via
  // `sectionFor` (single source of truth, BACKEND_ALIAS_MAP-aware).
  const section = sectionFor(mapped);
  const isUpcoming = section === "upcoming";
  const isPast = section === "history" && mapped !== "unknown";

  const formatPrice = (v: number) => `${v.toLocaleString("ru-RU")} ₽`;
  const formattedRescheduledOrigin = formatRescheduledOrigin(
    d.rescheduled_origin_dt,
  );

  return (
    <div className="records-detail" lang="ru">
      <DetailHeader onBack={handleBack} />
      <main
        className={`records-detail__main${isUpcoming ? " records-detail__main--with-sticky" : ""}`}
      >
        {/* 1. Status header. */}
        <section className="records-detail__section records-detail__status">
          <StatusBadge rendering={rendering} />
          {d.status === "rescheduled" && formattedRescheduledOrigin && (
            <p className="records-detail__rescheduled-from">
              Перенесена с {formattedRescheduledOrigin}.
            </p>
          )}
        </section>

        {/* 2. Main recap. */}
        <section className="records-detail__section records-detail__recap">
          <p className="records-detail__service">
            {d.service_name}
            <span className="records-detail__duration">
              {" "}
              · {d.duration_min} минут
            </span>
          </p>
          <p className="records-detail__master">
            у {d.master_first_name}
          </p>
          <p className="records-detail__tenant">{d.tenant_name}</p>
        </section>

        {/* 3. Date/time. */}
        <section className="records-detail__section records-detail__when-section">
          <p className="records-detail__when">{d.datetime_relative_label}</p>
        </section>

        {/* 4. Address + route. */}
        <section className="records-detail__section">
          <p className="records-detail__address">{d.address_full}</p>
          {isUpcoming && d.route_deeplink && (
            <button
              type="button"
              className="btn-secondary records-detail__route-btn"
              onClick={handleRoute}
            >
              Маршрут до салона
            </button>
          )}
        </section>

        {/* 5. Past-status context (cancelled / no_show / refund). For
             upcoming, actions live in the sticky CTA at bottom. */}
        {isPast && (
          <section className="records-detail__section records-detail__past-context">
            <h2 className="records-detail__section-title">Что произошло</h2>
            <PastContext detail={d} mapped={mapped} />
          </section>
        )}

        {/* 6. Price / payment / refund. */}
        {(typeof d.price_approx === "number" ||
          d.refund_status === "completed" ||
          d.refund_status === "pending" ||
          typeof d.no_show_penalty === "number") && (
          <section className="records-detail__section">
            <h2 className="records-detail__section-title">Стоимость</h2>
            {typeof d.price_approx === "number" && (
              <p className="records-detail__price">
                {isUpcoming ? `~${formatPrice(d.price_approx)}` : formatPrice(d.price_approx)}
              </p>
            )}
            {d.refund_status === "completed" &&
              typeof d.refund_amount === "number" && (
                <p className="records-detail__refund">
                  Возврат завершён · {formatPrice(d.refund_amount)}.
                </p>
              )}
            {d.refund_status === "pending" && (
              <p className="records-detail__refund">
                Возврат в обработке. Обычно деньги приходят в течение 3 дней.
              </p>
            )}
            {typeof d.no_show_penalty === "number" && d.no_show_penalty > 0 && (
              <p className="records-detail__penalty">
                Удержано {formatPrice(d.no_show_penalty)} — по условиям отмены.
              </p>
            )}
          </section>
        )}

        {/* 7. Cancellation policy — upcoming only. */}
        {isUpcoming && d.cancellation_policy_text && (
          <section className="records-detail__section">
            <h2 className="records-detail__section-title">Политика отмены</h2>
            <p className="records-detail__policy">
              {d.cancellation_policy_text}
            </p>
          </section>
        )}

        {/* 8. Note to master. */}
        {isUpcoming && (
          <section className="records-detail__section">
            <h2 className="records-detail__section-title">Заметка мастеру</h2>
            {d.note_to_master ? (
              <p className="records-detail__note">{d.note_to_master}</p>
            ) : (
              <p className="records-detail__note records-detail__note--empty">
                Заметки пока нет.
              </p>
            )}
            {/* Edit-note flow lives on existing /my-visits/:id detail screen — keep
                wiring single-source. */}
          </section>
        )}

        {/* 9. Past: repeat / review actions. */}
        {isPast && (
          <section className="records-detail__section records-detail__past-actions">
            {d.repeat_intent_available && (
              <button
                type="button"
                className="btn-secondary records-detail__primary-cta"
                onClick={() => void handleRepeat()}
              >
                Записаться ещё
              </button>
            )}
            {d.can_review && !d.review_submitted && (
              <button
                type="button"
                className="btn-secondary"
                onClick={handleReview}
              >
                Оставить отзыв
              </button>
            )}
          </section>
        )}
      </main>

      {/* Sticky CTA — UPCOMING ONLY per ТЛ Q3. iOS-safe via
          env(safe-area-inset-bottom). Past statuses use inline actions
          (above). */}
      {isUpcoming && (
        <div
          className="records-detail__sticky-cta"
          role="region"
          aria-label="Действия по записи"
        >
          <button
            type="button"
            className="btn-secondary records-detail__sticky-cta-msg"
            onClick={() => setMessageOpen(true)}
          >
            Сообщить по записи
          </button>
          <div className="records-detail__sticky-cta-row">
            <button
              type="button"
              className="btn-secondary"
              onClick={handleReschedule}
            >
              Перенести
            </button>
            <button
              type="button"
              className="btn-secondary btn-secondary--danger"
              onClick={handleCancel}
            >
              Отменить
            </button>
          </div>
        </div>
      )}

      {/* AMM modal. */}
      <BookingMessageModal
        open={messageOpen}
        booking={d}
        onClose={() => setMessageOpen(false)}
        onSent={(r) =>
          setToast(`Передала ${r.masterFirstName} из ${r.tenantName}.`)
        }
      />

      {toast && (
        <div className="records-screen__toast" role="status" aria-live="polite">
          {toast}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function DetailHeader({ onBack }: { onBack: () => void }) {
  return (
    <header className="records-detail__header" role="banner">
      <button
        type="button"
        className="records-detail__back"
        aria-label="Назад к записям"
        onClick={onBack}
      >
        <span aria-hidden="true">←</span>
      </button>
      <h1 className="records-detail__title">Запись</h1>
    </header>
  );
}

function PastContext({
  detail,
  mapped,
}: {
  detail: BookingDetail;
  mapped: CustomerVisibleStatus | "unknown";
}) {
  // Round-1 refactor: drive PastContext from typed CustomerVisibleStatus
  // (not raw backend string). Aliases like `cancel_requested`, `noshow`,
  // `chargeback`, `refunded`, `finished` are now handled via
  // BACKEND_ALIAS_MAP in booking-status.ts — PastContext just consumes
  // the 8-bucket vocabulary. `cancellation_context` retained for the
  // «Отменена вами» vs «Отменена салоном» disambiguation dimension.
  const ctx = detail.cancellation_context;

  if (mapped === "completed") {
    return (
      <p className="records-detail__context-text">
        Визит прошёл{" "}
        {detail.completed_at
          ? formatCompletedDate(detail.completed_at)
          : "вовремя"}
        .
      </p>
    );
  }
  // «Отменена вами» — customer-initiated cancellation. Disambiguation
  // via cancellation_context preserved.
  if (mapped === "cancelled_customer" || ctx === "by_customer") {
    return (
      <>
        <p className="records-detail__context-text">
          Отменена вами{" "}
          {detail.completed_at
            ? formatCompletedDate(detail.completed_at)
            : "ранее"}
          .
        </p>
        {detail.refund_status === "none" && (
          <p className="records-detail__context-text">
            Без штрафа — отмена была вовремя.
          </p>
        )}
      </>
    );
  }
  if (mapped === "provider_cancelled" || ctx === "by_provider") {
    return (
      <>
        <p className="records-detail__context-text">
          Запись отменена салоном.
        </p>
        {detail.refund_status === "completed" && (
          <p className="records-detail__context-text">
            Полный возврат — это не твоя вина.
          </p>
        )}
        {detail.refund_status === "pending" && (
          <p className="records-detail__context-text">
            Возврат уже в банке — обычно приходит в течение 3 дней.
          </p>
        )}
      </>
    );
  }
  if (mapped === "no_show" || ctx === "by_system_no_show") {
    // Round-1 fix: penalty copy gated on actual non-zero penalty.
    // Force-majeure / grace-period flows ship no_show without retention;
    // unconditional «удержал часть оплаты» would lie. Also grammar:
    // «Запись отмечена как «Не пришла»» (feminine subject + quoted
    // brand status label).
    const hasPenalty =
      typeof detail.no_show_penalty === "number" && detail.no_show_penalty > 0;
    return (
      <p className="records-detail__context-text">
        Запись отмечена как «Не пришла».
        {hasPenalty && " По условиям отмены салон удержал часть оплаты."}
      </p>
    );
  }
  if (mapped === "refund_pending") {
    return (
      <p className="records-detail__context-text">
        Возврат ушёл в банк — обычно приходит в течение 3 дней.
      </p>
    );
  }
  if (mapped === "refund_completed") {
    return (
      <p className="records-detail__context-text">
        Возврат завершён. Деньги вернулись на карту.
      </p>
    );
  }
  return null;
}

function formatRescheduledOrigin(iso?: string): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return null;
    const day = d.getDate();
    const MONTHS = [
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
    const m = MONTHS[d.getMonth()];
    const hh = d.getHours().toString().padStart(2, "0");
    const mm = d.getMinutes().toString().padStart(2, "0");
    return `${day} ${m} ${hh}:${mm}`;
  } catch {
    return null;
  }
}

function formatCompletedDate(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const day = d.getDate();
    const MONTHS = [
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
    const m = MONTHS[d.getMonth()];
    return `${day} ${m}`;
  } catch {
    return "";
  }
}
