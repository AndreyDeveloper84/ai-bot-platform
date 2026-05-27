/**
 * Booking message modal — «Сообщить по записи».
 *
 * Spec: `docs/design/policies/ayla-mediated-messaging.md` §4 (quick
 * reasons taxonomy) + §5 (UX flow happy path).
 *
 * Phase B scope:
 *   - 4 quick reason chips (⏰ Опаздываю / 🚪 Не могу найти вход /
 *     📋 Уточнить подготовку / ✎ Другое).
 *   - Optional free text (for non-«Опаздываю» reasons — chip flow keeps
 *     «Опаздываю» as instant-send-via-text, simplified vs the full AMM
 *     §5.3a sub-option ladder; W4 backend reports a generic delay
 *     placeholder in the chip variant and Phase C will plug the
 *     sub-option ladder).
 *   - Toast confirmation per AMM §5.3b step 5.
 *
 * Voice locks (memory + Tau §10.5):
 *   - Title: «Сообщить по записи» — NOT «Написать» (F1 lock).
 *   - «ты» canonical.
 *   - No exclamation marks.
 *
 * Accessibility (WCAG):
 *   - role="dialog" + aria-modal=true so SR users get modal semantics.
 *   - Focus moves to the first chip on open.
 *   - Tab/Shift+Tab trapped within the panel.
 *   - Escape dismisses.
 *   - Backdrop tap dismisses.
 *
 * The emoji chips are ALLOWED here (AMM §4 chip taxonomy specifies
 * emoji per-chip — they're not status icons). The status-badge SVG
 * rule applies to permanent UI (per Tau §6 anti-pattern), not to
 * one-shot modal reason selection.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  sendBookingMessage,
  type BookingDetail,
  type BookingListItem,
  type MessageReason,
} from "../lib/customer-records";

type Reason = {
  key: MessageReason;
  emoji: string;
  label: string;
  /** When true, tapping the chip immediately sends (no free-text step). */
  instant: boolean;
  /** Free-text prompt shown when `instant=false`. */
  textPrompt?: string;
  /** Optional pre-filled body text for instant chips. */
  instantBody?: string;
};

const REASONS: Reason[] = [
  {
    key: "running_late",
    emoji: "⏰",
    label: "Опаздываю",
    instant: true,
    instantBody: "Опаздываю — подойду чуть позже.",
  },
  {
    key: "cannot_find_entrance",
    emoji: "🚪",
    label: "Не могу найти вход",
    instant: false,
    textPrompt: "Опиши где ты сейчас или что видишь.",
  },
  {
    key: "ask_about_preparation",
    emoji: "📋",
    label: "Уточнить подготовку",
    instant: false,
    textPrompt: "Что хочешь узнать про подготовку?",
  },
  {
    key: "other",
    emoji: "✎",
    label: "Другое",
    instant: false,
    textPrompt: "Что хочешь сказать?",
  },
];

interface Props {
  /** Open / closed state — caller controls. */
  open: boolean;
  /** Booking context for the modal header + send payload. */
  booking: BookingListItem | BookingDetail;
  onClose: () => void;
  /**
   * Optional toast handler — caller can surface the confirmation outside
   * the modal (e.g. after the modal closes). When omitted, the modal
   * stays open showing an inline confirmation step.
   */
  onSent?: (result: { masterFirstName: string; tenantName: string }) => void;
}

type ModalStep =
  | { kind: "select" }
  | { kind: "compose"; reason: Reason }
  | { kind: "sending" }
  | { kind: "sent"; masterFirstName: string; tenantName: string }
  | { kind: "error"; message: string };

