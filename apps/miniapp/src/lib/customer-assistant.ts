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
 * Подтверждений (`confirm`) у клиента нет: у бота они кнопками и приходят
 * быстрыми ответами; предложений (`pending_action`) и карточек (`cards`)
 * сервер клиенту не присылает.
 *
 * # Ключ запроса — у вызывающего (DRF-2953)
 *
 * `request_id` раньше рождался здесь, на каждый вызов заново, и серверная
 * защита от повтора не срабатывала ни разу: повтор всегда был новым ходом.
 * Теперь ключ держит экран и шлёт его повторно, пока не узнает исход хода.
 * На повтор сервер отвечает 409 `duplicate_request` с `details.turn`:
 *
 *   - `done`    — ход прошёл, ответ лежит в истории;
 *   - `failed`  — ход упал, ключ занят: спрашивать заново новым ключом;
 *   - `running` — ход ещё идёт (или исход неизвестен).
 */
import type { AylaChatMessage, AylaQuickReply } from "../components/AylaChat";
import { request } from "./api";

export interface AskResponse {
  answer: string;
  buttons?: AylaQuickReply[];
  pending_action: null;
  cards?: [];
}

/** Слаг 409 на повтор того же `request_id`. */
export const DUPLICATE_REQUEST = "duplicate_request";

/** Слаг 502: ход упал на сервере; его ключ остаётся занятым. */
export const TURN_FAILED = "turn_failed";

/** Новый ключ запроса. Один вопрос — один ключ на все его повторы. */
export function newRequestId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (d) =>
    (Number(d) ^ (Math.random() * 16 >> (Number(d) / 4))).toString(16),
  );
}

/**
 * Сколько экран ждёт ответа. Прокси и сервер приложений рвут ход на 30 с;
 * без своего предела зависшее соединение держало бы «Ayla думает…» и
 * выключенное поле, пока не сдастся браузер. Обрыв безопасен: повтор идёт
 * тем же ключом и второго хода не запускает.
 */
export const ASK_TIMEOUT_MS = 40_000;

export const customerAylaApi = {
  history: () => request<{ messages: AylaChatMessage[] }>("/assistant/history"),
  ask: (text: string, requestId: string) => {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), ASK_TIMEOUT_MS);
    return request<AskResponse>("/assistant/ask", {
      method: "POST",
      body: JSON.stringify({ text, request_id: requestId }),
      signal: abort.signal,
    }).finally(() => clearTimeout(timer));
  },
};
