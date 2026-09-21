/**
 * «Продолжить разговор с Ayla» — последняя тема для Главной (DRF-2144).
 *
 *   GET /api/v1/customer/last-topic/ → { last_topic: { text, at } | null }
 *
 * Фриз H01 25.08 п.4: превью показывает только реальный последний контекст,
 * при его отсутствии экран говорит нейтрально «Продолжить разговор». Сервер
 * отдаёт первые 80 знаков последнего хода Ayla (то, что человек уже читал в
 * чате), без safety-строк и служебных строк памяти; здесь ничего не
 * сокращается и не пересказывается — строка рисуется дословно.
 *
 * `null` — темы нет; это ответ, а не сбой. Сбой ручки экран тоже читает как
 * «темы нет»: блок нейтральный, Главная от него не зависит.
 */
import { request } from "./api";

export interface LastTopic {
  /** Первые знаки последнего ответа Ayla, обрезка по слову с «…». */
  text: string;
  /** ISO-время того хода. */
  at: string;
}

/** DRF-2266 — тема и ссылка на диалог бота, из которого открыт Mini App. */
export interface LastTopicAndChat {
  topic: LastTopic | null;
  /** Публичная ссылка на диалог бота (`MAX_BOT_<S>_LINK`) или `null`. */
  chatLink: string | null;
}

export async function getLastTopicAndChatLink(): Promise<LastTopicAndChat> {
  const res = await request<{ last_topic: LastTopic | null; chat_link?: string | null }>(
    "/last-topic/",
  );
  const topic = res.last_topic;
  const clean = !topic || typeof topic.text !== "string" || !topic.text.trim() ? null : topic;
  const link = typeof res.chat_link === "string" && res.chat_link.trim() ? res.chat_link : null;
  return { topic: clean, chatLink: link };
}

export async function getLastTopic(): Promise<LastTopic | null> {
  return (await getLastTopicAndChatLink()).topic;
}

/** «20 сент., 11:30» — когда был этот ход; пустая строка, если дата нечитаема. */
export function formatTopicWhen(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const sameYear = d.getFullYear() === now.getFullYear();
  const day = d.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    ...(sameYear ? {} : { year: "numeric" }),
  });
  const time = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  return `${day}, ${time}`;
}
