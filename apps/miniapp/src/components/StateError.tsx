/**
 * Standardised error state per spec §7.4.
 *
 * Auto-classifies by `slug` / status:
 *   - network → "Не получилось загрузить. Проверьте интернет и попробуйте снова."
 *   - 5xx → "Что-то у нас не получается прямо сейчас."
 *   - 403 → "Этот раздел сейчас недоступен."
 *   - other → falls back to detail
 *
 * Always offers retry button. Server error path also offers «Сообщить
 * студии» which deeplinks the bot DM with pre-filled context.
 */

import { ApiError } from "../lib/api";

interface Props {
  err: unknown;
  onRetry: () => void;
  screenId?: string;
}

function pickCopy(err: unknown): { headline: string; offerEscalate: boolean } {
  if (err instanceof ApiError) {
    if (err.status >= 500) {
      return { headline: "Что-то у нас не получается прямо сейчас.", offerEscalate: true };
    }
    if (err.status === 403) {
      return { headline: "Этот раздел сейчас недоступен.", offerEscalate: false };
    }
    return { headline: err.detail || "Не получилось загрузить.", offerEscalate: false };
  }
  return {
    headline: "Не получилось загрузить. Проверьте интернет и попробуйте снова.",
    offerEscalate: false,
  };
}

export function StateError({ err, onRetry, screenId }: Props) {
  const { headline, offerEscalate } = pickCopy(err);
  return (
    <div className="callout callout--danger" role="alert">
      <p style={{ margin: 0 }}>{headline}</p>
      <div style={{ marginTop: "var(--s-3)", display: "flex", gap: "var(--s-2)" }}>
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Попробовать снова
        </button>
        {offerEscalate && screenId && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              // Q-FT8: opens bot DM via MAX deeplink so customer can
              // reach staff. Implementation lands in 4b polish.
              window.location.href = `https://max.ru/?prefill=miniapp-error:${screenId}`;
            }}
          >
            Сообщить студии
          </button>
        )}
      </div>
    </div>
  );
}
