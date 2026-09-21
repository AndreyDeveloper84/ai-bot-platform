/**
 * Подсказка «как вернуться в чат», когда вернуть туда не вышло (DRF-2266/2268).
 *
 * `returnToChat()` пробует мост `close()`, потом ссылку на диалог бота; если
 * нет ни того, ни другого (web.max.ru без `close()`, ссылка не задана),
 * отвечает «stuck» — и экран рисует эту строку рядом с нажатой кнопкой.
 * Раньше в этом месте была тишина: «кнопка не работает».
 */

/** ЧЕРНОВИК владельцу (DRF-2266). */
export const CHAT_STUCK_HINT =
  "Вернись в чат с Ayla: закрой приложение крестиком вверху — чат останется под ним.";

export function ReturnToChatHint({ className = "return-to-chat-hint" }: { className?: string }) {
  return (
    <p className={className} role="status">
      {CHAT_STUCK_HINT}
    </p>
  );
}
