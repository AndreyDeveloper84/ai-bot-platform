/**
 * Раздел «Ayla» — диалог мастера с ассистентом.
 *
 * Адрес: `/master/ayla`
 *
 * Решение владельца, OD-7 от 21.08.2026 (повторено 05.09 в DRF-1180),
 * дословно:
 *
 *     «Раздел «Ayla» — это диалог мастера с Ayla, тот же, что в боте,
 *      но через Mini App. Не список клиентских переписок.»
 *
 * Отсюда две вещи, которые легко сделать иначе и получить другой экран:
 *
 * 1. История приходит с сервера
 *    (`GET /api/v1/master/assistant/history`), а не копится в
 *    состоянии вкладки. Нить одна на бот и приложение: спросил по
 *    дороге в MAX — дочитал в приложении.
 * 2. Собеседник один — Ayla. Ни списка клиентов, ни выбора адресата:
 *    мастер не переписывается с клиентом (DRF-1039), и эта поверхность
 *    такой возможности не открывает.
 *
 * # Действие, меняющее данные, не выполняется молча
 *
 * Эпик DRF-1180: «сначала она должна показать, что именно собирается
 * сделать, получить подтверждение пользователя и только после этого
 * выполнять действие». Ответ сервера может нести `pending_action` —
 * это ПРЕДЛОЖЕНИЕ. Экран рисует карточку со сводкой и двумя кнопками;
 * пока «Подтвердить» не нажата, `POST /assistant/confirm` не
 * вызывается вовсе. Аргументы лежат внутри подписанного талона, не в
 * состоянии экрана: показать одно, а отправить другое здесь нечем.
 *
 * # Нижняя навигация не тронута
 *
 * `MasterTabBar` сегодня четырёхразделная. Перевод 4 → 3
 * (`Сегодня | Расписание | Ayla`) идёт вместе с удалением экранов
 * переписки — это DRF-1255, и он упирается в неотвеченные вопросы
 * владельца. Пока экран живёт на своём адресе, а вход в него — карточка
 * на «Сегодня». Убрать вкладку раньше, чем появится экран, значило бы
 * увести человека в пустоту.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { MasterTabBar } from "../components/MasterTabBar";
import { useScreenBack } from "../hooks/useScreenBack";
import { ApiError } from "../lib/api";
import {
  askAyla,
  confirmAylaAction,
  getAylaHistory,
  type AylaMessage,
  type AylaPendingAction,
} from "../lib/master-api";
import { hapticImpact, hapticSelection, signalReady } from "../lib/max-sdk";
import { backTo } from "../lib/screen-back";

/** Тот же потолок, что у сервера (`views_assistant.MAX_QUESTION_CHARS`). */
const MAX_QUESTION_CHARS = 1000;

const GREETING =
  "Спросите про день, загрузку или свободные окна — отвечу по вашему расписанию.";

const FAILED_TEXT = "Не получилось отправить. Попробуйте ещё раз.";

/** Черновая реплика, ещё не подтверждённая сервером. */
interface LocalMessage extends AylaMessage {
  pending?: boolean;
}

let localSeq = 0;
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

export function MasterAylaScreen() {
  useScreenBack(backTo("/master/dashboard"));

  const [messages, setMessages] = useState<LocalMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState<AylaPendingAction | null>(null);
  const [error, setError] = useState("");

  const listEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    getAylaHistory()
      .then((res) => {
        if (!alive) return;
        setMessages(res.messages);
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
    setMessages((prev) => [...prev, localMessage("user", text)]);
    setSending(true);
    try {
      const res = await askAyla(text);
      // Предложение показывается карточкой, а не пузырём: иначе один и
      // тот же текст стоял бы на экране дважды — сводкой и репликой.
      // На сервере он записан репликой в любом случае, так что после
      // перезахода диалог читается целиком.
      if (res.pending_action === null) {
        setMessages((prev) => [...prev, localMessage("assistant", res.answer)]);
      }
      setPending(res.pending_action);
    } catch (err) {
      const detail =
        err instanceof ApiError && err.detail ? err.detail : FAILED_TEXT;
      setError(detail);
    } finally {
      setSending(false);
    }
  }, [draft, sending]);

  const onConfirm = useCallback(async () => {
    if (pending === null || confirming) return;
    hapticImpact("heavy");
    setConfirming(true);
    setError("");
    try {
      const res = await confirmAylaAction(pending.token);
      setPending(null);
      setMessages((prev) => [...prev, localMessage("assistant", res.answer)]);
    } catch (err) {
      const detail =
        err instanceof ApiError && err.detail ? err.detail : FAILED_TEXT;
      setError(detail);
    } finally {
      setConfirming(false);
    }
  }, [pending, confirming]);

  const onDecline = useCallback(() => {
    hapticSelection();
    setPending(null);
    setMessages((prev) => [
      ...prev,
      localMessage("assistant", "Хорошо, ничего не меняю."),
    ]);
  }, []);

  const overLimit = draft.length > MAX_QUESTION_CHARS;

  return (
    <main className="screen ayla-screen">
      <header className="ayla-header">
        <h1 className="ayla-header__title">Ayla</h1>
        <p className="ayla-header__sub">Помощник по вашему расписанию</p>
      </header>

      <div className="ayla-list" role="log" aria-label="Диалог с Ayla">
        {loadingHistory ? (
          <p className="ayla-empty" aria-live="polite">
            Загружаю диалог…
          </p>
        ) : messages.length === 0 ? (
          <p className="ayla-empty">{GREETING}</p>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              className={
                m.role === "user"
                  ? "ayla-bubble ayla-bubble--mine"
                  : "ayla-bubble ayla-bubble--ayla"
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
          <section
            className="ayla-confirm"
            aria-labelledby="ayla-confirm-title"
          >
            <h2 className="ayla-confirm__title" id="ayla-confirm-title">
              Подтвердите действие
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

      {/* Значки вкладок этот экран не считает: они приходят с
          дашборда (`tab_badges`), и запрашивать дашборд ради трёх
          чисел на экране диалога — лишний круг к серверу на каждом
          открытии. Панель здесь нужна как навигация, не как сводка. */}
      <MasterTabBar
        unreadCount={0}
        scheduleHasPendingChange={false}
        profileHasOwnerPendingChange={false}
      />
    </main>
  );
}