export function BookingMessageModal({ open, booking, onClose, onSent }: Props) {
  const [step, setStep] = useState<ModalStep>({ kind: "select" });
  const [text, setText] = useState<string>("");
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  // Reset state every time the modal opens.
  useEffect(() => {
    if (!open) return;
    setStep({ kind: "select" });
    setText("");
  }, [open]);

  // Escape dismiss.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  // Focus management — snapshot on open, restore on close, trap Tab.
  useEffect(() => {
    if (!open) return;
    if (typeof document !== "undefined") {
      const active = document.activeElement;
      restoreFocusRef.current = active instanceof HTMLElement ? active : null;
    }
    const panel = panelRef.current;
    if (!panel) return;

    const firstItem = panel.querySelector<HTMLElement>(
      'button, textarea, [tabindex="0"]',
    );
    firstItem?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab" || !panel) return;
      const focusable = panel.querySelectorAll<HTMLElement>(
        'button, textarea, [href], [tabindex="0"]',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const target = restoreFocusRef.current;
      if (target && typeof document !== "undefined" && document.contains(target)) {
        target.focus();
      }
      restoreFocusRef.current = null;
    };
  }, [open]);

  const handleReasonClick = useCallback(
    async (reason: Reason) => {
      if (!reason.instant) {
        setStep({ kind: "compose", reason });
        setText("");
        return;
      }
      // Instant send — chip flow with a static body.
      setStep({ kind: "sending" });
      try {
        const result = await sendBookingMessage({
          booking_id: booking.booking_id,
          reason: reason.key,
          text: reason.instantBody,
        });
        if (!result.delivered) {
          setStep({
            kind: "error",
            message: "Не получилось передать. Попробуй ещё раз.",
          });
          return;
        }
        setStep({
          kind: "sent",
          masterFirstName: result.master_first_name,
          tenantName: result.tenant_name,
        });
        onSent?.({
          masterFirstName: result.master_first_name,
          tenantName: result.tenant_name,
        });
      } catch {
        setStep({
          kind: "error",
          message: "Не получилось передать. Попробуй ещё раз.",
        });
      }
    },
    [booking.booking_id, onSent],
  );

  const handleSubmitCompose = useCallback(async () => {
    if (step.kind !== "compose") return;
    const trimmed = text.trim();
    if (!trimmed) return;
    setStep({ kind: "sending" });
    try {
      const result = await sendBookingMessage({
        booking_id: booking.booking_id,
        reason: step.reason.key,
        text: trimmed,
      });
      if (!result.delivered) {
        setStep({
          kind: "error",
          message: "Не получилось передать. Попробуй ещё раз.",
        });
        return;
      }
      setStep({
        kind: "sent",
        masterFirstName: result.master_first_name,
        tenantName: result.tenant_name,
      });
      onSent?.({
        masterFirstName: result.master_first_name,
        tenantName: result.tenant_name,
      });
    } catch {
      setStep({
        kind: "error",
        message: "Не получилось передать. Попробуй ещё раз.",
      });
    }
  }, [booking.booking_id, onSent, step, text]);

  if (!open) return null;

  return (
    <div className="records-msg-modal" role="presentation">
      <button
        type="button"
        className="records-msg-modal__backdrop"
        aria-label="Закрыть"
        onClick={onClose}
      />
      <div
        ref={panelRef}
        className="records-msg-modal__panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="records-msg-title"
      >
        <div className="records-msg-modal__grip" aria-hidden="true" />
        <h2 id="records-msg-title" className="records-msg-modal__title">
          Сообщить по записи
        </h2>
        <p className="records-msg-modal__booking-context">
          {booking.datetime_relative_label} · {booking.service_name} · у{" "}
          {booking.master_first_name}
        </p>

        {step.kind === "select" && (
          <>
            <p className="records-msg-modal__prompt">Что хочешь сказать?</p>
            <ul className="records-msg-modal__reasons">
              {REASONS.map((r) => (
                <li key={r.key}>
                  <button
                    type="button"
                    className="records-msg-modal__reason-chip"
                    onClick={() => void handleReasonClick(r)}
                  >
                    <span
                      className="records-msg-modal__reason-emoji"
                      aria-hidden="true"
                    >
                      {r.emoji}
                    </span>
                    <span className="records-msg-modal__reason-label">
                      {r.label}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="btn-secondary records-msg-modal__cancel"
              onClick={onClose}
            >
              Отмена
            </button>
          </>
        )}

        {step.kind === "compose" && (
          <>
            <p className="records-msg-modal__prompt">
              <span
                className="records-msg-modal__reason-emoji"
                aria-hidden="true"
              >
                {step.reason.emoji}
              </span>{" "}
              {step.reason.label}
            </p>
            <label
              htmlFor="records-msg-text"
              className="records-msg-modal__text-label"
            >
              {step.reason.textPrompt ?? "Что хочешь сказать?"}
            </label>
            <textarea
              id="records-msg-text"
              className="records-msg-modal__textarea"
              rows={4}
              value={text}
              maxLength={500}
              onChange={(e) => setText(e.target.value)}
              placeholder=""
            />
            <div className="records-msg-modal__action-row">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setStep({ kind: "select" })}
              >
                Назад
              </button>
              <button
                type="button"
                className="btn-secondary records-msg-modal__send"
                onClick={() => void handleSubmitCompose()}
                disabled={!text.trim()}
              >
                Отправить
              </button>
            </div>
          </>
        )}

        {step.kind === "sending" && (
          <div className="records-msg-modal__sending" role="status" aria-live="polite">
            Передаю мастеру…
          </div>
        )}

        {step.kind === "sent" && (
          <div
            className="records-msg-modal__sent"
            role="status"
            aria-live="polite"
          >
            <p>
              Передала {step.masterFirstName} из {step.tenantName}. Напишу,
              когда она ответит.
            </p>
            <button
              type="button"
              className="btn-secondary"
              onClick={onClose}
              autoFocus
            >
              Ок
            </button>
          </div>
        )}

        {step.kind === "error" && (
          <div
            className="records-msg-modal__error"
            role="alert"
            aria-live="polite"
          >
            <p>{step.message}</p>
            <div className="records-msg-modal__action-row">
              <button
                type="button"
                className="btn-secondary"
                onClick={onClose}
              >
                Отмена
              </button>
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setStep({ kind: "select" })}
              >
                Попробовать снова
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
