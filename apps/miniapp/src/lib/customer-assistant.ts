/**
 * Разговор с Ayla внутри клиентского Mini App (DRF-2799).
 *
 *   GET  /api/v1/customer/assistant/history → { messages }
 *   POST /api/v1/customer/assistant/ask     → { answer, buttons, pending_action, cards }
 *
 * Решение владельца 06.10.2026: «диалог продолжается там, где начат». Сервер
 * прогоняет вопрос через тот же ход, что отвечает в чате бота, — та же
 * безопасность и та же нить, — а ответ отдаёт сюда, а не в MAX.
 *
 * `confirm` у клиента не нужен: подтверждения у бота — кнопками, и они
 * приходят быстрыми ответами. Предложения (`pending_action`) сервер клиенту
 * не присылает, поэтому `confirm` здесь не вызывается никогда.
 */
import type { AylaChatApi, AylaChatConfirmResult, AylaQuickReply } from "../components/AylaChat";
import { request } from "./api";

interface AskResponse {
  answer: string;
  buttons?: AylaQuickReply[];
  pending_action: null;
  cards?: [];
}

/** Уникальный id вопроса: повтор того же запроса сервер не превращает во второй ход. */
function requestId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (d) =>
    (Number(d) ^ (Math.random() * 16 >> (Number(d) / 4))).toString(16),
  );
}

export const customerAylaApi: AylaChatApi = {
  history: () => request("/assistant/history"),
  ask: (text) =>
    request<AskResponse>("/assistant/ask", {
      method: "POST",
      body: JSON.stringify({ text, request_id: requestId() }),
    }),
  confirm: (): Promise<AylaChatConfirmResult> =>
    Promise.reject(new Error("confirm is not used on the customer surface")),
};
