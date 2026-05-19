/**
 * Standardised error state per spec §7.4.
 *
 * Auto-classifies by `slug` / status:
 *   - network → "Не получилось загрузить. Проверьте интернет и попробуйте снова."
 *   - 5xx → "Что-то у нас не получается прямо сейчас."
 *   - 403 → "Этот раздел сейчас недоступен."
 *   - other → falls back to detail
 *
 * Always offers retry. The escalation button («Сообщить студии») used
 * to deeplink to `https://max.ru/?prefill=…`, but that URL never opened
 * the bot DM — placeholder from an early Q-FT8 stub. Removed in P5-4.
 * Customers can reach the studio from the bot chat directly; a proper
 * "report to studio" path will land with the bot-side handle config
 * (deferred to 5b).
 */

import { ApiError } from "../lib/api";

interface Props {
  err: unknown;
  onRetry: () => void;
  /** @deprecated kept for the existing call sites; no longer surfaced. */
  screenId?: string;
}

function pickCopy(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status >= 500) return "Что-то у нас не получается прямо сейчас.";
    if (err.status === 403) return "Этот раздел сейчас недоступен.";
    return err.detail || "Не получилось загрузить.";
  }
  return "Не получилось загрузить. Проверьте интернет и попробуйте снова.";
}

export function StateError({ err, onRetry }: Props) {
  const headline = pickCopy(err);
  return (
    <div className="callout callout--danger" role="alert">
      <p style={{ margin: 0 }}>{headline}</p>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Попробовать снова
        </button>
      </div>
    </div>
  );
}
