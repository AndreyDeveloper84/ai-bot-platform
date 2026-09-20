/**
 * Диалог с Ayla — один каркас на мастера и на администратора (DRF-2119).
 *
 * Лента реплик, поле ввода, карточка предложения. Что за ассистент и куда
 * ходить — решает вызывающий экран через `api` (тройка history / ask /
 * confirm): мастерский (`/master/ayla`, `lib/master-api`) и админский
 * (`/admin/ayla`, `lib/admin-api`) экраны — два субъекта одного каркаса,
 * не два каркаса. До DRF-2119 всё это жило внутри `MasterAylaScreen`;
 * вынесено без смены поведения.
 *
 * # Два вида подтверждения
 *
 * Предложение (`pending_action`) — это то, что Ayla собирается сделать, но
 * не сделала (DRF-1180). У мастера оно всегда с талоном: «Подтвердить» →
 * `POST /assistant/confirm`. У администратора есть второй вид —
 * `confirm_kind: "open"`: сервер ничего не делает, кнопка открывает форму
 * Mini App по `open_url` с предзаполнением (черновик записи), и саму
 * запись создаёт человек в форме. Без `onOpen` такой карточке некуда
 * вести — вызывающий экран обязан его дать, если его API умеет `open`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../lib/api";
import { hapticImpact, hapticSelection, signalReady } from "../lib/max-sdk";

/** Тот же потолок, что у сервера (`views_assistant.MAX_QUESTION_CHARS`). */
export const MAX_QUESTION_CHARS = 1000;

const FAILED_TEXT = "Не получилось отправить. Попробуйте ещё раз.";
const DECLINED_TEXT = "Хорошо, ничего не меняю.";

export interface AylaChatMessage {
  id: string;
  /** "user" | "assistant" */
  role: string;
  content: string;
  tool: string;
  created_at: string;
}

export interface AylaChatPendingAction {
  action: string;
  summary: string;
  confirm_label: string;
  token: string;
  expires_in_sec: number;
  /** "token" (умолчание, подтверждение на сервере) | "open" (дверь в Mini App). */
  confirm_kind?: string;
  /** Куда ведёт дверь при `confirm_kind === "open"`. */
  open_url?: string;
}

export interface AylaChatApi {
  history: () => Promise<{ messages: AylaChatMessage[] }>;
  ask: (text: string) => Promise<{
    answer: string;
    pending_action: AylaChatPendingAction | null;
  }>;
  confirm: (token: string) => Promise<{ answer: string }>;
}

export interface AylaChatProps {
  api: AylaChatApi;
  /** Приглашение на пустом экране. */
  greeting: string;
  /** Дверь для `confirm_kind === "open"` — переход внутри Mini App. */
  onOpen?: (url: string) => void;
  /** `aria-label` ленты — какой это диалог. */
  logLabel?: string;
}

/** Черновая реплика, ещё не подтверждённая сервером. */
interface LocalMessage extends AylaChatMessage {
  pending?: boolean;
}

