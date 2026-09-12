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
import { authErrorCopy, isAuthRefusalSlug } from "../lib/auth-error-copy";

interface Props {
  err: unknown;
  onRetry: () => void;
  /** @deprecated kept for the existing call sites; no longer surfaced. */
  screenId?: string;
}

type Copy = { title?: string; body: string };

function pickCopy(err: unknown): Copy {
  if (err instanceof ApiError) {
    // DRF-1319 D-1. Отказ входа (нет/протух initData, удалённый аккаунт,
    // сервер без токена) раньше падал в ветку `err.detail` и показывал
    // человеку «missing Authorization header» по-английски. Теперь — та
    // же копия, что на HelloScreen: одно состояние, одно имя. Проверка
    // стоит ПЕРЕД 5xx/403, потому что `server_misconfigured` — 500, а
    // `user_deleted` — 403, и общие фразы для них хуже точных.
    if (isAuthRefusalSlug(err.slug)) {
      const copy = authErrorCopy(err.slug);
      return { title: copy.title, body: copy.body };
    }
    if (err.status >= 500) return { body: "Что-то у нас не получается прямо сейчас." };
    if (err.status === 403) return { body: "Этот раздел сейчас недоступен." };
    return { body: err.detail || "Не получилось загрузить." };
  }
  return { body: "Не получилось загрузить. Проверьте интернет и попробуйте снова." };
}

export function StateError({ err, onRetry }: Props) {
  const copy = pickCopy(err);
  return (
    <div className="callout callout--danger" role="alert">
      {copy.title && (
        <p style={{ margin: 0, fontWeight: 600 }}>{copy.title}</p>
      )}
      <p style={{ margin: copy.title ? "var(--s-1) 0 0" : 0 }}>{copy.body}</p>
      <div style={{ marginTop: "var(--s-3)" }}>
        <button type="button" className="btn-secondary" onClick={onRetry}>
          Попробовать снова
        </button>
      </div>
    </div>
  );
}
