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

  // Сверка утверждения с прочитанным (DRF-2347) — при отрисовке, а не в
  // эффекте: несоответствие обязано останавливать ТАМ ЖЕ, где сообщение
  // рождается, и быть поймано обычным `expect(...).toThrow()`. В отладочной
  // и тестовой сборке бросает; перед человеком сторож молчит — он проверяет
  // наши слова, а не его действия.
  if (visible && claim) assertClaim(claim);

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
    <div className="snackbar" role="status" aria-live="polite">
      <span style={{ flex: 1 }}>{message}</span>
      {actionLabel && onAction && (
        <button
          type="button"
          className="snackbar__action"
          onClick={() => {
            onAction();
            onDismiss?.();
          }}
        >
          {actionLabel}
        </button>
      )}
    </div>
  );
}