let localSeq = 0;
// DRF-2151 — второй слой поверх фильтра бэкенда: команда или токен
// приглашения на экране не рисуются никогда, даже если история пришла
// со старого бэкенда. Формы — те же, что читает салонный бот.
const HIDDEN_TURN = /^\/|master_invite_[0-9a-fA-F-]{8,}|\binv_[A-Za-z0-9]{4,}\b/;
// Код сотрудника — ровно четыре знака алфавита кодов после AYLA в любом
// регистре (staff_invites.CODE_ALPHABET); «AYLA Beauty» — не код.
const TYPED_CODE = /\bAYLA[-_ ]?[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{4}\b/i;

export function isHiddenTurn(content: string): boolean {
  const text = (content || "").trim();
  return HIDDEN_TURN.test(text) || TYPED_CODE.test(text);
}

export function visibleMessages<T extends { content: string }>(messages: T[]): T[] {
  return messages.filter((m) => !isHiddenTurn(m.content));
}

function localMessage(role: string, content: string): LocalMessage {
  localSeq += 1;
  return {
    id: `local-${localSeq}`,
    role,
    content,
    tool: "",
    created_at: "",
    pending: true,
  };
}

export function AylaChat({ api, greeting, onOpen, logLabel = "Диалог с Ayla" }: AylaChatProps) {
  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<AylaChatPendingAction | null>(null);
  const [error, setError] = useState("");

  const listEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .history()
      .then((res) => {
        if (!alive) return;
        setMessages(visibleMessages(res.messages));
      })
      .catch(() => {
        // История — не условие разговора. Пустой экран с приглашением
        // честнее, чем ошибка на месте диалога, которого ещё нет.
        if (alive) setMessages([]);
      })
      .finally(() => {
        if (!alive) return;
        setLoadingHistory(false);
        signalReady();
      });
    return () => {
      alive = false;
    };
    // `api` — стабильный объект экрана; перезапрашивать историю на каждый
    // рендер незачем.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    // `scrollIntoView` есть не везде (jsdom в тестах, старые webview) —
    // прокрутка к последней реплике удобна, но диалог не должен от неё
    // зависеть.
    const end = listEndRef.current;
    if (end && typeof end.scrollIntoView === "function") {
      end.scrollIntoView({ block: "end" });
    }
  }, [messages.length, pending]);

  const onSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;

    hapticSelection();
    setDraft("");
    setError("");
    // Новый вопрос отменяет висящее предложение: подтверждать сводку,
    // на которую сверху лёг другой разговор, человек не должен.
    setPending(null);
    // DRF-2151: команда/токен на экране не рисуется и до перезахода.
    if (!isHiddenTurn(text)) {
      setMessages((prev) => [...prev, localMessage("user", text)]);
    }
    setSending(true);
    try {
      const res = await api.ask(text);
      // Предложение показывается карточкой, а не пузырём: иначе один и
      // тот же текст стоял бы на экране дважды — сводкой и репликой.
      // На сервере он записан репликой в любом случае, так что после
      // перезахода диалог читается целиком.
      if (res.pending_action === null) {
        setMessages((prev) => [...prev, localMessage("assistant", res.answer)]);
      }
      setPending(res.pending_action);
    } catch (err) {
      const detail = err instanceof ApiError && err.detail ? err.detail : FAILED_TEXT;
      setError(detail);
    } finally {
      setSending(false);
    }
  }, [api, draft, sending]);

  const onConfirm = useCallback(async () => {
    if (pending === null || confirming) return;
    hapticImpact("heavy");
    if (pending.confirm_kind === "open") {
      // Дверь: сервер ничего не делает, форму открывает Mini App, а запись
      // создаст человек уже там — кнопкой формы.
      const url = pending.open_url ?? "";
      setPending(null);
      setMessages((prev) => [...prev, localMessage("assistant", pending.summary)]);
      if (url && onOpen) onOpen(url);
      return;
    }
    setConfirming(true);
    setError("");
    try {
      const res = await api.confirm(pending.token);
      setPending(null);
      setMessages((prev) => [...prev, localMessage("assistant", res.answer)]);
    } catch (err) {
      const detail = err instanceof ApiError && err.detail ? err.detail : FAILED_TEXT;
      setError(detail);
    } finally {
      setConfirming(false);
    }
  }, [api, pending, confirming, onOpen]);

  const onDecline = useCallback(() => {
    hapticSelection();
    setPending(null);
    setMessages((prev) => [...prev, localMessage("assistant", DECLINED_TEXT)]);
  }, []);

  const overLimit = draft.length > MAX_QUESTION_CHARS;

  return (
    <>
      <div className="ayla-list" role="log" aria-label={logLabel}>
        {loadingHistory ? (
          <p className="ayla-empty" aria-live="polite">
            Загружаю диалог…
          </p>
        ) : messages.length === 0 ? (
          <p className="ayla-empty">{greeting}</p>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={
                m.role === "user" ? "ayla-bubble ayla-bubble--mine" : "ayla-bubble ayla-bubble--ayla"
              }
            >
              <p className="ayla-bubble__content">{m.content}</p>
            </div>
          ))
        )}

        {sending ? (
          <p className="ayla-typing" aria-live="polite">
            Ayla думает…
          </p>
        ) : null}

        {pending !== null ? (
          <section className="ayla-confirm" aria-labelledby="ayla-confirm-title">
            <h2 className="ayla-confirm__title" id="ayla-confirm-title">
              {pending.confirm_kind === "open" ? "Черновик готов" : "Подтвердите действие"}
            </h2>
            <p className="ayla-confirm__summary">{pending.summary}</p>
            <div className="ayla-confirm__actions">
              <button
                type="button"
                className="ayla-btn ayla-btn--primary"
                onClick={onConfirm}
                disabled={confirming}
              >
                {confirming ? "Отправляю…" : pending.confirm_label}
              </button>
              <button
                type="button"
                className="ayla-btn ayla-btn--ghost"
                onClick={onDecline}
                disabled={confirming}
              >
                Не надо
              </button>
            </div>
          </section>
        ) : null}

        {error ? (
          <p className="ayla-error" role="alert">
            {error}
          </p>
        ) : null}

        <div ref={listEndRef} />
      </div>

      <form
        className="ayla-compose"
        onSubmit={(e) => {
          e.preventDefault();
          void onSend();
        }}
      >
        <div className="ayla-compose__row">
          <textarea
            className="ayla-compose__textarea"
            aria-label="Вопрос к Ayla"
            placeholder="Спросите Ayla"
            value={draft}
            rows={1}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button
            type="submit"
            className="ayla-compose__send"
            aria-label="Отправить"
            disabled={sending || draft.trim().length === 0 || overLimit}
          >
            ➤
          </button>
        </div>
        {overLimit ? (
          <p className="ayla-compose__counter ayla-compose__counter--over">
            {draft.length} / {MAX_QUESTION_CHARS}
          </p>
        ) : null}
      </form>
    </>
  );
}
