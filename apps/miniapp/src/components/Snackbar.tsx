/**
 * Snackbar primitive — undo-toast UX per spec §3.4.
 *
 * Auto-dismiss after `durationMs` (default 5000). Optional "Отменить"
 * action triggers `onUndo` AND dismisses immediately. The Snackbar
 * itself is stateless; the parent owns visibility + lifecycle.
 *
 * For the cancel-undo flow specifically, the server is the authority
 * on the 5-second window — the client merely matches the server's TTL
 * for UX. If the user taps undo after 5s, the server returns 409
 * `undo_window_elapsed` and the caller swaps in an "окно отмены
 * истекло" message via `onError`.
 */

import { useEffect, useRef } from "react";

import { assertClaim, type Claim } from "../lib/claims";

interface Props {
  visible: boolean;
  message: string;
  /**
   * DRF-2347 — что сообщение утверждает выполненным и чем это подтверждено.
   * Обязательно для сообщений о СДЕЛАННОМ; сообщения о запросе, ошибке и
   * ходе дела его не несут — им нечего доказывать.
   */
  claim?: Claim<unknown>;
  actionLabel?: string;
  onAction?: () => void;
  durationMs?: number;
  onTimeout?: () => void;
  onDismiss?: () => void;
}

export function Snackbar({
  visible,
  message,
  claim,
  actionLabel,
  onAction,
  durationMs = 5000,
  onTimeout,
  onDismiss,
}: Props) {
  const timerRef = useRef<number | null>(null);

  // Сверка утверждения с прочитанным (DRF-2347). В отладочной и тестовой
  // сборке несоответствие бросает — там это и ловится; перед человеком
  // сторож молчит: он проверяет наши слова, а не его действия.
  useEffect(() => {
    if (visible && claim) assertClaim(claim);
  }, [visible, claim]);

  useEffect(() => {
    if (!visible) {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      return;
    }
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      onTimeout?.();
    }, durationMs);
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [visible, durationMs, onTimeout]);

  if (!visible) return null;

  return (
    <div
      className="snackbar"
      role="status"
      aria-live="polite"
      style={{
        position: "fixed",
        left: "var(--s-3)",
        right: "var(--s-3)",
        bottom: "calc(var(--s-6) + var(--safe-bottom, 0px))",
        background: "var(--c-text-primary)",
        color: "var(--c-bg)",
        padding: "var(--s-3) var(--s-4)",
        borderRadius: "var(--r-md)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "var(--s-3)",
        boxShadow: "0 4px 16px rgba(0,0,0,0.18)",
        zIndex: 100,
      }}
    >
      <span style={{ flex: 1 }}>{message}</span>
      {actionLabel && onAction && (
        <button
          type="button"
          onClick={() => {
            onAction();
            onDismiss?.();
          }}
          style={{
            background: "transparent",
            color: "var(--c-accent-subtle)",
            border: "none",
            font: "inherit",
            fontWeight: 600,
            padding: "var(--s-1) var(--s-2)",
            cursor: "pointer",
          }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
